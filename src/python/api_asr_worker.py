#!/usr/bin/env python3
"""Hosted ASR (OpenAI-compatible) as a normal NDJSON worker.

Speaking the same protocol as every other worker means hosted models drop into bench_asr.py -- and
into win_host.py -- with no special-casing, and building a reference transcript is just this worker
with a chunk size larger than the clip.

  python src/python/api_asr_worker.py --api-model gpt-4o-transcribe --chunk-seconds 9999

Credentials come from a JSON config, never from argv -- a command line is visible to every other
process on the machine. Default location keys.json in the project root (gitignored):

  {
    "base_url": "http://your-proxy:4000",
    "api_key":  "sk-...",
    "models":   {"openai": "gpt-4o-transcribe", "gemini": "gemini-3.1-pro-preview"}
  }

Override the path with --key-config or LIVECAPTION_KEY_CONFIG. LIVECAPTION_API_KEY and
LIVECAPTION_BASE_URL also work if you would rather not have a file at all.

Two transports, because a proxy may only offer one:
  transcriptions  POST /v1/audio/transcriptions  (multipart, the dedicated ASR endpoint)
  chat            POST /v1/chat/completions      (base64 input_audio, for audio-in chat models)
--transport auto tries transcriptions and falls back to chat on a 404/405.
"""

import argparse
import base64
import io
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np

PROJECT = pathlib.Path(__file__).resolve().parents[2]
PROMPT = ("Transcribe this meeting audio verbatim. It mixes Mandarin Chinese and English, often "
          "within a single sentence -- keep each word in the language it was spoken in and do not "
          "translate. Output only the transcript.")


def load_credentials(path):
    config = {}
    candidate = pathlib.Path(path or os.environ.get("LIVECAPTION_KEY_CONFIG")
                             or PROJECT / "keys.json")
    if candidate.is_file():
        with open(candidate, encoding="utf-8") as f:
            config = json.load(f)
    # accept the obvious spellings rather than making the user match one exactly
    base = (os.environ.get("LIVECAPTION_BASE_URL") or config.get("base_url")
            or config.get("baseUrl") or config.get("url") or "")
    key = (os.environ.get("LIVECAPTION_API_KEY") or config.get("api_key")
           or config.get("key") or config.get("apiKey") or "")
    if not base or not key:
        raise SystemExit(f"no base_url/api_key in {candidate} or the environment "
                         "(see the docstring of api_asr_worker.py)")
    return base.rstrip("/"), key, config.get("models", {})


def wav_bytes(audio, rate):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buffer.getvalue()


def post(url, key, body, content_type, timeout):
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": content_type})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def via_transcriptions(base, key, model, audio, rate, prompt, timeout):
    """multipart/form-data against the dedicated ASR endpoint."""
    boundary = uuid.uuid4().hex
    parts = []

    def field(name, value):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                     f"{value}\r\n".encode())

    field("model", model)
    field("response_format", "json")
    if prompt:
        field("prompt", prompt)
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                 f"filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode())
    parts.append(wav_bytes(audio, rate))
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    payload = post(f"{base}/v1/audio/transcriptions", key, b"".join(parts),
                   f"multipart/form-data; boundary={boundary}", timeout)
    return (payload.get("text") or "").strip()


def via_chat(base, key, model, audio, rate, prompt, timeout):
    """base64 input_audio through chat completions, for models with no ASR endpoint."""
    encoded = base64.b64encode(wav_bytes(audio, rate)).decode()
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt or PROMPT},
        {"type": "input_audio", "input_audio": {"data": encoded, "format": "wav"}},
    ]}]}).encode()
    payload = post(f"{base}/v1/chat/completions", key, body, "application/json", timeout)
    return (payload["choices"][0]["message"].get("content") or "").strip()


def transcribe(base, key, model, audio, rate, args, state):
    """One clip -> text, retrying transient failures and remembering which transport worked."""
    order = {"transcriptions": [via_transcriptions], "chat": [via_chat],
             "auto": [via_transcriptions, via_chat]}[state.get("transport") or args.transport]
    last = None
    for attempt in range(args.retries + 1):
        for fn in order:
            try:
                text = fn(base, key, model, audio, rate, args.prompt, args.timeout)
                state["transport"] = "transcriptions" if fn is via_transcriptions else "chat"
                return text
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:300].decode("utf-8", "replace")
                last = f"HTTP {exc.code} {detail}"
                # 404/405/415 = endpoint absent. 500 "Unmapped provider" = the proxy has the
                # endpoint but cannot route this model to it, which a gateway reports as its own
                # failure; either way the other transport is the thing to try.
                if exc.code in (404, 405, 415, 500) and len(order) > 1:
                    continue
                if exc.code in (400, 401, 403, 413):
                    raise SystemExit(f"{model}: {last}")   # retrying will not help
                break
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
                last = repr(exc)
                break
        if attempt < args.retries:
            time.sleep(2 ** attempt)
            print(f"retry {attempt + 1}/{args.retries}: {last}", file=sys.stderr)
    print(f"api error: {last}", file=sys.stderr)
    return ""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api-model", required=True, help="model id as the proxy names it")
    p.add_argument("--key-config", default="")
    p.add_argument("--chunk-seconds", type=float, default=30.0,
                   help="audio per request; larger than the clip = one request, the reference mode")
    p.add_argument("--transport", default="auto", choices=["auto", "transcriptions", "chat"])
    p.add_argument("--prompt", default=PROMPT, help="passed as prompt / system text where supported")
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--retries", type=int, default=3)
    args = p.parse_args()

    base, key, _ = load_credentials(args.key_config)
    print(json.dumps({"status": "ready", "device": f"api {args.api_model}"}), flush=True)

    buffers, rates, state = {}, {}, {}

    def flush(source):
        audio = buffers.get(source)
        if audio is None or audio.size == 0:
            return
        buffers[source] = np.zeros(0, dtype=np.float32)
        text = transcribe(base, key, args.api_model, audio, rates[source], args, state)
        if text:
            print(json.dumps({"source": source, "text": text, "final": True}, ensure_ascii=False),
                  flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if event.get("type") != "audio":
                continue
            source = event["source"]
            rates[source] = int(event["sampleRate"])
            audio = np.frombuffer(base64.b64decode(event["pcmFloat32"]), dtype=np.float32)
        except Exception as exc:
            print(f"bad input: {exc}", file=sys.stderr)
            continue
        if audio.size == 0:
            continue
        buffers[source] = np.concatenate([buffers.get(source, np.zeros(0, dtype=np.float32)), audio])
        if buffers[source].size / rates[source] >= args.chunk_seconds:
            flush(source)

    for source in list(buffers):  # stdin closed: the tail is still speech
        flush(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
