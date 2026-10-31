#!/usr/bin/env python3
"""Ask a transcript. Model and endpoint come from the environment or flags, not a hidden default."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


MISSING = "未配置模型地址或模型名。请设置 LOCAL_LLM_BASE_URL 与 LOCAL_LLM_MODEL，或 LC_OLLAMA_URL 与 LC_OLLAMA_MODEL。拒绝使用脚本内默认模型。"


def configured_endpoint(base_url: str = "", model: str = "") -> tuple[str, str]:
    """Flags, then LOCAL_LLM_*, then LC_OLLAMA_*. Never invent a model name."""
    url = str(base_url or os.environ.get("LOCAL_LLM_BASE_URL") or "").strip()
    name = str(model or os.environ.get("LOCAL_LLM_MODEL") or "").strip()
    if not url:
        raw = str(os.environ.get("LC_OLLAMA_URL") or "").strip().rstrip("/")
        if raw:
            url = raw if raw.endswith("/v1") else f"{raw}/v1"
    if not name:
        name = str(os.environ.get("LC_OLLAMA_MODEL") or "").strip()
    return url, name


def ask_transcript(transcript: str, prompt: str, *, base_url: str, model: str, api_key: str = "ollama", timeout: float = 120) -> str:
    if not str(base_url or "").strip() or not str(model or "").strip():
        raise RuntimeError(MISSING)
    try:
        from model_access.resources import LocalAdmissionError, lease_local
    except ImportError as exc:
        raise RuntimeError("未安装 model_access，拒绝调用本地模型") from exc
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You answer from the transcript only."},
            {"role": "user", "content": f"{prompt}\n\nTranscript:\n{transcript}"},
        ],
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with lease_local("livecaption-query", model, endpoint=base_url, purpose="text"):
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.load(response)
    except LocalAdmissionError as exc:
        raise RuntimeError(str(exc)) from exc
    return data["choices"][0]["message"]["content"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript")
    parser.add_argument("prompt", nargs="?", default="Summarize this transcript concisely.")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)
    base_url, model = configured_endpoint(args.base_url, args.model)
    api_key = os.environ.get("LOCAL_LLM_API_KEY", "ollama")
    if not base_url or not model:
        print(MISSING, file=sys.stderr)
        return 2
    try:
        with open(args.transcript, "r", encoding="utf-8") as handle:
            transcript = handle.read()
        print(ask_transcript(transcript, args.prompt, base_url=base_url, model=model, api_key=api_key))
    except (OSError, urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
