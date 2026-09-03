#!/usr/bin/env python3
"""Word/char-level timestamps for a recorded WAV using Qwen3-ForcedAligner-0.6B.

Usage:
  python3 src/python/align_transcript.py recordings/xxx-mic.wav "transcript text" [--language English]
  python3 src/python/align_transcript.py recordings/xxx-mic.wav transcripts/2026-07-18.txt

Limits: speech only, <= 5 minutes per file, 11 languages. --language is required by the model (it
has no detect mode) and defaults to Chinese.
Install: pip install qwen-asr
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("text", help="transcript text, or path to a transcript file")
    # ponytail: align() calls language.lower() straight away, so None -- the old default -- crashed
    # on every invocation the usage line above documents. The aligner has no detect mode.
    parser.add_argument("--language", default="Chinese", help="e.g. Chinese, English")
    parser.add_argument("--model", default="Qwen/Qwen3-ForcedAligner-0.6B")
    args = parser.parse_args()

    try:
        from qwen_asr import Qwen3ForcedAligner
    except ImportError:
        print("pip install qwen-asr", file=sys.stderr)
        return 1

    text = args.text
    if os.path.isfile(text):
        with open(text, encoding="utf-8") as f:
            text = f.read()

    model = Qwen3ForcedAligner.from_pretrained(args.model)
    results = model.align(audio=args.audio, text=text, language=args.language)
    for item in results[0]:
        print(f"{item.start_time:8.2f} {item.end_time:8.2f}  {item.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
