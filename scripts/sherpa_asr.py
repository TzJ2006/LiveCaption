#!/usr/bin/env python3
import argparse
import base64
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".build" / "pydeps"))

import numpy as np
import sherpa_onnx


def resample(samples: np.ndarray, src_rate: int, dst_rate: int = 16000) -> np.ndarray:
    if src_rate == dst_rate or len(samples) == 0:
        return samples.astype(np.float32)
    count = max(1, int(len(samples) * dst_rate / src_rate))
    old = np.linspace(0, len(samples), num=len(samples), endpoint=False)
    new = np.linspace(0, len(samples), num=count, endpoint=False)
    return np.interp(new, old, samples).astype(np.float32)


def emit(source: str, text: str, final: bool) -> None:
    text = text.strip()
    if text:
        print(json.dumps({"source": source, "text": text, "final": final}, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", default=str(ROOT / "models/sherpa-onnx-streaming-paraformer-bilingual-zh-en"))
    args = parser.parse_args()

    model = pathlib.Path(args.model_dir).expanduser()
    recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
        encoder=str(model / "encoder.int8.onnx"),
        decoder=str(model / "decoder.int8.onnx"),
        tokens=str(model / "tokens.txt"),
        num_threads=2,
        sample_rate=16000,
        enable_endpoint_detection=True,
    )
    streams = {}
    last = {}

    for line in sys.stdin:
        try:
            obj = json.loads(line)
            source = obj["source"]
            rate = int(obj["sampleRate"])
            raw = base64.b64decode(obj["pcmFloat32"])
            samples = np.frombuffer(raw, dtype=np.float32)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr, flush=True)
            continue

        stream = streams.setdefault(source, recognizer.create_stream())
        stream.accept_waveform(16000, resample(samples, rate))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
