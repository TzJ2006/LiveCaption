#!/usr/bin/env python3
"""Windows host for LiveCaption: mic and/or speaker output -> ASR worker -> caption overlay.

Speaker capture uses WASAPI loopback (no virtual audio driver needed).
Reuses the same NDJSON workers as the macOS Swift host (hf_asr_worker.py / sherpa_asr_worker.py);
the Overlay class below is a port of SubtitleWindow in src/swift/LiveSubtitle.swift -- same
geometry, colours, controls and text model, so both hosts look and behave the same.

Usage:
  python src/python/win_host.py --source both --asr hf --hf-model Qwen/Qwen3-ASR-0.6B [--record]

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
CAPTION_FAMILY = "Consolas"
BUTTON_FAMILY = "Segoe UI"
PREFIXES = {"mic": "(microphone) ", "sys": "(speaker) ", "mixed": "(meeting) "}
RECORD_LABELS = {"mic": "microphone", "sys": "speaker", "mixed": "meeting"}


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
    if args.asr == "hf" and not args.hf_model:
        sys.exit("--asr hf requires --hf-model <huggingface/model-id>")
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

    Borderless always-on-top strip pinned to the bottom of the screen: sys | mic columns above a
    34pt control bar holding a drag handle, Hide and Quit. Each source keeps an append-only
    history; the current line is rewritten in place (gray while partial, white once final) and
    prefixed with (speaker)/(microphone). Scrolling only follows the tail when the view is already
    at the bottom. Dragging the handle moves the window; Hide collapses it to a pill that keeps
    only the control bar, anchored at the window's bottom-right corner.
    """

    # layoutControls()/collapsedWidth in Swift; macOS points, scaled through _px()
    BAR_H = 34
    BTN_W, BTN_H = 52, 22
    HANDLE = 22
    GAP, PAD = 6, 6
    MIN_VISIBLE = 24  # DragHandleView.clampedOrigin keeps this much of the window reachable
    COLLAPSED_W = PAD + HANDLE + GAP + BTN_W + GAP + BTN_W + PAD

    def __init__(self, sources, height, opacity, on_quit):
        self.sources, self.on_quit = sources, on_quit
        self.collapsed = False
        self.drag_from = None
        self.line_starts, self.places = {}, {}
        self.live_texts, self.live_colors, self.debug_prefixes, self.levels = {}, {}, {}, {}
        self.texts = {}

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
        if len(sources) == 2:
            gap = self._px(8)
            half = (w - gap) // 2
            self._add_region(sources[0], x, y, half, h)
            self._add_region(sources[1], x + half + gap, y, half, h)
        else:
            self._add_region(sources[0], x, y, w, h)

        style = dict(fg="white", font=(BUTTON_FAMILY, -self._px(11), "bold"), bd=0, relief="flat",
                     highlightthickness=0, activeforeground="white", cursor="hand2")
        self.quit_btn = tk.Button(root, text="Quit", bg="#9e1f26", activebackground="#b53039",
                                  command=self.quit, **style)
        self.hide_btn = tk.Button(root, text="Hide", bg="#1a5780", activebackground="#26709f",
                                  command=self.toggle, **style)
        self.handle = self._make_handle()
        self._layout_controls(self.width, self.height)

        root.bind("<Escape>", lambda _e: self.quit())
        root.bind_all("<Control-c>", self._copy)
        root.bind_all("<MouseWheel>", self._wheel)

    def _px(self, points):
        """macOS point -> physical pixel on this display."""
        return round(points * self.scale)

    def _add_region(self, source, x, y, w, h):
        txt = tk.Text(self.root, bg="black", fg=WHITE, font=self.caption_font, wrap="word", bd=0,
                      padx=0, pady=0, highlightthickness=0, cursor="arrow", insertwidth=0,
                      state="disabled")
        for color in (WHITE, LIVE, YELLOW, GREEN, ORANGE):
            txt.tag_config(color, foreground=color)
        self.places[source] = dict(x=x, y=y, width=w, height=h)
        txt.place(**self.places[source])
        self.texts[source] = txt
        self.line_starts[source] = "1.0"

    # --- text model (replaceLine / setStatus / update / showDebug in Swift) ---

    def _prefix(self, source):
        return PREFIXES.get(source, "")

    def _replace_line(self, text, color, source):
        """Rewrite everything after the last committed line, Swift's replaceLine()."""
        txt = self.texts.get(source)
        if txt is None:
            return
        follow = txt.yview()[1] > 0.99  # isNearBottom()
        start = self.line_starts[source]
        txt.configure(state="normal")
        txt.delete(start, "end")
        # ponytail: separator leads the pending line — Tk owns the trailing newline and would
        # swallow a committed one on the next delete-to-end
        txt.insert(start, ("" if start == "1.0" else "\n") + text, color)
        txt.configure(state="disabled")
        if follow:
            txt.see("end-1c")

    def _commit_line(self, source):
        self.line_starts[source] = self.texts[source].index("end-1c")

    def set_status(self, text, source):
        self._replace_line(self._prefix(source) + text, YELLOW, source)
        self._commit_line(source)

    def update(self, text, source, final):
        if source not in self.texts:
            return
        color = WHITE if final else LIVE
        self.live_texts[source], self.live_colors[source] = text, color
        prefix = self.debug_prefixes.get(source, self._prefix(source))
        self._replace_line(f"{prefix}{text}", color, source)
        if final:
            self._commit_line(source)
            self.live_texts.pop(source, None)
            self.live_colors.pop(source, None)

    def show_debug(self):
        for source in self.sources:
            level = f"{self.levels[source]:.1f} dB" if source in self.levels else "waiting"
            prefix = f"{self._prefix(source)}{level}  "
            self.debug_prefixes[source] = prefix
            if source in self.live_texts:
                self._replace_line(prefix + self.live_texts[source],
                                   self.live_colors.get(source, WHITE), source)
            else:
                self._replace_line(prefix, GREEN, source)

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

    def _layout_controls(self, width, height):
        """layoutControls(in:) -- handle, Hide and Quit right-aligned inside the control bar."""
        y = height - self._px(self.PAD + self.BTN_H)
        quit_x = width - self._px(self.PAD + self.BTN_W)
        hide_x = quit_x - self._px(self.GAP + self.BTN_W)
        box = dict(y=y, width=self._px(self.BTN_W), height=self._px(self.BTN_H))
        self.quit_btn.place(x=quit_x, **box)
        self.hide_btn.place(x=hide_x, **box)
        self.handle.place(x=hide_x - self._px(self.GAP + self.HANDLE), y=y,
                          width=self._px(self.HANDLE), height=self._px(self.HANDLE))

    def toggle(self):
        """toggleVisibility(): collapse to a pill holding just the control bar."""
        width, height = self._current_size()
        self.collapsed = not self.collapsed
        self.hide_btn.config(text="Show" if self.collapsed else "Hide")
        new_width, new_height = self._current_size()
        for source, txt in self.texts.items():
            txt.place_forget() if self.collapsed else txt.place(**self.places[source])
        # the bottom-right corner stays put, so the pill grows back up and to the left
        self._apply_geometry(self.left + width - new_width, self.top + height - new_height,
                             new_width, new_height)
        self._layout_controls(new_width, new_height)

    def quit(self):
        for source in self.sources:
            self._replace_line("Stopping LiveCaption...", ORANGE, source)
        self.on_quit()

    def _copy(self, _event=None):
        value = None
        for txt in self.texts.values():
            if txt.tag_ranges("sel"):
                value = txt.get("sel.first", "sel.last")
                break
        if value is None:
            value = self.texts[self.sources[0]].get("1.0", "end-1c")
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
            texts.put(("__level__", source, rms_db(floats)))
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

    # transcripts, same layout as the Swift host (date resolved per line, so a run can cross midnight)
    os.makedirs(args.output_dir, exist_ok=True)
    pending = {}

    def append_transcript(source, text):
        day = datetime.date.today().isoformat()
        suffix = "-sys" if source == "sys" else ""
        with open(os.path.join(args.output_dir, f"{day}{suffix}.txt"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {text}\n")

    def read_worker():
        for line in worker.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("status") == "ready":
                print(f"worker ready: {event.get('device', '')}", file=sys.stderr)
                continue
            text, source = event.get("text", "").strip(), event.get("source", "mic")
            if not text:
                continue
            final = bool(event.get("final"))
            texts.put((source, text, final))
            if final:
                append_transcript(source, text)
                pending.pop(source, None)
            else:
                pending[source] = text
        stop.set()

    threading.Thread(target=read_worker, daemon=True).start()

    sources = ["sys", "mic"] if args.source == "both" else ["mic"] if args.source == "mic" else ["sys"]
    overlay = Overlay(sources, args.height, args.opacity, stop.set)
    if args.debug:
        overlay.show_debug()
    else:
        for source in sources:
            overlay.set_status("Listening...", source)
    overlay.root.after(0, overlay.root.focus_force)

    last_debug_draw = [0.0]

    def poll():
        while not texts.empty():
            source, text, final = texts.get()
            if source == "__level__":
                overlay.levels[text] = final
                now = time.monotonic()
                if now - last_debug_draw[0] >= 0.15:  # same redraw throttle as the Swift host
                    last_debug_draw[0] = now
                    overlay.show_debug()
                continue
            overlay.update(text, source, final)
        if stop.is_set():
            overlay.root.destroy()
            return
        overlay.root.after(100, poll)

    overlay.root.after(100, poll)
    try:
        overlay.root.mainloop()
    finally:
        stop.set()
        for source, text in pending.items():  # applicationWillTerminate() flushes partials too
            append_transcript(source, text)
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
    assert rms_db(np.zeros(4, dtype=np.float32)) == -90.0
    assert abs(rms_db(np.ones(4, dtype=np.float32)) - 0.0) < 1e-6

    # overlay text model: partial lines are rewritten in place, finals become history
    overlay = Overlay(["mic"], 120, 1.0, lambda: None)
    overlay.root.withdraw()
    overlay.set_status("Listening...", "mic")
    overlay.update("hello", "mic", False)
    overlay.update("hello world", "mic", True)
    overlay.update("next", "mic", False)
    body = overlay.texts["mic"].get("1.0", "end-1c")
    assert body == "(microphone) Listening...\n(microphone) hello world\n(microphone) next", repr(body)
    overlay.levels["mic"] = -12.34
    overlay.show_debug()
    assert overlay.texts["mic"].get("3.0", "end-1c") == "(microphone) -12.3 dB  next"

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
    print("self-test ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        main()
