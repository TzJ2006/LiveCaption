#!/usr/bin/env python3
"""Minimal local OpenAI-compatible LLM client for transcript files."""

import argparse
import json
import os
import sys
import urllib.request


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript")
    parser.add_argument("prompt", nargs="?", default="Summarize this transcript concisely.")
    return parser.parse_args()


def main():
    args = parse_args()
    with open(args.transcript, "r", encoding="utf-8") as handle:
        transcript = handle.read()

    base_url = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    model = os.environ.get("LOCAL_LLM_MODEL", "llama3.1")
    api_key = os.environ.get("LOCAL_LLM_API_KEY", "ollama")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You answer from the transcript only."},
            {"role": "user", "content": f"{args.prompt}\n\nTranscript:\n{transcript}"},
        ],
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.load(response)
    print(data["choices"][0]["message"]["content"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
