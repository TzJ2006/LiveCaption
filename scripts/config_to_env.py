#!/usr/bin/env python3
"""Print config.json as CFG_* shell assignments for start.sh to eval.

The Swift host parses CLI flags only, so start.sh is where config.json turns into flags. Values
are shell-quoted; a list becomes the comma-separated form the --hf-models flag expects. Unknown
keys are ignored, and an unreadable file only costs a warning -- a broken config should not stop
the caption window from opening.

Usage: python3 scripts/config_to_env.py config.json
"""

import json
import shlex
import sys

# config.json key -> shell variable start.sh reads as a default
KEYS = {
    "source": "CFG_SOURCE",
    "asr": "CFG_ASR",
    "language": "CFG_LANGUAGE",
    "hf-model": "CFG_HF_MODEL",
    "hf-models": "CFG_HF_MODELS",
    "hf-stream-python": "CFG_HF_STREAM_PYTHON",
    "output-dir": "CFG_OUTPUT_DIR",
    "opacity": "CFG_OPACITY",
    "height": "CFG_HEIGHT",
}
FLAGS = {"debug": "CFG_DEBUG", "record": "CFG_RECORD"}


def main():
    try:
        with open(sys.argv[1], encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            raise ValueError("expected a JSON object")
    except (OSError, ValueError, IndexError) as exc:
        print(f"warning: ignoring {sys.argv[1:2] or ['config']}: {exc}", file=sys.stderr)
        return 0

    for key, var in KEYS.items():
        if key not in config or config[key] in (None, ""):
            continue
        value = config[key]
        if isinstance(value, list):
            value = ",".join(str(item).strip() for item in value if str(item).strip())
        print(f"{var}={shlex.quote(str(value))}")
    for key, var in FLAGS.items():
        if config.get(key):
            print(f"{var}=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
