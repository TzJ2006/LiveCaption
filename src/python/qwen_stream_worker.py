#!/usr/bin/env python3
"""True-streaming Qwen3-ASR worker via the vLLM backend. Linux/WSL only.

Same NDJSON protocol as the other workers. Partial text streams out as
final=false; every --finalize-seconds the stream is flushed as final=true
so transcripts get written.

Install inside WSL:  pip install -U "qwen-asr[vllm]"
"""

import argparse
import base64
import json
import sys

import numpy as np


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--hf-model", default="Qwen/Qwen3-ASR-0.6B")
    p.add_argument("--push-seconds", type=float, default=0.5)
    p.add_argument("--finalize-seconds", type=float, default=30.0)
    return p.parse_args()


def resample16k(audio, rate):
    if rate == 16000:
        return audio
    n = int(len(audio) * 16000 / rate)
    # ponytail: linear interp is fine for speech; polyphase if quality ever matters
    return np.interp(np.linspace(0, len(audio), n, endpoint=False),
                     np.arange(len(audio)), audio).astype(np.float32)


def main():
    args = parse_args()
    try:
        from qwen_asr import Qwen3ASRModel
    except ImportError:
        print('Inside WSL run: pip install -U "qwen-asr[vllm]"', file=sys.stderr)
        return 1

    asr = Qwen3ASRModel.LLM(model=args.hf_model, gpu_memory_utilization=0.8, max_new_tokens=32)
    print(json.dumps({"status": "ready", "device": "wsl-vllm"}), flush=True)

    states, buffers, pushed = {}, {}, {}

    def emit(source, text, final):
        text = (text or "").strip()
        if text:
            print(json.dumps({"source": source, "text": text, "final": final}, ensure_ascii=False), flush=True)

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

        buffers.setdefault(source, []).append(resample16k(audio, rate))
        total = sum(len(c) for c in buffers[source])
        if total < args.push_seconds * 16000:
            continue
        seg = np.concatenate(buffers[source])
        buffers[source].clear()

        state = states.get(source)
        if state is None:
            state = states[source] = asr.init_streaming_state(
                unfixed_chunk_num=2, unfixed_token_num=5, chunk_size_sec=2.0)
            pushed[source] = 0.0
        asr.streaming_transcribe(seg, state)
        pushed[source] += len(seg) / 16000

        if pushed[source] >= args.finalize_seconds:
            asr.finish_streaming_transcribe(state)
            emit(source, state.text, True)
            del states[source]
        else:
            emit(source, state.text, False)

    for source, state in states.items():
        asr.finish_streaming_transcribe(state)
        emit(source, state.text, True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
