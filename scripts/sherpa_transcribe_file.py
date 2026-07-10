#!/usr/bin/env python3
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".build" / "pydeps"))

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly
import sherpa_onnx


def load_audio(path: str) -> tuple[int, np.ndarray]:
    rate, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    else:
        data = data.astype(np.float32)
    if rate != 16000:
        data = resample_poly(data, 16000, rate).astype(np.float32)
        rate = 16000
    return rate, data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
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

    for file in args.files:
        rate, audio = load_audio(file)
        stream = recognizer.create_stream()
        pieces = []
        for start in range(0, len(audio), 1600):
            stream.accept_waveform(rate, audio[start:start + 1600])
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            if recognizer.is_endpoint(stream):
                text = str(recognizer.get_result(stream)).strip()
                if text:
                    pieces.append(text)
                recognizer.reset(stream)
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        text = str(recognizer.get_result(stream)).strip()
        if text:
            pieces.append(text)
        print(f"== {file} ==")
        print(" ".join(pieces) or "(empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
