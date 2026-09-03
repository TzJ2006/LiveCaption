#!/usr/bin/env python3
"""Replay a recording through a real ASR worker and log what it said, when, and at what power cost.

This is win_host.py with the microphone replaced by a WAV file and the caption overlay replaced by
a JSON event log. It spawns workers through win_host.worker_command(), so every backend is measured
running exactly the command line the app runs -- same chunking, same commit rules, same everything.

Render the gated stream once (both channels through AudioGate, as --source auto does live):
  python src/python/bench_asr.py --render-gate recordings/X-microphone.wav recordings/X-speaker.wav \
      --out bench/clips/X.wav

Then benchmark a backend against that clip:
  python src/python/bench_asr.py --clip bench/clips/X.wav --asr hf --hf-model Qwen/Qwen3-ASR-0.6B
  python src/python/bench_asr.py --clip bench/clips/X.wav --asr sherpa --pace max

Score the results with score_asr.py.

# ponytail: no per-model inference code lives here. Every backend already speaks NDJSON over a pipe,
# so the benchmark is a fake host, not a model zoo -- adding a model is a command line, not a class.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import win_host as host  # noqa: E402  (path juggling has to come first)

FRAME_SECONDS = 0.1  # capture_loop() reads rate//10 frames; match it so the worker sees live timing


# --- audio -----------------------------------------------------------------------------------

def read_wav(path):
    """(sample_rate, float32 mono). Handles the 16-bit PCM the recorder writes."""
    with wave.open(path, "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"{path}: expected 16-bit PCM, got {width * 8}-bit")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return rate, audio


def write_wav(path, rate, floats):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    clipped = np.clip(floats, -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((clipped * 32767).astype(np.int16).tobytes())


def render_gate(mic_path, sys_path, out_path):
    """Run both channels through AudioGate and write the stream the worker would actually hear.

    Doing this once means every candidate -- local and hosted -- scores against byte-identical
    audio, and the reference transcript describes that same audio rather than a raw channel.

    The gate's hangover normally runs off the wall clock; here it runs off audio position, so the
    render is deterministic and independent of how fast this machine happens to be.
    """
    mic_rate, mic = read_wav(mic_path)
    sys_rate, speaker = read_wav(sys_path)
    gate = host.AudioGate()
    audio_clock = {"t": 0.0}
    gate.clock = lambda: audio_clock["t"]

    out, spans = [], []
    steps = int(max(len(mic) / mic_rate, len(speaker) / sys_rate) / FRAME_SECONDS)
    for step in range(steps):
        audio_clock["t"] = step * FRAME_SECONDS
        for source, audio, rate in (("sys", speaker, sys_rate), ("mic", mic, mic_rate)):
            start = int(step * FRAME_SECONDS * rate)
            block = audio[start:start + int(FRAME_SECONDS * rate)]
            if block.size == 0:
                continue
            selected = gate.process(source, rate, block)
            if selected is None or selected.size == 0:
                continue
            position = sum(len(chunk) for chunk in out) / host.AudioGate.TARGET_RATE
            if spans and spans[-1][2] == source:
                spans[-1][1] = position + len(selected) / host.AudioGate.TARGET_RATE
            else:
                spans.append([position, position + len(selected) / host.AudioGate.TARGET_RATE,
                              source])
            out.append(selected)

    gated = np.concatenate(out) if out else np.zeros(0, dtype=np.float32)
    write_wav(out_path, host.AudioGate.TARGET_RATE, gated)
    sidecar = os.path.splitext(out_path)[0] + ".channels.json"
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump({"seconds": len(gated) / host.AudioGate.TARGET_RATE, "spans": spans}, f, indent=1)
    print(f"{out_path}: {len(gated) / host.AudioGate.TARGET_RATE / 60:.1f} min gated, "
          f"{len(spans)} channel turns -> {sidecar}")
    return out_path


# --- power -----------------------------------------------------------------------------------

class PowerSampler:
    """GPU board power at 2 Hz, plus the worker's own VRAM.

    500 ms because the scalar query fields on this card only refresh at ~2 Hz -- polling faster
    returns the same value several times and produces dense-looking data that is not.
    """

    FIELDS = "timestamp,power.draw,temperature.gpu,clocks_throttle_reasons.active"

    def __init__(self, pid=None):
        self.pid, self.samples, self.peak_vram_mb = pid, [], None
        self.proc, self.thread, self.stop = None, None, threading.Event()

    @staticmethod
    def idle_watts(seconds=3.0):
        """Desktop draw before the worker starts. Idle here is ~86 W, not a rounding error."""
        readings = []
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            value = _nvidia_smi(["--query-gpu=power.draw", "--format=csv,noheader,nounits"])
            if value:
                try:
                    readings.append(float(value.splitlines()[0]))
                except ValueError:
                    pass
            time.sleep(0.5)
        return float(np.mean(readings)) if readings else 0.0

    def start(self):
        try:
            self.proc = subprocess.Popen(
                ["nvidia-smi", f"--query-gpu={self.FIELDS}", "--format=csv,noheader,nounits",
                 "-lms", "500"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError:
            return  # no nvidia-smi: CPU-only backend, power stays empty and the report says so
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        started = time.perf_counter()
        for index, line in enumerate(self.proc.stdout):
            if self.stop.is_set():
                break
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                continue
            try:
                watts, temp = float(parts[1]), float(parts[2])
            except ValueError:
                continue
            self.samples.append([time.perf_counter() - started, watts, temp, parts[3]])
            if self.pid and index % 4 == 0:  # ~2 s: process spawn is too costly at 2 Hz
                self._sample_vram()

    def _sample_vram(self):
        out = _nvidia_smi(["--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"])
        for row in (out or "").splitlines():
            parts = [p.strip() for p in row.split(",")]
            if len(parts) == 2 and parts[0] == str(self.pid):
                try:
                    used = float(parts[1])
                except ValueError:
                    return
                self.peak_vram_mb = max(self.peak_vram_mb or 0.0, used)

    def finish(self):
        self.stop.set()
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.thread:
            self.thread.join(timeout=5)
        return {"samples": self.samples, "peak_vram_mb": self.peak_vram_mb}


def _nvidia_smi(args):
    try:
        return subprocess.run(["nvidia-smi", *args], capture_output=True, text=True,
                              timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


# --- the run ---------------------------------------------------------------------------------

def run(clip, choice, args):
    """Feed one clip to one worker and return the run record."""
    rate, audio = read_wav(clip)
    duration = len(audio) / rate
    command = host.worker_command(choice, args)
    print(f"$ {' '.join(command)}", file=sys.stderr)

    worker = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=None, text=True, encoding="utf-8", bufsize=1)
    events, ready = [], threading.Event()
    fed = {"seconds": 0.0}
    started = {"t": None}

    def reader():
        for line in worker.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("status") == "ready":
                print(f"worker ready: {event.get('device', '')}", file=sys.stderr)
                ready.set()
                continue
            text = (event.get("text") or "").strip()
            if not text or started["t"] is None:
                continue
            events.append({"t": time.perf_counter() - started["t"], "audio_pos": fed["seconds"],
                           "text": text, "final": bool(event.get("final"))})

    threading.Thread(target=reader, daemon=True).start()
    # model load and CUDA warm-up happen before t0 by construction, matching the Open ASR
    # Leaderboard convention of timing inference only
    if not ready.wait(timeout=args.ready_timeout):
        print(f"warning: no ready event after {args.ready_timeout}s; timing includes model load",
              file=sys.stderr)

    idle = PowerSampler.idle_watts() if args.power else 0.0
    power = PowerSampler(worker.pid) if args.power else None
    if power:
        power.start()

    frame = int(FRAME_SECONDS * rate)
    started["t"] = t0 = time.perf_counter()
    blocked, drift_max = 0, 0.0
    for index in range(0, len(audio), frame):
        block = audio[index:index + frame]
        if args.pace == "realtime":
            target = t0 + (index / rate)
            drift = time.perf_counter() - target
            drift_max = max(drift_max, abs(drift))
            if drift < 0:
                time.sleep(-drift)
        line = json.dumps({"type": "audio", "source": "auto", "sampleRate": rate,
                           "pcmFloat32": base64.b64encode(block.astype(np.float32).tobytes()).decode()})
        before = time.perf_counter()
        try:
            worker.stdin.write(line + "\n")
        except (OSError, ValueError):
            print("worker closed its input early", file=sys.stderr)
            break
        if time.perf_counter() - before > 0.05:
            blocked += 1  # pipe backpressure: the worker is not keeping up, i.e. RTF > 1
        fed["seconds"] = min(duration, (index + len(block)) / rate)

    try:
        worker.stdin.close()
    except (OSError, ValueError):
        pass
    worker.wait(timeout=args.drain_timeout)
    wall = time.perf_counter() - t0
    time.sleep(0.3)  # let the reader thread drain the last lines

    record = {
        "model": choice["menu"], "choice": choice["id"], "clip": os.path.basename(clip),
        "pace": args.pace, "audio_seconds": duration, "wall_seconds": wall,
        "rtf": wall / duration if duration else None,
        "warmup_seconds": args.warmup_seconds,
        "blocked_writes": blocked, "max_schedule_drift_s": drift_max,
        "events": events,
    }
    if power:
        record["power"] = {"idle_watts": idle, **power.finish()}
    if args.pace == "realtime" and drift_max > 0.05:
        print(f"warning: feed schedule drifted {drift_max * 1000:.0f} ms; latency numbers are soft",
              file=sys.stderr)
    return record


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--render-gate", nargs=2, metavar=("MIC_WAV", "SYS_WAV"),
                   help="render both channels through AudioGate into one clip and exit")
    p.add_argument("--out", default="", help="output path (--render-gate) or directory (a run)")
    p.add_argument("--clip", help="gated WAV to replay")
    p.add_argument("--asr", default="hf",
                   choices=["hf", "hf-stream", "sherpa", "wsl-vllm", "api"])
    p.add_argument("--hf-model", default="")
    p.add_argument("--api-model", default="", help="hosted model id for --asr api")
    p.add_argument("--pace", default="realtime", choices=["realtime", "max"],
                   help="realtime measures latency and power; max measures throughput (RTF)")
    p.add_argument("--warmup-seconds", type=float, default=20.0,
                   help="audio ignored by the latency stats, so cuDNN autotune is not the first-token number")
    p.add_argument("--power", action="store_true", default=True)
    p.add_argument("--no-power", dest="power", action="store_false")
    p.add_argument("--ready-timeout", type=float, default=900.0)
    p.add_argument("--drain-timeout", type=float, default=900.0)
    # passed straight through to worker_command()
    p.add_argument("--chunk-seconds", type=float, default=3.0)
    p.add_argument("--language", default="auto")
    p.add_argument("--context", default="")
    p.add_argument("--model-dir", default="")
    p.add_argument("--key-config", default="", help="credentials for --asr api")
    p.add_argument("--hf-stream-python", default=host.stream_python())
    p.add_argument("--wsl-python", default="/home/tongt/miniconda3/envs/AI/bin/python")
    args = p.parse_args()

    if args.render_gate:
        if not args.out:
            p.error("--render-gate needs --out")
        render_gate(args.render_gate[0], args.render_gate[1], args.out)
        return 0
    if not args.clip:
        p.error("--clip is required (or --render-gate)")

    if args.api_model:  # worker_command() reads the hosted model id out of the same slot
        args.hf_model = args.api_model
    # the model identity is the model id for hf/api but the directory for sherpa, where choice_id()
    # collapses every checkpoint to the bare string "sherpa" -- distinct runs must not share a name
    label = args.hf_model or os.path.basename(args.model_dir.rstrip("/\\")) or args.asr
    choice = {"id": host.choice_id(args.asr, args.hf_model), "asr": args.asr,
              "hf_model": args.hf_model, "label": label,
              "menu": f"{label} ({args.asr})"}
    record = run(args.clip, choice, args)
    record["label"] = label

    out_dir = args.out or os.path.join(host.PROJECT, "bench", "runs")
    os.makedirs(out_dir, exist_ok=True)
    stem = f"{args.asr}__{label}__{os.path.splitext(os.path.basename(args.clip))[0]}__{args.pace}"
    path = os.path.join(out_dir, stem.replace("/", "_").replace(":", "-") + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)

    finals = sum(1 for e in record["events"] if e["final"])
    print(f"{path}\n  {finals} final / {len(record['events'])} events, "
          f"RTF {record['rtf']:.3f}, blocked writes {record['blocked_writes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
