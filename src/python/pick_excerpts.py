#!/usr/bin/env python3
"""Find the most benchmark-worthy stretch of a long recording, so a human only corrects that part.

Transcribes a gated clip in fixed windows with Qwen3-ASR (locally -- no audio leaves the machine),
then scores each window by how much zh-en code-switching and technical vocabulary it contains and
proposes the densest contiguous span.

Usage:
  python src/python/pick_excerpts.py bench/clips/2026-08-15.wav --span-minutes 10

Why not just take the first ten minutes: a minute of monolingual small talk teaches the benchmark
nothing, and every corrected minute is human time. The reference transcript is the expensive part
of this whole exercise, so it should be spent on the audio that actually discriminates between
models -- switch points and jargon.

# ponytail: rough transcript only. It picks the excerpt, it is never the reference -- that comes
# from the 2-of-3 consensus and a human. Which is why the cheap 0.6B model is the right one here.
"""

import argparse
import json
import os
import pathlib
import sys

HF_CACHE = pathlib.Path(__file__).resolve().parents[2] / "models" / "hf"
if not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(HF_CACHE)
    HF_CACHE.mkdir(parents=True, exist_ok=True)

import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_asr import read_wav  # noqa: E402
from score_asr import normalize, script, tokenize, is_number  # noqa: E402

RATE = 16000


def transcribe_windows(audio, window_seconds, model_id, batch_size):
    from qwen_asr import Qwen3ASRModel
    import torch

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    kwargs = {"device_map": device, "dtype": torch.bfloat16} if device != "cpu" else {}
    print(f"loading {model_id} on {device}...", file=sys.stderr)
    model = Qwen3ASRModel.from_pretrained(model_id, max_inference_batch_size=batch_size, **kwargs)

    step = int(window_seconds * RATE)
    windows = [(i / RATE, min(len(audio), i + step) / RATE, audio[i:i + step])
               for i in range(0, len(audio), step)]
    out = []
    for start in range(0, len(windows), batch_size):
        batch = windows[start:start + batch_size]
        results = model.transcribe(audio=[(w[2], RATE) for w in batch])
        for (begin, end, _), result in zip(batch, results):
            out.append({"start": begin, "end": end, "text": (result.text or "").strip()})
        done = min(start + batch_size, len(windows))
        print(f"  {done}/{len(windows)} windows", end="\r", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    return out


def window_features(text):
    """Switch points, distinct jargon candidates and token count for one window."""
    tokens = tokenize(normalize(text))
    if not tokens:
        return {"tokens": 0, "switches": 0, "jargon": 0}
    scripts = [script(t) for t in tokens]
    switches = sum(1 for a, b in zip(scripts, scripts[1:]) if a != b)
    jargon = {t for t in tokens if script(t) == "en" and len(t) >= 3 and not is_number(t)}
    return {"tokens": len(tokens), "switches": switches, "jargon": len(jargon)}


def best_span(windows, span_seconds):
    """The contiguous run of windows with the most switch points plus distinct jargon.

    Both components are raw counts of 'interesting events', so they add without a tuning knob --
    a span wins by containing more of what the benchmark is trying to discriminate on.
    """
    if not windows:
        return None
    width = max(1, round(span_seconds / (windows[0]["end"] - windows[0]["start"])))
    scores = [w["features"]["switches"] + w["features"]["jargon"] for w in windows]
    best, best_start = -1, 0
    running = sum(scores[:width])
    best, best_start = running, 0
    for start in range(1, max(1, len(scores) - width + 1)):
        running += scores[start + width - 1] - scores[start - 1]
        if running > best:
            best, best_start = running, start
    chunk = windows[best_start:best_start + width]
    return {"start": chunk[0]["start"], "end": chunk[-1]["end"], "score": best,
            "switches": sum(w["features"]["switches"] for w in chunk),
            "jargon_tokens": sum(w["features"]["jargon"] for w in chunk),
            "tokens": sum(w["features"]["tokens"] for w in chunk)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("clip")
    p.add_argument("--window-seconds", type=float, default=30.0)
    p.add_argument("--span-minutes", type=float, default=10.0)
    p.add_argument("--hf-model", default="Qwen/Qwen3-ASR-0.6B")
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--out", default="")
    args = p.parse_args()

    rate, audio = read_wav(args.clip)
    if rate != RATE:
        raise SystemExit(f"{args.clip}: expected {RATE} Hz (render it with --render-gate first)")

    out_path = args.out or os.path.splitext(args.clip)[0] + ".windows.json"
    if os.path.exists(out_path):
        print(f"reusing {out_path}", file=sys.stderr)
        with open(out_path, encoding="utf-8") as f:
            windows = json.load(f)["windows"]
    else:
        windows = transcribe_windows(audio, args.window_seconds, args.hf_model, args.batch_size)

    for window in windows:
        window["features"] = window_features(window["text"])
    span = best_span(windows, args.span_minutes * 60)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"clip": args.clip, "windows": windows, "proposed_span": span}, f,
                  ensure_ascii=False, indent=1)

    total = {k: sum(w["features"][k] for w in windows) for k in ("tokens", "switches", "jargon")}
    print(f"\n{args.clip}: {len(audio) / RATE / 60:.1f} min, {len(windows)} windows")
    print(f"  whole clip : {total['tokens']} tokens, {total['switches']} switch points, "
          f"{total['jargon']} jargon hits")
    if span:
        minutes = lambda s: f"{int(s // 60):02d}:{int(s % 60):02d}"  # noqa: E731
        print(f"  best {args.span_minutes:g} min: {minutes(span['start'])}-{minutes(span['end'])} "
              f"-> {span['tokens']} tokens, {span['switches']} switch points, "
              f"{span['jargon_tokens']} jargon hits")
        share = span["switches"] / total["switches"] * 100 if total["switches"] else 0
        print(f"  that span holds {share:.0f}% of the clip's switch points in "
              f"{args.span_minutes * 60 / (len(audio) / RATE) * 100:.0f}% of its length")
    print(f"  -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
