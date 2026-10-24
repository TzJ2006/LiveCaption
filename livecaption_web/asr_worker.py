"""Streaming sherpa-onnx worker. Offline SenseVoice and FireRed models are refused.

Run as ``python -m modules.caption.asr_worker --model-dir <path>``. The parent process
does not import sherpa. CPU only; this process does not take a GPU lease.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

TARGET_RATE = 16000


class OfflineModelError(RuntimeError):
    pass


def streaming_layout(model_dir: Path) -> str:
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise FileNotFoundError(f"no such model directory: {model_dir}")
    folded = model_dir.name.lower().replace("_", "-")
    if any(token in folded for token in ("fire-red", "firered", "sensevoice", "sense-voice", "whisper")):
        raise OfflineModelError("offline SenseVoice, FireRed, and Whisper models are not used")
    if any(model_dir.glob("joiner*.onnx")):
        return "transducer"
    if any(model_dir.glob("model*.onnx")):
        raise OfflineModelError("offline SenseVoice and FireRed models are not used")
    if any(model_dir.glob("encoder*.onnx")) and any(model_dir.glob("decoder*.onnx")):
        return "paraformer"
    raise FileNotFoundError(f"{model_dir} is not a streaming transducer or paraformer model")


def pick(model_dir: Path, stem: str) -> str:
    for pattern in (f"{stem}*.int8.onnx", f"{stem}*.onnx"):
        matches = sorted(model_dir.glob(pattern))
        if matches:
            return str(matches[0])
    return ""


def prepare_runtime() -> Path | None:
    """Prefer the ONNX Runtime shipped with sherpa-onnx 1.13.8 over an older System32 copy."""
    from core.instance import install_root
    root = install_root() / "models" / "onnxruntime"
    if not root.is_dir():
        return None
    libraries = sorted(root.rglob("onnxruntime.dll"))
    if not libraries:
        return None
    folder = libraries[0].parent
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(folder))
    os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
    return folder


def build_recognizer(model_dir: Path, num_threads: int):
    prepare_runtime()
    import sherpa_onnx

    layout = streaming_layout(model_dir)
    tokens = model_dir / "tokens.txt"
    if not tokens.is_file():
        raise FileNotFoundError(tokens)
    if layout == "transducer":
        recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(tokens),
            encoder=pick(model_dir, "encoder"),
            decoder=pick(model_dir, "decoder"),
            joiner=pick(model_dir, "joiner"),
            num_threads=num_threads,
            sample_rate=TARGET_RATE,
            feature_dim=80,
            enable_endpoint_detection=True,
            provider="cpu",
        )
    else:
        recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
            tokens=str(tokens),
            encoder=pick(model_dir, "encoder"),
            decoder=pick(model_dir, "decoder"),
            num_threads=num_threads,
            sample_rate=TARGET_RATE,
            enable_endpoint_detection=True,
            provider="cpu",
        )
    return recognizer


def feed(recognizer, streams: dict, last: dict, source: str, samples, utterances: dict | None = None) -> list[dict]:
    """Push one chunk through an OnlineRecognizer-shaped object. Partials and finals are separate.

    A final carries an utterance number for that source. The same words said again get a new number.
    """
    if utterances is None:
        utterances = {}
    stream = streams.setdefault(source, recognizer.create_stream())
    stream.accept_waveform(TARGET_RATE, samples)
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    text = str(recognizer.get_result(stream)).strip()
    events = []
    if text and text != last.get(source):
        events.append({"source": source, "text": text, "final": False})
        last[source] = text
    if recognizer.is_endpoint(stream):
        if text:
            utterances[source] = int(utterances.get(source, 0)) + 1
            events.append(
                {"source": source, "text": text, "final": True, "utterance": utterances[source]}
            )
        recognizer.reset(stream)
        last[source] = ""
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--num-threads", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        recognizer = build_recognizer(Path(args.model_dir), args.num_threads)
    except (OSError, OfflineModelError, FileNotFoundError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
        return 1
    print(json.dumps({"status": "ready", "provider": "cpu"}), flush=True)
    streams, last, utterances = {}, {}, {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            source = str(obj.get("source") or "mic")
            samples = _samples(obj)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr, flush=True)
            continue
        for event in feed(recognizer, streams, last, source, samples, utterances):
            print(json.dumps(event, ensure_ascii=False), flush=True)
    for source, stream in streams.items():
        # Supply right context for the streaming model's final audio window.
        stream.accept_waveform(TARGET_RATE, [0.0] * int(TARGET_RATE * .6))
        if hasattr(stream, "input_finished"):
            stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        text = str(recognizer.get_result(stream)).strip()
        if text:
            utterances[source] = int(utterances.get(source, 0)) + 1
            print(
                json.dumps(
                    {"source": source, "text": text, "final": True, "utterance": utterances[source]},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return 0


def _samples(obj: dict):
    import array

    raw = base64.b64decode(obj["pcm"])
    values = array.array("f")
    values.frombytes(raw)
    return list(values)


if __name__ == "__main__":
    raise SystemExit(main())
