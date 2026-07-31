#!/usr/bin/env python3
"""Windows host for LiveCaption: mic and/or speaker output -> ASR worker -> caption overlay.

Speaker capture uses WASAPI loopback (no virtual audio driver needed).
Reuses the same NDJSON workers as the macOS Swift host (hf_asr_worker.py / sherpa_asr_worker.py).

Usage:
  python src/python/win_host.py --source both --asr hf --hf-model Qwen/Qwen3-ASR-0.6B [--record]

Deps: pip install pyaudiowpatch numpy  (plus the chosen worker's deps)
"""

import argparse
import base64
import datetime
import json
import os
import queue
import subprocess
import sys
import threading
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=os.path.join(PROJECT, "config.json"),
                   help="JSON config file; CLI flags override it (see config.example.json)")
    p.add_argument("--source", choices=["mic", "system", "both"], default="mic")
    p.add_argument("--asr", choices=["hf", "sherpa", "wsl-vllm"], default="hf",
                   help="apple ASR is macOS-only; wsl-vllm = true streaming Qwen3-ASR inside WSL")
    p.add_argument("--wsl-python", default="/home/tongt/miniconda3/envs/AI/bin/python",
                   help="python interpreter inside WSL, e.g. /home/you/miniconda3/envs/AI/bin/python")
    p.add_argument("--hf-model", default="")
    p.add_argument("--chunk-seconds", type=float, default=3.0,
                   help="audio buffered per ASR call; lower = snappier captions, higher = better accuracy")
    p.add_argument("--language", default="zh-CN")  # accepted for start.sh parity; workers auto-detect
    p.add_argument("--output-dir", default=os.path.join(PROJECT, "transcripts"))
    p.add_argument("--record", action="store_true")
    p.add_argument("--record-dir", default=os.path.join(PROJECT, "recordings"))
    p.add_argument("--opacity", type=float, default=0.75)
    p.add_argument("--height", type=int, default=120)
    p.add_argument("--debug", action="store_true")
    # ponytail: config file values become argparse defaults, so CLI flags still win
    config_path = p.parse_known_args()[0].config
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            p.set_defaults(**{k.replace("-", "_"): v for k, v in json.load(f).items()})
    args = p.parse_args()
    if args.asr == "hf" and not args.hf_model:
        sys.exit("--asr hf requires --hf-model <huggingface/model-id>")
    return args


def downmix(int16_bytes, channels):
    audio = np.frombuffer(int16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


class Recorder:
    """One WAV per source for the whole run, float32 written as int16."""

    def __init__(self, directory, run_id):
        os.makedirs(directory, exist_ok=True)
        self.directory, self.run_id, self.files, self.lock = directory, run_id, {}, threading.Lock()

    def write(self, source, sample_rate, floats):
        with self.lock:
            f = self.files.get(source)
            if f is None:
                f = wave.open(os.path.join(self.directory, f"{self.run_id}-{source}.wav"), "wb")
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(int(sample_rate))
                self.files[source] = f
            f.writeframes((np.clip(floats, -1, 1) * 32767).astype(np.int16).tobytes())

    def close(self):
        with self.lock:
            for f in self.files.values():
                f.close()


def capture_loop(pa, device, source, send, stop):
    """Read one device until stop is set, pushing float32 mono frames to send()."""
    import pyaudiowpatch as pyaudio
    channels = int(device["maxInputChannels"])
    rate = int(device["defaultSampleRate"])
    stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                     input_device_index=device["index"], frames_per_buffer=rate // 10)
    while not stop.is_set():
        data = stream.read(rate // 10, exception_on_overflow=False)
        send(source, rate, downmix(data, channels))
    stream.stop_stream()
    stream.close()


def main():
    args = parse_args()
    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    recorder = Recorder(args.record_dir, run_id) if args.record else None

    # spawn the ASR worker (same NDJSON protocol as the Swift host)
    if args.asr == "hf":
        cmd = [sys.executable, os.path.join(HERE, "hf_asr_worker.py"), "--hf-model", args.hf_model,
               "--chunk-seconds", str(args.chunk_seconds)]
    elif args.asr == "wsl-vllm":
        drive, rest = os.path.splitdrive(os.path.join(HERE, "qwen_stream_worker.py"))
        wsl_script = "/mnt/" + drive[0].lower() + rest.replace("\\", "/")
        cmd = ["wsl", args.wsl_python, wsl_script]
        if args.hf_model:
            cmd += ["--hf-model", args.hf_model]
    else:
        env_deps = os.path.join(PROJECT, ".build", "pydeps")
        cmd = [sys.executable, os.path.join(HERE, "sherpa_asr_worker.py")]
        os.environ["PYTHONPATH"] = env_deps + os.pathsep + os.environ.get("PYTHONPATH", "")
    worker = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                              encoding="utf-8", bufsize=1)
    stdin_lock = threading.Lock()
    texts = queue.Queue()

    def send(source, rate, floats):
        if recorder:
            recorder.write(source, rate, floats)
        if args.debug:
            db = 20 * np.log10(max(float(np.sqrt(np.mean(floats ** 2))), 1e-6))
            texts.put(("__level__", source, db))
        line = json.dumps({"type": "audio", "source": source, "sampleRate": rate,
                           "pcmFloat32": base64.b64encode(floats.tobytes()).decode()})
        with stdin_lock:
            try:
                worker.stdin.write(line + "\n")
            except OSError:
                stop.set()

    # capture
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    stop = threading.Event()
    threads = []
    # ponytail: missing device = warn and keep going with what's left
    if args.source in ("mic", "both"):
        try:
            mic = pa.get_device_info_by_index(pa.get_default_input_device_info()["index"])
            threads.append(threading.Thread(target=capture_loop, args=(pa, mic, "mic", send, stop), daemon=True))
        except (OSError, LookupError) as exc:
            print(f"warning: no microphone available, skipping mic ({exc})", file=sys.stderr)
    if args.source in ("system", "both"):
        try:
            loopback = pa.get_default_wasapi_loopback()
            threads.append(threading.Thread(target=capture_loop, args=(pa, loopback, "sys", send, stop), daemon=True))
        except (OSError, LookupError) as exc:
            print(f"warning: no speaker loopback available, skipping system audio ({exc})", file=sys.stderr)
    if not threads:
        sys.exit("no audio devices available (mic and speaker loopback both missing)")
    for t in threads:
        t.start()

    # transcripts, same layout as the Swift host
    os.makedirs(args.output_dir, exist_ok=True)
    day = datetime.date.today().isoformat()
    paths = {"mic": os.path.join(args.output_dir, f"{day}.txt"),
             "sys": os.path.join(args.output_dir, f"{day}-sys.txt")}

    def read_worker():
        for line in worker.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("status") == "ready":
                texts.put(("__ready__", event.get("device", ""), False))
                continue
            text, source = event.get("text", "").strip(), event.get("source", "mic")
            if not text:
                continue
            texts.put((source, text, bool(event.get("final"))))
            if event.get("final"):
                with open(paths.get(source, paths["mic"]), "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.now():%H:%M:%S}] {text}\n")
        stop.set()

    threading.Thread(target=read_worker, daemon=True).start()

    # overlay mirroring the macOS window: bottom strip, sys | mic columns,
    # (speaker)/(microphone) prefixes, scrolling history, gray live -> white final
    import tkinter as tk
    prefixes = {"sys": "(speaker) ", "mic": "(microphone) "}
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", max(0.1, min(1.0, args.opacity)))
    root.configure(bg="black")
    width = root.winfo_screenwidth() - 100
    bottom_y = root.winfo_screenheight() - args.height - 50
    root.geometry(f"{width}x{args.height}+50+{bottom_y}")
    sources = ["sys", "mic"] if args.source == "both" else ["mic"] if args.source == "mic" else ["sys"]
    widgets, finals = {}, {s: [] for s in sources}

    # bottom-right Hide/Quit controls, same as the macOS window
    controls = tk.Frame(root, bg="black")
    controls.pack(side="bottom", fill="x")
    text_area = tk.Frame(root, bg="black")
    text_area.pack(side="top", fill="both", expand=True)
    collapsed = [False]

    def toggle():
        collapsed[0] = not collapsed[0]
        if collapsed[0]:
            text_area.pack_forget()
            root.geometry(f"{width}x34+50+{bottom_y + args.height - 34}")
            hide_btn.config(text="Show")
        else:
            text_area.pack(side="top", fill="both", expand=True)
            root.geometry(f"{width}x{args.height}+50+{bottom_y}")
            hide_btn.config(text="Hide")

    btn_style = dict(fg="white", font=("Segoe UI", 9, "bold"), bd=0, width=6,
                     activeforeground="white", cursor="hand2")
    quit_btn = tk.Button(controls, text="Quit", bg="#9e1f26", activebackground="#b53039",
                         command=stop.set, **btn_style)
    quit_btn.pack(side="right", padx=(4, 10), pady=4)
    hide_btn = tk.Button(controls, text="Hide", bg="#1a5780", activebackground="#26709f",
                         command=toggle, **btn_style)
    hide_btn.pack(side="right", pady=4)

    for source in sources:
        txt = tk.Text(text_area, bg="black", fg="white", font=("Segoe UI", 14), wrap="word",
                      bd=0, highlightthickness=0, cursor="arrow")
        txt.tag_config("final", foreground="white")
        txt.tag_config("live", foreground="#b8b8b8")
        txt.tag_config("status", foreground="#e6c84a")
        txt.pack(side="left", fill="both", expand=True, padx=(12, 4))
        widgets[source] = txt

    statuses = {s: "Loading ASR model... (first run downloads it)" for s in sources}
    live_texts = {s: None for s in sources}
    levels = {}

    def prefix(source):
        if not args.debug:
            return prefixes[source]
        level = f"{levels[source]:.1f} dB" if source in levels else "waiting"
        return f"{prefixes[source]}{level}  "

    def render(source):
        txt = widgets[source]
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        # ponytail: flowing paragraph, not one line per chunk
        history = " ".join(finals[source][-100:])
        if history:
            txt.insert("end", history + " ", "final")
        if live_texts[source] is not None:
            txt.insert("end", prefix(source) + live_texts[source], "live")
        elif not finals[source]:
            txt.insert("end", prefix(source) + statuses[source], "status")
        else:
            txt.insert("end", prefix(source) if args.debug else "", "status")
        txt.see("end")
        txt.configure(state="disabled")

    for source in sources:
        render(source)

    root.bind("<Escape>", lambda e: stop.set())

    def poll():
        while not texts.empty():
            source, text, final = texts.get()
            if source == "__ready__":
                for s in sources:
                    statuses[s] = f"Listening... [{text}]" if text else "Listening..."
                    render(s)
                continue
            if source == "__level__":
                if text in widgets:
                    levels[text] = final
                    render(text)
                continue
            if source not in widgets:
                continue
            if final:
                # ponytail: prefix only on the live/status line, not every history line;
                # drop the trailing period Qwen appends to every isolated chunk
                finals[source].append(text.rstrip("。."))
                live_texts[source] = None
            else:
                live_texts[source] = text
            render(source)
        if stop.is_set():
            root.destroy()
            return
        root.after(100, poll)

    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        stop.set()
        if recorder:
            recorder.close()
        worker.terminate()
        # ponytail: skip pa.terminate()/stdin.close() — they can deadlock on live
        # streams or a full pipe and hang Quit; hard-exit after cleanup instead
        os._exit(0)


def self_test():
    stereo = np.array([1000, 3000, -2000, -4000], dtype=np.int16).tobytes()
    mono = downmix(stereo, 2)
    assert mono.shape == (2,) and abs(mono[0] - 2000 / 32768.0) < 1e-6
    assert np.frombuffer(base64.b64decode(base64.b64encode(mono.tobytes())), dtype=np.float32).shape == (2,)
    print("self-test ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        main()
