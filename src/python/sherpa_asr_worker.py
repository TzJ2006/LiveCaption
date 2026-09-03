#!/usr/bin/env python3
"""Sherpa-ONNX ASR worker using NDJSON over standard input and output.

Handles the three model layouts sherpa-onnx ships for zh-en, picked by looking at which files the
model directory actually contains rather than by a flag:

  joiner.*     -> streaming transducer  (X-ASR zh-en, Nemotron 3.5, bilingual zipformer)
  model.*      -> offline SenseVoice    (chunked: buffers --chunk-seconds, every caption final)
  otherwise    -> streaming paraformer  (the original bilingual zh-en model)

# ponytail: file presence, not a --model-type flag. Every one of these tarballs is unambiguous
# about which kind it is, so asking the user to repeat it is a flag that can only ever be wrong.
"""

import argparse
import base64
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".build" / "pydeps"))

import numpy as np
import sherpa_onnx

TARGET_RATE = 16000


def resample(samples: np.ndarray, src_rate: int, dst_rate: int = TARGET_RATE) -> np.ndarray:
    if src_rate == dst_rate or len(samples) == 0:
        return samples.astype(np.float32)
    count = max(1, int(len(samples) * dst_rate / src_rate))
    old = np.linspace(0, len(samples), num=len(samples), endpoint=False)
    new = np.linspace(0, len(samples), num=count, endpoint=False)
    return np.interp(new, old, samples).astype(np.float32)


def emit(source: str, text: str, final: bool) -> None:
    text = text.strip()
    if text:
        print(json.dumps({"source": source, "text": text, "final": final}, ensure_ascii=False),
              flush=True)


def pick(model_dir: pathlib.Path, stem: str) -> str:
    """Prefer the int8 export, fall back to fp32.

    Not cosmetic: the X-ASR int8 package ships decoder.onnx in fp32 because the int8 decoder is
    deleted during export, so a hardcoded .int8.onnx name finds an encoder and no decoder.

    Globbed rather than exact because the older icefall exports keep their training suffix
    (encoder-epoch-99-avg-1.int8.onnx) while the 2026 ones do not (encoder.int8.onnx).
    """
    for pattern in (f"{stem}*.int8.onnx", f"{stem}*.onnx"):
        matches = sorted(model_dir.glob(pattern))
        if matches:
            return str(matches[0])
    return ""


def build(model_dir: pathlib.Path, args):
    """(recognizer, streaming?) for whichever model lives in model_dir."""
    tokens = str(model_dir / "tokens.txt")
    joiner = pick(model_dir, "joiner")
    if joiner:
        kwargs = dict(tokens=tokens, encoder=pick(model_dir, "encoder"),
                      decoder=pick(model_dir, "decoder"), joiner=joiner,
                      num_threads=args.num_threads, sample_rate=TARGET_RATE, feature_dim=80,
                      enable_endpoint_detection=True)
        if args.hotwords_file:
            # biasing needs beam search; greedy has nowhere to apply the boost
            kwargs.update(decoding_method="modified_beam_search",
                          hotwords_file=args.hotwords_file, hotwords_score=args.hotwords_score)
            if args.bpe_vocab:
                kwargs.update(modeling_unit=args.modeling_unit, bpe_vocab=args.bpe_vocab)
        return sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs), True

    model = pick(model_dir, "model")
    if model:
        # ponytail: SenseVoice and FireRedASR2-CTC ship the identical model.int8.onnx + tokens.txt
        # layout, so the directory name is the only thing that tells them apart without opening the
        # graph. from_fire_red_asr (AED, encoder+decoder) is a different constructor from
        # from_fire_red_asr_ctc (this export, encoder+CTC branch only, one file) -- sherpa-onnx's
        # own examples pick by name too, and this repack is CTC-only per its own README.
        if "fire-red" in model_dir.name.lower():
            return sherpa_onnx.OfflineRecognizer.from_fire_red_asr_ctc(
                model=model, tokens=tokens, num_threads=args.num_threads), False
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model, tokens=tokens, num_threads=args.num_threads, use_itn=True), False

    return sherpa_onnx.OnlineRecognizer.from_paraformer(
        encoder=pick(model_dir, "encoder"), decoder=pick(model_dir, "decoder"), tokens=tokens,
        num_threads=args.num_threads, sample_rate=TARGET_RATE,
        enable_endpoint_detection=True), True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir",
                        default=str(ROOT / "models/sherpa-onnx-streaming-paraformer-bilingual-zh-en"))
    parser.add_argument("--num-threads", type=int, default=2)
    parser.add_argument("--chunk-seconds", type=float, default=3.0,
                        help="audio per call for offline models; streaming models ignore it")
    parser.add_argument("--hotwords-file", default="", help="one phrase per line; transducers only")
    parser.add_argument("--hotwords-score", type=float, default=2.0)
    parser.add_argument("--modeling-unit", default="cjkchar+bpe")
    parser.add_argument("--bpe-vocab", default="", help="needed with --hotwords-file on a bpe model")
    args = parser.parse_args()

    model_dir = pathlib.Path(args.model_dir).expanduser()
    if not model_dir.is_dir():
        print(f"no such model directory: {model_dir}", file=sys.stderr)
        return 1
    recognizer, streaming = build(model_dir, args)
    print(json.dumps({"status": "ready",
                      "device": f"sherpa-onnx cpu ({'streaming' if streaming else 'chunked'})"}),
          flush=True)

    streams, last, buffers = {}, {}, {}

    def decode_offline(source, final):
        """One self-contained clip through the offline recognizer."""
        audio = buffers.get(source)
        if audio is None or audio.size == 0:
            return
        buffers[source] = np.zeros(0, dtype=np.float32)
        stream = recognizer.create_stream()
        stream.accept_waveform(TARGET_RATE, audio)
        recognizer.decode_stream(stream)
        emit(source, str(stream.result.text), final)

    for line in sys.stdin:
        try:
            obj = json.loads(line)
            source = obj["source"]
            rate = int(obj["sampleRate"])
            samples = resample(np.frombuffer(base64.b64decode(obj["pcmFloat32"]), dtype=np.float32),
                               rate)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr, flush=True)
            continue

        if not streaming:
            buffers[source] = np.concatenate([buffers.get(source, np.zeros(0, dtype=np.float32)),
                                              samples])
            if buffers[source].size / TARGET_RATE >= args.chunk_seconds:
                decode_offline(source, True)
            continue

        stream = streams.setdefault(source, recognizer.create_stream())
        stream.accept_waveform(TARGET_RATE, samples)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        text = str(recognizer.get_result(stream)).strip()
        if text and text != last.get(source):
            emit(source, text, False)
            last[source] = text
        if recognizer.is_endpoint(stream):
            emit(source, text, True)
            recognizer.reset(stream)
            last[source] = ""

    # stdin closed: drain the tail the same way transcribe_sherpa.py does, otherwise whatever was
    # being said when the host quit never reaches the caption or the transcript
    for source in list(buffers):
        decode_offline(source, True)
    for source, stream in streams.items():
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        emit(source, str(recognizer.get_result(stream)).strip(), True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
