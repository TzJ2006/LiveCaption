#!/usr/bin/env python3
"""Streaming Hugging Face ASR worker using NDJSON over standard input and output.

The counterpart of hf_asr_worker.py, which is the offline path: that one buffers whole seconds
of audio and pushes each block through pipeline(), so a caption only appears once its block is
finished. This one keeps a single generate() alive per source and feeds it the fixed-size chunks
the checkpoint was trained on, reusing the encoder cache between them, so text arrives while the
sentence is still being spoken.

Only cache-aware streaming checkpoints fit here -- e.g. nvidia/nemotron-3.5-asr-streaming-0.6b
on transformers >= 5.13. Anything else belongs on --asr hf.

Input:
  {"type":"audio","source":"mic","sampleRate":16000,"pcmFloat32":"..."}
Output:
  {"source":"mic","text":"hello","final":false}
"""

import argparse
import base64
import copy
import json
import os
import pathlib
import queue
import re
import sys
import threading

# ponytail: same redirect as hf_asr_worker.py -- keep weights inside LiveCaption, and set it
# before transformers/huggingface_hub import, since they read HF_HOME once at import time.
HF_CACHE = pathlib.Path(__file__).resolve().parents[2] / "models" / "hf"
if not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(HF_CACHE)
    HF_CACHE.mkdir(parents=True, exist_ok=True)

import numpy as np

SAMPLE_RATE = 16000
# a cache-aware RNNT punctuates as it decodes, so sentence ends are the natural commit points
TERMINAL = "。．.!?！？"
# --language auto makes the checkpoint name the language it heard, as a special token right after
# the sentence's terminal punctuation ("Hello.<en-US>"). That is per utterance, so a bilingual
# meeting labels itself line by line. The rest of the special tokens are noise in a transcript.
LANGUAGE_TAG = re.compile(r"<([a-z]{2}-[A-Za-z]{2})>")
OTHER_SPECIAL = re.compile(r"<(?:unk|pad|blank)>")
# ponytail: the streamer yields an empty string on every decoding step that produced no token, so
# silence arrives as a stream of blanks rather than as a pause -- these two are measured in decoded
# audio, never in wall clock, and never by waiting on the streamer itself. On a slow device the
# model falls behind and a wall clock would cut sentences in half instead of noticing a silence.
# Must clear the model's lookahead: it needs audio past a word before it will decode it, so a
# shorter wait commits the line while the last word or two is still in flight and splits it off.
SILENCE_SECONDS = 2.5  # quiet audio that commits the pending line: someone stopped talking
TAG_WAIT = 0.4         # quiet audio a punctuated line waits for the tag naming its language
COMMIT_WAIT = 0.35     # wall clock before the streamer hands control back, ~one decoding step

_stdout_lock = threading.Lock()  # one thread per source, one stdout


def clean(raw):
    """Drop the tokenizer's own special tokens; only the language tag means anything here."""
    return OTHER_SPECIAL.sub("", raw)


def partition_language(raw):
    """Split at the first language tag: (text before it, locale, undecoded text after it).

    The tag lands after the terminal punctuation of the sentence it describes, so it marks where
    one utterance ends and the next begins -- everything before it is a finished caption.
    """
    match = LANGUAGE_TAG.search(raw)
    if match is None:
        return clean(raw), "", ""
    return clean(raw[:match.start()]), match.group(1), raw[match.end():]


def emit(source, text, final, language=""):
    text = (text or "").strip()
    # a line of nothing but punctuation is the tail of one the silence rule already committed
    if not text or all(char in TERMINAL + "，,、;； " for char in text):
        return
    event = {"source": source, "text": text, "final": final}
    if language:  # absent rather than empty, so hosts and older workers agree on "not detected"
        event["language"] = language
    with _stdout_lock:
        print(json.dumps(event, ensure_ascii=False), flush=True)


def pick_device(preference):
    """CUDA, then the Apple Silicon GPU, then the CPU -- unless something pins one.

    LIVECAPTION_DEVICE wins over --device: the hosts pass the environment straight through to the
    worker, so it is the one way to force cpu from a start.sh command line if MPS ever misbehaves.
    """
    try:
        import torch
    except ImportError:
        return "cpu"
    preference = os.environ.get("LIVECAPTION_DEVICE") or preference
    if preference != "auto":
        return preference
    if torch.cuda.is_available():
        return "cuda:0"
    # ponytail: MPS is what lets this run live on a Mac. A 0.6B cache-aware RNNT on CPU decodes
    # several times slower than realtime, and every second of that pushes the caption further
    # behind the speaker until the backlog is the whole meeting.
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def resample16k(audio, rate):
    if rate == SAMPLE_RATE or audio.size == 0:
        return audio.astype(np.float32)
    count = max(1, int(len(audio) * SAMPLE_RATE / rate))
    return np.interp(np.linspace(0, len(audio), count, endpoint=False),
                     np.arange(len(audio)), audio).astype(np.float32)


class SourceStream:
    """One long-lived generate() call for one source, fed chunk by chunk from stdin.

    generate() pulls audio through a generator, so the pulling has to happen off the stdin loop:
    run() owns a thread that blocks on the queue while the reader keeps accepting frames.
    """

    def __init__(self, source, model, processor, streamer_class, language, finalize_seconds):
        # ponytail: generate() patches get_audio_features onto the model instance and deletes it
        # on the way out, so two sources sharing one instance rip the attribute out from under
        # each other. A shallow copy gives this source its own __dict__; the weights stay shared,
        # so the second source costs no extra memory.
        self.source, self.model, self.processor = source, copy.copy(model), processor
        self.streamer_class, self.language = streamer_class, language
        self.finalize_seconds = finalize_seconds
        self.frames = queue.Queue()
        self.buffer = np.zeros(0, dtype=np.float32)
        self.buffer_start = 0  # absolute index, in samples, of buffer[0] within the whole stream
        # ponytail: the pending line is aged in audio seconds, not wall clock -- on a CPU the
        # model runs slower than realtime, and a wall clock would cut sentences in half there
        self.seconds = 0.0
        self.thread = threading.Thread(target=self.run, daemon=True)

    # --- audio plumbing ---

    def push(self, audio):
        self.frames.put(audio)

    def close(self):
        self.frames.put(None)

    def take(self, start, end):
        """Samples [start, end) of the stream, blocking for them; None once stdin is done."""
        while self.buffer_start + self.buffer.size < end:
            block = self.frames.get()
            if block is None:
                return None
            self.buffer = np.concatenate([self.buffer, block])
        drop = start - self.buffer_start
        if drop > 0:  # everything before this chunk is decoded and cached, so let it go
            self.buffer = self.buffer[drop:]
            self.buffer_start = start
        return self.buffer[start - self.buffer_start:end - self.buffer_start]

    def chunk_inputs(self, audio, first):
        inputs = self.processor(audio, sampling_rate=SAMPLE_RATE, is_streaming=True,
                                is_first_audio_chunk=first, language=self.language,
                                return_tensors="pt")
        return inputs.to(self.model.device, dtype=self.model.dtype)

    def features(self, first_features):
        """The chunk generator generate() pulls from; blocks on the queue between chunks.

        The index arithmetic is the model card's streaming recipe: chunk k starts where the k-th
        mel frame's window opens, so chunks butt up against each other in mel frames rather than
        in samples and the encoder never sees the same frame twice.
        """
        yield first_features[:, :self.processor.num_mel_frames_first_audio_chunk, :]

        mel_frame = self.processor.num_mel_frames_first_audio_chunk
        hop = self.processor.feature_extractor.hop_length
        n_fft = self.processor.feature_extractor.n_fft
        while True:
            start = mel_frame * hop - n_fft // 2
            audio = self.take(start, start + self.processor.num_samples_per_audio_chunk)
            if audio is None:
                return
            self.seconds = mel_frame * hop / SAMPLE_RATE
            yield self.chunk_inputs(audio, first=False).input_features
            mel_frame += self.processor.num_mel_frames_per_audio_chunk

    # --- recognition ---

    def start(self):
        self.thread.start()

    def join(self):
        self.thread.join()

    def run(self):
        audio = self.take(0, self.processor.num_samples_first_audio_chunk)
        if audio is None:  # stdin closed before a full first chunk arrived
            return
        first = self.chunk_inputs(audio, first=True)
        # ponytail: special tokens are kept so the <xx-XX> language tag survives; clean() strips
        # them back out. Skipping them here would throw the detection away inside the tokenizer.
        # The timeout is what lets consume() notice a silence instead of blocking through it.
        streamer = self.streamer_class(self.processor.tokenizer, skip_special_tokens=False,
                                       timeout=COMMIT_WAIT)
        kwargs = {**first, "input_features": self.features(first.input_features), "streamer": streamer}
        worker = threading.Thread(target=self.generate, args=(kwargs,), daemon=True)
        worker.start()
        self.consume(streamer)
        worker.join()

    def generate(self, kwargs):
        try:
            self.model.generate(**kwargs)
        except Exception as exc:
            print(f"generate error [{self.source}]: {exc}", file=sys.stderr, flush=True)
            kwargs["streamer"].end()  # generate() never got there; consume() would wait forever

    def consume(self, streamer):
        """Grow one caption line out of the decoded pieces, committing it at sentence ends.

        Three things end a line, and the order matters:

        * punctuation, the common case. A punctuated line is *held* for TAG_WAIT so the language
          tag, which lands a step later, can be attached to the line it describes.
        * the tag itself, for the sentence the model punctuated inside the same step.
        * SILENCE_SECONDS of decoded audio with no new token -- nobody is talking. Without this a
          half-finished sentence hangs on screen until the *next* speaker reaches a full stop,
          which is what a microphone/speaker handover looks like from in here.

        Waiting on the tag alone (an earlier version of this) stalls everything: the model
        punctuates far more often than it tags, and while nobody speaks it does neither.
        """
        line, opened, sent = "", self.seconds, ""
        held = None           # a punctuated line waiting TAG_WAIT for the tag naming its language
        spoke = self.seconds  # audio position of the last step that actually decoded a token
        pieces = iter(streamer)
        while True:
            try:
                piece = next(pieces)
            except StopIteration:
                break
            except queue.Empty:
                piece = ""  # generate() itself stalled; the same as a step that decoded nothing
            line += piece
            if piece.strip():
                spoke = self.seconds
            quiet = self.seconds - spoke

            if held is not None:
                _, tag, rest = partition_language(line)
                if tag:  # the tag names the line that just ended; what follows opens the next one
                    emit(self.source, held, True, tag)
                    held, line, opened, sent = None, rest, self.seconds, ""
                elif clean(line).strip() or quiet >= TAG_WAIT:
                    emit(self.source, held, True)  # next line started, or no tag is coming
                    held, opened, sent = None, self.seconds, ""
                else:
                    continue  # still within one step of the punctuation; give the tag its chance

            while True:  # the model can punctuate and tag inside a single step
                head, tag, rest = partition_language(line)
                if not tag:
                    break
                emit(self.source, head, True, tag)
                line, opened, sent = rest, self.seconds, ""

            text = clean(line).strip()
            if not text:
                continue
            if self.seconds - opened >= self.finalize_seconds:
                emit(self.source, text, True)  # the model never punctuated; commit on age
                line, opened, sent = "", self.seconds, ""
            elif text[-1] in TERMINAL:
                held, line, sent = text, "", ""
            elif quiet >= SILENCE_SECONDS:
                # nobody is talking: commit rather than hold the line open until the next speaker
                emit(self.source, text, True)
                line, opened, sent = "", self.seconds, ""
            elif text != sent:
                emit(self.source, text, False)
                sent = text  # this step decoded no new token; redrawing the same caption is noise
        if held is not None:
            emit(self.source, held, True)
        emit(self.source, clean(line), True)  # flush what was still partial when the audio stopped


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hf-model", required=True)
    parser.add_argument("--language", default="auto",
                        help="language prompt for the checkpoint (e.g. zh-CN, en-US), or auto to detect")
    parser.add_argument("--lookahead-tokens", type=int, default=None,
                        help="right context per chunk; omit to keep the checkpoint default "
                             "(more tokens = better accuracy, more latency)")
    parser.add_argument("--finalize-seconds", type=float, default=20.0,
                        help="commit a line this old even if the model never punctuated it")
    parser.add_argument("--device", default="auto",
                        help="auto picks cuda, then mps (Apple Silicon), then cpu; pass one to pin it")
    return parser.parse_args()


def main():
    args = parse_args()

    device = pick_device(args.device)
    print(f"ASR device: {device}" + (" (no GPU in reach — expect captions to lag)"
                                     if device == "cpu" else ""), file=sys.stderr)
    print(f"HF cache: {os.environ['HF_HOME']}", file=sys.stderr)

    try:
        from transformers import AutoModelForRNNT, AutoProcessor, TextIteratorStreamer
    except ImportError:
        print('Streaming Hugging Face models need transformers >= 5.13:'
              ' pip install -U "transformers>=5.13"', file=sys.stderr)
        return 1

    processor = AutoProcessor.from_pretrained(args.hf_model)
    # ponytail: the chunk geometry below only exists on cache-aware checkpoints. Refuse the rest
    # here rather than half-work: the offline path already handles them properly.
    if not hasattr(processor, "num_samples_per_audio_chunk"):
        print(f"{args.hf_model} is not a cache-aware streaming checkpoint —"
              " run it on the offline path (--asr hf) instead", file=sys.stderr)
        return 1
    if args.lookahead_tokens is not None:
        processor.set_num_lookahead_tokens(args.lookahead_tokens)

    language = args.language
    prompts = getattr(processor, "prompt_dictionary", None) or {}
    if prompts and language not in prompts:
        print(f"{language} is not one of this checkpoint's language prompts, using auto",
              file=sys.stderr)
        language = "auto"

    model = AutoModelForRNNT.from_pretrained(args.hf_model).to(device).eval()

    latency = getattr(processor, "streaming_latency_ms", None)
    detail = device if latency is None else f"{device}, {round(latency)} ms"
    print(json.dumps({"status": "ready", "device": detail}), flush=True)

    streams = {}
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if event.get("type") != "audio":
                continue
            source = event["source"]
            rate = int(event["sampleRate"])
            audio = np.frombuffer(base64.b64decode(event["pcmFloat32"]), dtype=np.float32)
        except Exception as exc:
            print(f"bad input: {exc}", file=sys.stderr)
            continue
        if audio.size == 0:
            continue

        stream = streams.get(source)
        if stream is None:
            stream = streams[source] = SourceStream(source, model, processor, TextIteratorStreamer,
                                                    language, args.finalize_seconds)
            stream.start()
        stream.push(resample16k(audio, rate))

    for stream in streams.values():
        stream.close()
    for stream in streams.values():
        stream.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
