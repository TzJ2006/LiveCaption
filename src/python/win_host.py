#!/usr/bin/env python3
"""Windows host for LiveCaption: mic and/or speaker output -> ASR worker -> caption overlay.

Speaker capture uses WASAPI loopback (no virtual audio driver needed).
Reuses the same NDJSON workers as the macOS Swift host (hf_asr_worker.py, hf_stream_worker.py,
sherpa_asr_worker.py); the Overlay class below is a port of SubtitleWindow in
src/swift/LiveSubtitle.swift -- same geometry, colours, controls and text model, so both hosts
look and behave the same.

Usage:
  python src/python/win_host.py --source auto --asr hf --hf-model Qwen/Qwen3-ASR-0.6B [--record]
  python src/python/win_host.py --source both --asr hf-stream \
      --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b

Deps: pip install pyaudiowpatch numpy  (plus the chosen worker's deps)
"""

import argparse
import base64
import ctypes
import datetime
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))

# AppKit colours the Swift host draws with
WHITE = "#ffffff"
LIVE = "#b8b8b8"    # NSColor(white: 0.72)
YELLOW = "#ffcc00"  # .systemYellow
GREEN = "#28cd41"   # .systemGreen
ORANGE = "#ff9500"  # .systemOrange
RED = "#ff3b30"     # .systemRed
CAPTION_FAMILY = "Consolas"
BUTTON_FAMILY = "Segoe UI"
# "auto" is a pane, not a channel: every line in it carries the prefix of the channel it came from
PREFIXES = {"mic": "(microphone) ", "sys": "(speaker) ", "auto": ""}
RECORD_LABELS = {"mic": "microphone", "sys": "speaker"}


def split_models(value):
    """--hf-models takes a comma-separated string (CLI) or a JSON list (config.json)."""
    if isinstance(value, str):
        value = value.split(",")
    return [str(item).strip() for item in value if str(item).strip()]


HF_PATHS = {"hf": "chunked", "hf-stream": "streaming"}


def hf_choice(spec):
    """Split one model spec into (path, model id).

    A cache-aware checkpoint decoded in fixed blocks throws away the thing it was built for, and a
    plain seq2seq has no streaming state to keep, so the two never share a worker. `stream:` and
    `offline:` pick the path outright; otherwise the id does, since checkpoints that stream say so
    in their name (nvidia/nemotron-3.5-asr-streaming-0.6b).
    """
    for prefix, asr in (("stream:", "hf-stream"), ("offline:", "hf")):
        if spec.startswith(prefix):
            return asr, spec[len(prefix):].strip()
    return ("hf-stream" if "streaming" in spec.lower() else "hf"), spec.strip()


def choice_id(asr, hf_model):
    return f"{asr}:{hf_model}" if asr in HF_PATHS else asr


def stream_python():
    """The venv setup-hf-stream.sh builds, or this interpreter if it was never built."""
    for parts in (("bin", "python"), ("Scripts", "python.exe")):
        candidate = os.path.join(PROJECT, ".build", "stream-env", *parts)
        if os.path.exists(candidate):
            return candidate
    return sys.executable


def model_choices(asr, hf_model, hf_models):
    """Entries for the caption bar dropdown: built-in backends first, then every HF model id.

    Everything the host can spawn is listed whether or not its deps are installed -- a missing
    one exits right away and says so in the caption area, which beats hiding it silently.
    """
    choices = [
        {"id": "sherpa", "label": "Sherpa", "menu": "Sherpa-ONNX", "asr": "sherpa", "hf_model": ""},
        {"id": "wsl-vllm", "label": "WSL vLLM", "menu": "WSL vLLM (streaming)", "asr": "wsl-vllm", "hf_model": ""},
    ]
    specs = list(hf_models)
    if hf_model:  # --asr already said which path the startup model takes; keep it off the guess
        specs.insert(0, {"hf-stream": "stream:", "hf": "offline:"}.get(asr, "") + hf_model)
    seen = set()
    for spec in specs:
        path, model = hf_choice(spec)
        cid = choice_id(path, model)
        if not model or cid in seen:
            continue
        seen.add(cid)
        name = model.rsplit("/", 1)[-1]
        choices.append({"id": cid, "label": name if len(name) <= 16 else name[:15] + "…",
                        "menu": f"{model} ({HF_PATHS[path]})", "asr": path, "hf_model": model})
    return choices


def worker_command(choice, args, base_pythonpath=""):
    """Same NDJSON protocol for every backend, so only the command line differs.

    Module level rather than a closure so bench_asr.py can spawn the exact command the app spawns
    -- a benchmark that builds its own copy of this measures a command line that drifts.
    """
    if choice["asr"] == "hf":
        cmd = [sys.executable, os.path.join(HERE, "hf_asr_worker.py"),
               "--hf-model", choice["hf_model"], "--chunk-seconds", str(args.chunk_seconds)]
        return cmd + (["--context", args.context] if getattr(args, "context", "") else [])
    if choice["asr"] == "hf-stream":
        return [args.hf_stream_python, os.path.join(HERE, "hf_stream_worker.py"),
                "--hf-model", choice["hf_model"], "--language", args.language]
    if choice["asr"] == "api":
        cmd = [sys.executable, os.path.join(HERE, "api_asr_worker.py"),
               "--api-model", choice["hf_model"], "--chunk-seconds", str(args.chunk_seconds)]
        return cmd + (["--key-config", args.key_config] if getattr(args, "key_config", "") else [])
    if choice["asr"] == "wsl-vllm":
        drive, rest = os.path.splitdrive(os.path.join(HERE, "qwen_stream_worker.py"))
        cmd = ["wsl", args.wsl_python, "/mnt/" + drive[0].lower() + rest.replace("\\", "/")]
        return cmd + (["--hf-model", args.hf_model] if args.hf_model else [])
    env_deps = os.path.join(PROJECT, ".build", "pydeps")
    # rebuilt from the startup value so repeated switches cannot stack the path up
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (env_deps, base_pythonpath) if p)
    cmd = [sys.executable, os.path.join(HERE, "sherpa_asr_worker.py"),
          "--chunk-seconds", str(args.chunk_seconds)]
    return cmd + (["--model-dir", args.model_dir] if getattr(args, "model_dir", "") else [])


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=os.path.join(PROJECT, "config.json"),
                   help="JSON config file; CLI flags override it (see config.example.json)")
    p.add_argument("--source", choices=["mic", "system", "auto", "both"], default="auto",
                   metavar="{mic,system,auto}",
                   help="auto = capture both channels but recognize only whichever is talking, "
                        "tagging each line with its channel. 'both' is the retired two-column mode "
                        "and now means auto")
    p.add_argument("--asr", choices=["hf", "hf-stream", "sherpa", "wsl-vllm"], default="hf",
                   help="hf = offline model in fixed blocks, hf-stream = cache-aware streaming model; "
                        "apple ASR is macOS-only; wsl-vllm = true streaming Qwen3-ASR inside WSL")
    p.add_argument("--wsl-python", default="/home/tongt/miniconda3/envs/AI/bin/python",
                   help="python interpreter inside WSL, e.g. /home/you/miniconda3/envs/AI/bin/python")
    p.add_argument("--hf-stream-python", default=stream_python(),
                   help="interpreter for the streaming worker; point it at a transformers>=5.13 "
                        "environment when the offline one is pinned lower (qwen-asr pins 4.57). "
                        "Defaults to .build/stream-env when setup-hf-stream.sh has built it")
    p.add_argument("--hf-model", default="")
    p.add_argument("--hf-models", default="",
                   help="extra model ids for the caption bar dropdown, comma separated; prefix an "
                        "id with stream: or offline: to force its path")
    p.add_argument("--chunk-seconds", type=float, default=3.0,
                   help="audio buffered per offline ASR call; lower = snappier captions, higher = "
                        "better accuracy. Streaming models use their own chunk size instead")
    p.add_argument("--language", default="zh-CN",
                   help="language prompt for hf-stream (auto detects per utterance); the other "
                        "workers auto-detect and only accept it for start.sh parity")
    p.add_argument("--context", default="",
                   help="vocabulary hint for --asr hf with Qwen3-ASR: jargon, product names, "
                        "attendee names. Comma-separated is fine; other backends ignore it")
    p.add_argument("--model-dir", default="",
                   help="model directory for --asr sherpa; omit to use the worker's default")
    p.add_argument("--output-dir", default=os.path.join(PROJECT, "transcripts"))
    p.add_argument("--record", action="store_true")
    p.add_argument("--record-dir", default=os.path.join(PROJECT, "recordings"))
    p.add_argument("--debug-dir", default=os.path.join(PROJECT, "debug-audio"))
    p.add_argument("--opacity", type=float, default=0.75)
    p.add_argument("--height", type=int, default=120)
    p.add_argument("--debug", action="store_true")
    # ponytail: config file values become argparse defaults, so CLI flags still win
    config_path = p.parse_known_args()[0].config
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            p.set_defaults(**{k.replace("-", "_"): v for k, v in json.load(f).items()})
    args = p.parse_args()
    if args.source == "both":
        # the caption window is single-pane now; keep older configs and habits working
        print("note: --source both is retired (one caption pane now); using auto", file=sys.stderr)
        args.source = "auto"
    if args.asr in HF_PATHS and not args.hf_model:
        sys.exit(f"--asr {args.asr} requires --hf-model <huggingface/model-id>")
    args.opacity = min(1.0, max(0.1, args.opacity))  # same clamps as the Swift host
    args.height = min(500, max(70, int(args.height)))
    return args


def downmix(int16_bytes, channels):
    audio = np.frombuffer(int16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


def enable_dpi_awareness():
    """Opt into real pixels before the first window exists.

    A DPI-unaware process gets rendered at 96 DPI and bitmap-stretched by Windows, which makes the
    captions blurry on any display scaled above 100%.
    """
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Windows 8.1 fallback
    except (OSError, AttributeError):
        pass


def work_area_bottom(fallback):
    """Desktop bottom minus the taskbar.

    The Swift host pins the strip to screen.minY; the Windows taskbar is always-on-top, so the
    same coordinate would hide the Hide/Quit bar behind it.
    """
    try:
        class RECT(ctypes.Structure):
            _fields_ = [(name, ctypes.c_long) for name in ("left", "top", "right", "bottom")]

        rect = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.bottom
    except (OSError, AttributeError, ValueError):
        pass
    return fallback


def rms_db(floats):
    """Level meter matching rmsDB() in LiveSubtitle.swift: -90 floor, +6 ceiling."""
    rms = float(np.sqrt(np.mean(np.square(floats)))) if len(floats) else 0.0
    return -90.0 if rms <= 1e-6 else float(np.clip(20 * np.log10(rms), -90, 6))


class AudioGate:
    """--source auto: keep whichever channel is talking, drop the other one.

    Port of AudioGate in src/swift/LiveSubtitle.swift -- same threshold, hangover and linear
    resampler, so both hosts hand the channel over at the same moment.

    The speaker wins while it has voice and the microphone gets the rest: in a meeting the far end
    is the side you cannot ask to repeat itself, and your own voice is the one you already heard.
    The hangover stops a pause inside a sentence from handing the channel back and forth mid-word.
    """

    TARGET_RATE = 16000
    SYSTEM_VOICE_DB = -45.0
    HANGOVER = 0.6

    def __init__(self):
        self.lock = threading.Lock()
        self.last_system_voice = float("-inf")
        self.selected = "mic"
        self.positions = {}
        # ponytail: live capture arrives in real time, so the wall clock IS the audio clock. Only
        # bench_asr.py replaying a file faster than real time needs to say otherwise, and it swaps
        # in an audio-position clock rather than forking the handover logic.
        self.clock = time.monotonic

    def process(self, source, rate, floats):
        """16 kHz mono for the winning channel, or None when `source` is the muted one."""
        if floats.size == 0 or rate <= 0:
            return None
        with self.lock:
            now = self.clock()
            if source == "sys" and rms_db(floats) >= self.SYSTEM_VOICE_DB:
                self.last_system_voice = now
            self.selected = "sys" if now - self.last_system_voice <= self.HANGOVER else "mic"
            if source != self.selected:
                return None
            return self._resample(source, rate, floats)

    def _resample(self, source, rate, floats):
        """Linear resample to 16 kHz, carrying the fractional read position across calls.

        Each channel keeps its own position: they are resampled independently and only one of them
        reaches the worker at a time, so a shared cursor would jump on every handover.
        """
        if abs(rate - self.TARGET_RATE) <= 1:
            return floats
        step = rate / self.TARGET_RATE
        start = self.positions.get(source, 0.0)
        count = max(0, int(np.ceil((len(floats) - start) / step)))
        taps = start + step * np.arange(count)
        lower = taps.astype(np.int64)
        upper = np.minimum(lower + 1, len(floats) - 1)
        self.positions[source] = start + step * count - len(floats)
        fraction = (taps - lower).astype(np.float32)
        return (floats[lower] + (floats[upper] - floats[lower]) * fraction).astype(np.float32)


class Recorder:
    """One WAV per source for the whole run, float32 written as int16."""

    def __init__(self, directory, run_id):
        os.makedirs(directory, exist_ok=True)
        self.directory, self.run_id, self.files, self.lock = directory, run_id, {}, threading.Lock()

    def write(self, source, sample_rate, floats):
        with self.lock:
            f = self.files.get(source)
            if f is None:
                label = RECORD_LABELS.get(source, source)
                f = wave.open(os.path.join(self.directory, f"{self.run_id}-{label}.wav"), "wb")
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(int(sample_rate))
                self.files[source] = f
            f.writeframes((np.clip(floats, -1, 1) * 32767).astype(np.int16).tobytes())

    def close(self):
        with self.lock:
            for f in self.files.values():
                f.close()


class Overlay:
    """Port of SubtitleWindow (src/swift/LiveSubtitle.swift).

    Borderless always-on-top strip pinned to the bottom of the screen: one caption pane above a
    34pt control bar holding a drag handle, the ASR model dropdown, Hide and Quit. The pane keeps
    an append-only history; the current line is rewritten in place (gray while partial, white once
    final) and prefixed with (speaker)/(microphone) -- under --source auto that prefix names
    whichever channel the gate picked for that line. Scrolling only follows the tail when the view
    is already at the bottom. Dragging the handle moves the window; Hide collapses it to a pill
    that keeps only the control bar, anchored at the bottom-right corner.

    # ponytail: one pane, fixed for the whole run. The side-by-side speaker|microphone columns are
    # retired: two recognizers writing one window is what the gate exists to avoid, and two panes
    # split a conversation down the middle instead of reading as one transcript.
    """

    # layoutControls()/collapsedWidth in Swift; macOS points, scaled through _px()
    BAR_H = 34
    BTN_W, BTN_H = 52, 22
    MODEL_W = 108
    HANDLE = 22
    GAP, PAD = 6, 6
    MIN_VISIBLE = 24  # DragHandleView.clampedOrigin keeps this much of the window reachable
    COLLAPSED_W = PAD + HANDLE + GAP + MODEL_W + GAP + BTN_W + GAP + BTN_W + PAD

    def __init__(self, pane, height, opacity, on_quit, choices=(), current="", on_model=None):
        self.pane, self.on_quit = pane, on_quit
        self.collapsed = False
        self.drag_from = None
        self.line_start = "1.0"
        self.live_text, self.live_color = None, WHITE
        self.debug_prefix, self.level, self.speaker = "", None, None

        enable_dpi_awareness()
        self.root = root = tk.Tk()
        # ponytail: the Swift sizes below are macOS points; scale them once here. The strip stays
        # on the primary monitor, so a per-monitor DPI change mid-run is not worth handling.
        self.scale = root.winfo_fpixels("1i") / 96.0
        self.height, self.bar_height = self._px(height), self._px(self.BAR_H)
        self.collapsed_width = self._px(self.COLLAPSED_W)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", opacity)
        root.configure(bg="black")
        # the Swift window spans the full screen.width since the drag handle landed
        self.width = root.winfo_screenwidth()
        self.work_bottom = work_area_bottom(root.winfo_screenheight())
        self._apply_geometry(0, self.work_bottom - self.height, self.width, self.height)
        self.caption_font = (CAPTION_FAMILY, -self._px(14))

        # textFrame(in:) -- 10pt side margins, 34pt control bar below, 5pt above
        x, y = self._px(10), self._px(5)
        w, h = self.width - self._px(20), max(self._px(40), self.height - self._px(39))
        self._add_region(x, y, w, h)

        style = dict(fg="white", font=(BUTTON_FAMILY, -self._px(11), "bold"), bd=0, relief="flat",
                     highlightthickness=0, activeforeground="white", cursor="hand2")
        self.quit_btn = tk.Button(root, text="Quit", bg="#9e1f26", activebackground="#b53039",
                                  command=self.quit, **style)
        self.hide_btn = tk.Button(root, text="Hide", bg="#1a5780", activebackground="#26709f",
                                  command=self.toggle, **style)
        self.model_btn = tk.Menubutton(root, text="", bg="#383d4a", activebackground="#4a5163", **style)
        self.model_choice = tk.StringVar(value=current)
        menu = tk.Menu(self.model_btn, tearoff=0, bg="#1e1e1e", fg=WHITE, activebackground="#26709f",
                       activeforeground=WHITE, bd=0, font=(BUTTON_FAMILY, -self._px(11)))
        for choice in choices:
            menu.add_radiobutton(label=choice["menu"], value=choice["id"], variable=self.model_choice,
                                 command=lambda cid=choice["id"]: on_model and on_model(cid))
            if choice["id"] == current:
                self.model_btn.config(text=choice["label"])
        self.model_btn.config(menu=menu)
        self.handle = self._make_handle()
        self._layout_controls(self.width, self.height)

        root.bind("<Escape>", lambda _e: self.quit())
        root.bind_all("<Control-c>", self._copy)
        root.bind_all("<MouseWheel>", self._wheel)

    def _px(self, points):
        """macOS point -> physical pixel on this display."""
        return round(points * self.scale)

    def _add_region(self, x, y, w, h):
        txt = tk.Text(self.root, bg="black", fg=WHITE, font=self.caption_font, wrap="word", bd=0,
                      padx=0, pady=0, highlightthickness=0, cursor="arrow", insertwidth=0,
                      state="disabled")
        for color in (WHITE, LIVE, YELLOW, GREEN, ORANGE):
            txt.tag_config(color, foreground=color)
        self.place = dict(x=x, y=y, width=w, height=h)
        txt.place(**self.place)
        self.text = txt

    # --- text model (replaceLine / setStatus / update / showDebug in Swift) ---

    def _line_prefix(self):
        """Who a caption came from: on the auto pane the gated channel, not the pane itself."""
        return PREFIXES.get(self.speaker or self.pane, "")

    def _replace_line(self, text, color):
        """Rewrite everything after the last committed line, Swift's replaceLine()."""
        txt = self.text
        follow = txt.yview()[1] > 0.99  # isNearBottom()
        start = self.line_start
        txt.configure(state="normal")
        txt.delete(start, "end")
        # ponytail: separator leads the pending line — Tk owns the trailing newline and would
        # swallow a committed one on the next delete-to-end
        txt.insert(start, ("" if start == "1.0" else "\n") + text, color)
        txt.configure(state="disabled")
        if follow:
            txt.see("end-1c")

    def _commit_line(self):
        self.line_start = self.text.index("end-1c")

    def set_status(self, text, color=YELLOW):
        """A status belongs to the pane, not to a channel, so it stays unprefixed under auto."""
        self._replace_line(PREFIXES.get(self.pane, "") + text, color)
        self._commit_line()

    def update(self, text, final, speaker=None):
        if speaker:  # --source auto: one pane, so each line names its own channel
            self.speaker = speaker
        self.live_text = text
        self.live_color = WHITE if final else LIVE
        self._replace_line(f"{self._line_prefix()}{self.debug_prefix}{text}", self.live_color)
        if final:
            self._commit_line()
            self.live_text = None

    def show_debug(self):
        self.debug_prefix = f"{self.level:.1f} dB  " if self.level is not None else "waiting  "
        prefix = self._line_prefix() + self.debug_prefix
        if self.live_text is not None:
            self._replace_line(prefix + self.live_text, self.live_color)
        else:
            self._replace_line(prefix, GREEN)

    # --- controls ---

    def _make_handle(self):
        """DragHandleView: grab strip with two dots that moves the whole window.

        # ponytail: Tk has no layer cornerRadius, so the handle's 5pt rounding is dropped rather
        # than hand-drawn on a Canvas -- the borderless window already skips its own 8pt radius.
        """
        size = self._px(self.HANDLE)
        canvas = tk.Canvas(self.root, width=size, height=size, bg="#595959", bd=0,
                           highlightthickness=0, cursor="fleur")
        dot = self._px(3)
        for offset in (-self._px(3.5), self._px(3.5)):  # Swift draws them at midY +2 / -5
            cx, cy = size / 2, size / 2 + offset
            canvas.create_oval(cx - dot / 2, cy - dot / 2, cx + dot / 2, cy + dot / 2,
                               fill="#bfbfbf", outline="")
        canvas.bind("<ButtonPress-1>", self._drag_start)
        canvas.bind("<B1-Motion>", self._drag_move)
        return canvas

    def _current_size(self):
        return (self.collapsed_width, self.bar_height) if self.collapsed else (self.width, self.height)

    def _clamp(self, left, top, width, height):
        """DragHandleView.clampedOrigin(), flipped into Tk's top-left origin.

        MIN_VISIBLE stays reachable on the left, right and top; the bottom edge never sinks past
        the work area, mirroring the Swift clamp against screenFrame.minY. Multi-monitor setups
        clamp to the primary display only, like the rest of this overlay.
        """
        visible = self._px(self.MIN_VISIBLE)
        left = min(max(left, visible - width), self.root.winfo_screenwidth() - visible)
        top = min(max(top, visible - height), self.work_bottom - height)
        return int(left), int(top)

    def _apply_geometry(self, left, top, width, height):
        self.left, self.top = self._clamp(left, top, width, height)
        self.root.geometry(f"{width}x{height}+{self.left}+{self.top}")

    def _drag_start(self, event):
        self.drag_from = (event.x_root, event.y_root, self.left, self.top)

    def _drag_move(self, event):
        if self.drag_from is None:
            return
        start_x, start_y, left, top = self.drag_from
        self._apply_geometry(left + event.x_root - start_x, top + event.y_root - start_y,
                             *self._current_size())

    def set_model_label(self, label, choice_id):
        """Keep the button text and the menu's radio mark on the model that is really running."""
        self.model_btn.config(text=label)
        self.model_choice.set(choice_id)

    def _layout_controls(self, width, height):
        """layoutControls(in:) -- handle, model, Hide and Quit right-aligned in the control bar."""
        y = height - self._px(self.PAD + self.BTN_H)
        quit_x = width - self._px(self.PAD + self.BTN_W)
        hide_x = quit_x - self._px(self.GAP + self.BTN_W)
        model_x = hide_x - self._px(self.GAP + self.MODEL_W)
        box = dict(y=y, width=self._px(self.BTN_W), height=self._px(self.BTN_H))
        self.quit_btn.place(x=quit_x, **box)
        self.hide_btn.place(x=hide_x, **box)
        self.model_btn.place(x=model_x, y=y, width=self._px(self.MODEL_W), height=self._px(self.BTN_H))
        self.handle.place(x=model_x - self._px(self.GAP + self.HANDLE), y=y,
                          width=self._px(self.HANDLE), height=self._px(self.HANDLE))

    def toggle(self):
        """toggleVisibility(): collapse to a pill holding just the control bar."""
        width, height = self._current_size()
        self.collapsed = not self.collapsed
        self.hide_btn.config(text="Show" if self.collapsed else "Hide")
        new_width, new_height = self._current_size()
        self.text.place_forget() if self.collapsed else self.text.place(**self.place)
        # the bottom-right corner stays put, so the pill grows back up and to the left
        self._apply_geometry(self.left + width - new_width, self.top + height - new_height,
                             new_width, new_height)
        self._layout_controls(new_width, new_height)

    def quit(self):
        self._replace_line("Stopping LiveCaption...", ORANGE)
        self.on_quit()

    def _copy(self, _event=None):
        selected = self.text.tag_ranges("sel")
        value = (self.text.get("sel.first", "sel.last") if selected
                 else self.text.get("1.0", "end-1c"))
        self.root.clipboard_clear()
        self.root.clipboard_append(value)

    def _wheel(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if isinstance(widget, tk.Text):
            widget.yview_scroll(-event.delta // 120, "units")


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
    run_id = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    # ponytail: --debug reuses the recorder, just pointed at debug-audio/ (same as the Swift host)
    recorder = (Recorder(args.debug_dir if args.debug else args.record_dir, run_id)
                if args.debug or args.record else None)

    choices = model_choices(args.asr, args.hf_model, split_models(args.hf_models))
    current = {"id": choice_id(args.asr, args.hf_model)}
    # state["gen"] is the generation counter: a worker only speaks for the UI while it is current,
    # so the output of one we already replaced cannot land in the captions or the transcript
    state = {"worker": None, "gen": 0}
    state_lock = threading.Lock()
    stdin_lock = threading.Lock()
    events = queue.Queue()
    base_pythonpath = os.environ.get("PYTHONPATH", "")

    gate = AudioGate() if args.source == "auto" else None
    # the channel the last gated frame came from; read_worker() puts it back on the text
    heard = {"source": "mic"}

    def send(source, rate, floats):
        if recorder:  # WAVs stay per channel -- the gate picks captions, not what was recorded
            recorder.write(source, rate, floats)
        if gate is not None:
            # ponytail: the winning channel goes out under one "auto" label so the worker keeps a
            # single unbroken stream. Splitting it back into mic/sys here would starve whichever
            # side is quiet, and a streaming recognizer needs the silence to finish its sentence.
            selected = gate.process(source, rate, floats)
            if selected is None:
                return
            heard["source"] = source
            source, rate, floats = "auto", AudioGate.TARGET_RATE, selected
        if args.debug:  # only the gated pane relabels itself; mic/system panes are their own channel
            events.put(("__level__", source, rms_db(floats), heard["source"] if gate else None))
        with state_lock:
            worker = state["worker"]
        if worker is None:  # between models, or the last one died -- drop the audio
            return
        line = json.dumps({"type": "audio", "source": source, "sampleRate": rate,
                           "pcmFloat32": base64.b64encode(floats.tobytes()).decode()})
        with stdin_lock:
            try:
                worker.stdin.write(line + "\n")
            except (OSError, ValueError):
                pass  # worker gone; read_worker() reports it and the dropdown stays usable

    # capture
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    stop = threading.Event()
    threads = []
    # ponytail: missing device = warn and keep going with what's left
    if args.source in ("mic", "both", "auto"):
        try:
            mic = pa.get_device_info_by_index(pa.get_default_input_device_info()["index"])
            threads.append(threading.Thread(target=capture_loop, args=(pa, mic, "mic", send, stop), daemon=True))
        except (OSError, LookupError) as exc:
            print(f"warning: no microphone available, skipping mic ({exc})", file=sys.stderr)
    if args.source in ("system", "both", "auto"):
        try:
            loopback = pa.get_default_wasapi_loopback()
            threads.append(threading.Thread(target=capture_loop, args=(pa, loopback, "sys", send, stop), daemon=True))
        except (OSError, LookupError) as exc:
            print(f"warning: no speaker loopback available, skipping system audio ({exc})", file=sys.stderr)
    if not threads:
        sys.exit("no audio devices available (mic and speaker loopback both missing)")
    for t in threads:
        t.start()

    # transcripts, same layout as the Swift host (date resolved per line, so a run can cross midnight)
    os.makedirs(args.output_dir, exist_ok=True)
    pending = {}  # source -> (text, speaker, language) of the line still being spoken
    spoken = {}   # source -> channel that line was credited to; --source auto only

    def append_transcript(source, text, speaker=None, language=""):
        """One file per channel, except auto: that one is a single conversation, so both channels
        share the main file and each line says which side it came from.

        `language` is whatever the worker detected for this line -- only --asr hf-stream on
        --language auto reports one, and it is what makes a bilingual meeting searchable later.
        """
        day = datetime.date.today().isoformat()
        suffix = "-sys" if source == "sys" else ""
        label = PREFIXES.get(speaker, "") if source == "auto" else ""
        tag = f"[{language}] " if language else ""
        with open(os.path.join(args.output_dir, f"{day}{suffix}.txt"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {label}{tag}{text}\n")

    def read_worker(worker, gen, choice):
        for line in worker.stdout:
            with state_lock:
                if state["gen"] != gen:
                    break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("status") == "ready":
                print(f"worker ready: {event.get('device', '')}", file=sys.stderr)
                events.put(("__status__", f"{choice['menu']} ready", GREEN, None))
                continue
            text, source = event.get("text", "").strip(), event.get("source", "mic")
            if not text:
                continue
            final = bool(event.get("final"))
            # ponytail: a line's channel is locked when it starts, not re-read as it grows -- the
            # gate can hand over mid-sentence, and relabelling half a caption reads worse than
            # crediting the whole of it to whoever opened it.
            speaker = spoken.setdefault(source, heard["source"]) if source == "auto" else source
            # ponytail: the detected language goes to the transcript only. The captions already
            # read as one language or the other, and the pane has enough prefixes on it.
            language = event.get("language", "")
            events.put((source, text, final, speaker))
            if final:
                append_transcript(source, text, speaker, language)
                pending.pop(source, None)
                spoken.pop(source, None)
            else:
                pending[source] = (text, speaker, language)
        worker.wait()
        # ponytail: a dead worker used to end the run; with hot switching it only ends this model,
        # so the caption bar says so and the dropdown can start another one
        with state_lock:
            superseded = state["gen"] != gen
            if not superseded:
                state["worker"] = None
        if not superseded:
            events.put(("__status__", f"{choice['menu']} stopped (exit {worker.returncode})"
                                      " — pick another model", RED, None))
        with stdin_lock:
            try:
                worker.stdin.close()
            except (OSError, ValueError):
                pass

    def start_worker(choice):
        try:
            worker = subprocess.Popen(worker_command(choice, args, base_pythonpath),
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      text=True, encoding="utf-8", bufsize=1)
        except OSError as exc:
            events.put(("__status__", f"Could not start {choice['menu']}: {exc}", RED, None))
            return
        with state_lock:
            state["gen"] += 1
            gen, state["worker"] = state["gen"], worker
        threading.Thread(target=read_worker, args=(worker, gen, choice), daemon=True).start()

    def stop_worker():
        """Retire the running worker. Never holds stdin_lock, so a blocked write cannot stall it."""
        spoken.clear()  # the next model opens its own lines; do not credit them to the old ones
        with state_lock:
            worker, state["worker"] = state["worker"], None
            state["gen"] += 1
        if worker is not None:
            try:
                worker.terminate()
            except OSError:
                pass

    def switch_model(cid):
        choice = next((c for c in choices if c["id"] == cid), None)
        if choice is None or cid == current["id"]:
            return
        stop_worker()
        current["id"] = cid
        overlay.set_model_label(choice["label"], cid)
        events.put(("__status__", f"Switching to {choice['menu']}...", YELLOW, None))
        start_worker(choice)

    pane = {"mic": "mic", "system": "sys", "auto": "auto"}[args.source]
    overlay = Overlay(pane, args.height, args.opacity, stop.set,
                      choices=choices, current=current["id"], on_model=switch_model)
    if args.debug:
        overlay.show_debug()
    else:
        overlay.set_status("Listening...")
    overlay.root.after(0, overlay.root.focus_force)
    start_worker(next(c for c in choices if c["id"] == current["id"]))

    last_debug_draw = [0.0]

    def poll():
        while not events.empty():
            kind, payload, extra, speaker = events.get()
            if kind == "__level__":  # (source, dB, gated channel)
                overlay.level = extra
                if speaker:  # so the meter names the channel even before it says anything
                    overlay.speaker = speaker
                now = time.monotonic()
                if now - last_debug_draw[0] >= 0.15:  # same redraw throttle as the Swift host
                    last_debug_draw[0] = now
                    overlay.show_debug()
                continue
            if kind == "__status__":  # (message, colour) -- model switches and worker failures
                overlay.set_status(payload, extra)
                continue
            overlay.update(payload, extra, speaker)  # (source, text, final, channel)
        if stop.is_set():
            overlay.root.destroy()
            return
        overlay.root.after(100, poll)

    overlay.root.after(100, poll)
    try:
        overlay.root.mainloop()
    finally:
        stop.set()
        for source, (text, speaker, language) in pending.items():  # applicationWillTerminate() too
            append_transcript(source, text, speaker, language)
        if recorder:
            recorder.close()
        stop_worker()
        # ponytail: skip pa.terminate()/stdin.close() — they can deadlock on live
        # streams or a full pipe and hang Quit; hard-exit after cleanup instead
        os._exit(0)


def self_test():
    stereo = np.array([1000, 3000, -2000, -4000], dtype=np.int16).tobytes()
    mono = downmix(stereo, 2)
    assert mono.shape == (2,) and abs(mono[0] - 2000 / 32768.0) < 1e-6
    assert np.frombuffer(base64.b64decode(base64.b64encode(mono.tobytes())), dtype=np.float32).shape == (2,)
    assert rms_db(np.zeros(4, dtype=np.float32)) == -90.0
    assert abs(rms_db(np.ones(4, dtype=np.float32)) - 0.0) < 1e-6

    # --source auto: the speaker takes the channel while it has voice and holds it for the hangover
    gate = AudioGate()
    loud = np.full(1600, 0.5, dtype=np.float32)
    quiet = np.zeros(1600, dtype=np.float32)
    assert gate.process("mic", 16000, loud) is not None   # nothing on the speaker yet
    assert gate.process("sys", 16000, loud) is not None    # speaker has voice, so it wins
    assert gate.selected == "sys" and gate.process("mic", 16000, loud) is None
    gate.last_system_voice -= AudioGate.HANGOVER + 0.1     # let the hangover lapse
    assert gate.process("mic", 16000, loud) is not None
    assert gate.process("sys", 16000, quiet) is None        # a silent speaker cannot take it back
    # resampling to 16 kHz carries the fractional read position across calls, so no drift builds up
    resampler = AudioGate()
    block = np.zeros(4000, dtype=np.float32)
    counts = [len(resampler.process("mic", 44100, block)) for _ in range(4)]
    assert abs(sum(counts) - 4 * len(block) * AudioGate.TARGET_RATE / 44100) <= 1, counts

    # model dropdown: built-in backends first, then the HF ids, deduped and long names shortened
    assert split_models("a, b ,,c") == split_models(["a", " b", "", "c"]) == ["a", "b", "c"]
    assert choice_id("hf", "Qwen/Qwen3-ASR-0.6B") == "hf:Qwen/Qwen3-ASR-0.6B"
    assert choice_id("sherpa", "Qwen/Qwen3-ASR-0.6B") == "sherpa"

    # streaming vs offline: the name decides unless stream:/offline: says otherwise
    assert hf_choice("nvidia/nemotron-3.5-asr-streaming-0.6b")[0] == "hf-stream"
    assert hf_choice("openai/whisper-large-v3-turbo")[0] == "hf"
    assert hf_choice("stream:some/model") == ("hf-stream", "some/model")
    assert hf_choice("offline:nvidia/nemotron-3.5-asr-streaming-0.6b")[0] == "hf"

    choices = model_choices("hf", "Qwen/Qwen3-ASR-0.6B",
                            ["Qwen/Qwen3-ASR-0.6B", "openai/whisper-large-v3-turbo",
                             "nvidia/nemotron-3.5-asr-streaming-0.6b"])
    assert [c["id"] for c in choices] == ["sherpa", "wsl-vllm", "hf:Qwen/Qwen3-ASR-0.6B",
                                          "hf:openai/whisper-large-v3-turbo",
                                          "hf-stream:nvidia/nemotron-3.5-asr-streaming-0.6b"]
    assert [c["label"] for c in choices[2:]] == ["Qwen3-ASR-0.6B", "whisper-large-v…", "nemotron-3.5-as…"]
    assert choices[4]["menu"].endswith("(streaming)") and choices[3]["menu"].endswith("(chunked)")
    # --asr wins over the name, so the same id can sit in the list on both paths at once
    forced = model_choices("hf-stream", "openai/whisper-large-v3-turbo", ["openai/whisper-large-v3-turbo"])
    assert [c["id"] for c in forced[2:]] == ["hf-stream:openai/whisper-large-v3-turbo",
                                             "hf:openai/whisper-large-v3-turbo"]

    # overlay text model: partial lines are rewritten in place, finals become history
    overlay = Overlay("mic", 120, 1.0, lambda: None,
                      choices=choices, current="sherpa", on_model=lambda _cid: None)
    overlay.root.withdraw()
    assert overlay.model_btn.cget("text") == "Sherpa"
    overlay.set_model_label("Qwen3-ASR-0.6B", "hf:Qwen/Qwen3-ASR-0.6B")
    assert overlay.model_choice.get() == "hf:Qwen/Qwen3-ASR-0.6B"
    overlay.set_status("Listening...")
    overlay.update("hello", False)
    overlay.update("hello world", True)
    overlay.update("next", False)
    body = overlay.text.get("1.0", "end-1c")
    assert body == "(microphone) Listening...\n(microphone) hello world\n(microphone) next", repr(body)
    overlay.level = -12.34
    overlay.show_debug()
    assert overlay.text.get("3.0", "end-1c") == "(microphone) -12.3 dB  next"

    # collapsing pins the pill to the window's bottom-right corner and expanding restores it
    expanded, corner = (overlay.left, overlay.top), (overlay.left + overlay.width,
                                                     overlay.top + overlay.height)
    overlay.toggle()
    assert overlay.collapsed
    assert (overlay.left + overlay.collapsed_width, overlay.top + overlay.bar_height) == corner
    overlay.toggle()
    assert not overlay.collapsed and (overlay.left, overlay.top) == expanded

    # the drag clamp keeps MIN_VISIBLE reachable instead of losing the window off-screen
    assert overlay._clamp(-99999, -99999, overlay.width, overlay.height) == (
        overlay._px(overlay.MIN_VISIBLE) - overlay.width,
        overlay._px(overlay.MIN_VISIBLE) - overlay.height)
    overlay.root.destroy()

    # --source auto: the same single pane, every line prefixed with the channel the gate picked
    auto = Overlay("auto", 120, 1.0, lambda: None, choices=choices, current="sherpa")
    auto.root.withdraw()
    auto.set_status("Listening...")  # a status belongs to the pane, so it stays unprefixed
    auto.update("who is speaking", True, speaker="sys")
    auto.update("i am", True, speaker="mic")
    body = auto.text.get("1.0", "end-1c")
    assert body == "Listening...\n(speaker) who is speaking\n(microphone) i am", repr(body)
    auto.level = -30.0
    auto.show_debug()  # the meter follows the same channel as the last line
    assert auto.text.get("4.0", "end-1c") == "(microphone) -30.0 dB  "
    auto.root.destroy()
    print("self-test ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        main()
