#!/usr/bin/env python3
"""
Minimal live subtitles for macOS.

Captures microphone and/or system audio, streams it to Apple Speech, shows a
floating subtitle window, and appends finalized lines to transcripts/YYYY-MM-DD.txt.

System audio needs a virtual input such as BlackHole. Use:
    bash scripts/start.sh --source both
"""

import argparse
import ctypes
import ctypes.util
import os
import signal
import sys
import threading
from datetime import datetime

import AppKit
import Foundation
import numpy as np
import objc
import sounddevice as sd


SAMPLE_RATE = 16_000
LANGUAGE_ALIASES = {
    "zh": "zh-CN",
    "cn": "zh-CN",
    "chinese": "zh-CN",
    "en": "en-US",
    "english": "en-US",
}


def locale_id(language):
    if not language:
        return "zh-CN"
    # ponytail: Apple Speech is single-locale; keep zh-CN as the mixed zh/en default.
    if language in {"auto", "mixed", "zh+en", "en+zh"}:
        return "zh-CN"
    return LANGUAGE_ALIASES.get(language.lower(), language)


def list_input_devices():
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            marker = " <- default" if index == sd.default.device[0] else ""
            print(f"{index}: {device['name']}{marker}")


def find_system_device(name=None):
    needle = (name or "blackhole").lower()
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and needle in device["name"].lower():
            return index
    return None


def ensure_speech_permission():
    import Speech

    done = threading.Event()
    status_box = {}

    def handler(status):
        status_box["status"] = int(status)
        done.set()

    Speech.SFSpeechRecognizer.requestAuthorization_(handler)
    done.wait(10)

    authorized = getattr(Speech, "SFSpeechRecognizerAuthorizationStatusAuthorized", 3)
    if status_box.get("status") != int(authorized):
        raise RuntimeError(
            "Speech Recognition permission is required. Enable it in "
            "System Settings > Privacy & Security > Speech Recognition."
        )


class AppleSpeechTranscriber:
    def __init__(self, callback, device=None, language="zh-CN"):
        import AVFoundation
        import Speech

        self.callback = callback
        self.device = device
        self.locale = locale_id(language)
        self._AV = AVFoundation
        self._Speech = Speech
        self._running = False
        self._request = None
        self._task = None
        self._last_text = ""
        self._lock = threading.Lock()

        ns_locale = Foundation.NSLocale.alloc().initWithLocaleIdentifier_(self.locale)
        self._recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(ns_locale)
        if not self._recognizer or not self._recognizer.isAvailable():
            raise RuntimeError(f"Apple Speech is unavailable for locale {self.locale}")

        self._format = AVFoundation.AVAudioFormat.alloc().initWithCommonFormat_sampleRate_channels_interleaved_(
            AVFoundation.AVAudioPCMFormatFloat32,
            float(SAMPLE_RATE),
            1,
            False,
        )

    def start(self):
        self._running = True
        self._start_session()
        threading.Thread(target=self._capture_loop, daemon=True).start()

    def stop(self):
        self._running = False
        with self._lock:
            if self._task:
                self._task.cancel()
            if self._request:
                self._request.endAudio()

    def _start_session(self):
        with self._lock:
            self._request = self._Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
            self._request.setShouldReportPartialResults_(True)
            if self._recognizer.supportsOnDeviceRecognition():
                self._request.setRequiresOnDeviceRecognition_(True)
            self._last_text = ""
            self._task = self._recognizer.recognitionTaskWithRequest_resultHandler_(
                self._request, self._on_result
            )

    def _restart_session(self):
        with self._lock:
            if self._task:
                self._task.cancel()
            if self._request:
                self._request.endAudio()
            self._task = None
            self._request = None
        if self._running:
            self._start_session()

    def _on_result(self, result, error):
        if not self._running:
            return
        if error:
            self._restart_session()
            return
        if not result:
            return

        text = str(result.bestTranscription().formattedString()).strip()
        is_final = bool(result.isFinal())
        if text and (is_final or text != self._last_text):
            self.callback(text, is_final)
            self._last_text = text
        if is_final:
            self._restart_session()

    def _capture_loop(self):
        objc_lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc_lib.sel_registerName.restype = ctypes.c_void_p
        objc_lib.sel_registerName.argtypes = [ctypes.c_char_p]
        float_channel_data = objc_lib.sel_registerName(b"floatChannelData")
        msg_send = ctypes.CFUNCTYPE(
            ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(("objc_msgSend", objc_lib))

        block_size = int(SAMPLE_RATE * 0.1)
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=block_size,
                device=self.device,
            ) as stream:
                while self._running:
                    data, overflowed = stream.read(block_size)
                    if overflowed:
                        continue
                    samples = np.ascontiguousarray(data.reshape(-1), dtype=np.float32)
                    buffer = self._AV.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(
                        self._format,
                        len(samples),
                    )
                    buffer.setFrameLength_(len(samples))
                    channel_data = msg_send(objc.pyobjc_id(buffer), float_channel_data)
                    ctypes.memmove(channel_data[0], samples.ctypes.data, len(samples) * 4)
                    with self._lock:
                        if self._request:
                            self._request.appendAudioPCMBuffer_(buffer)
        except Exception as exc:
            self.callback(f"[audio error: {exc}]", True)


class SubtitleApp(AppKit.NSObject):
    def init(self):
        self = objc.super(SubtitleApp, self).init()
        if self is None:
            return None
        self.output_dir = None
        self.source = "mic"
        self.language = "zh-CN"
        self.system_device = None
        self.opacity = 0.75
        self.height = 120
        self.window = None
        self.regions = {}
        self.transcribers = []
        return self

    @objc.python_method
    def configure(self, args):
        self.output_dir = args.output_dir
        self.source = args.source
        self.language = args.language
        self.system_device = args.system_device_id
        self.opacity = args.opacity
        self.height = max(args.height, 190) if args.source == "both" else args.height
        os.makedirs(self.output_dir, exist_ok=True)

    @objc.python_method
    def sources(self):
        if self.source == "both":
            return ["mic", "sys"]
        return ["sys"] if self.source == "system" else ["mic"]

    def applicationDidFinishLaunching_(self, _notification):
        self._setup_window()
        for source in self.sources():
            self._append_status(source, "Listening...\n")
        threading.Thread(target=self._start_transcribers, daemon=True).start()

    @objc.python_method
    def _setup_window(self):
        screen = AppKit.NSScreen.mainScreen().frame()
        margin = 50
        rect = Foundation.NSMakeRect(
            screen.origin.x + margin,
            screen.origin.y + margin,
            screen.size.width - margin * 2,
            self.height,
        )
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.window.setLevel_(AppKit.NSFloatingWindowLevel)
        self.window.setOpaque_(False)
        self.window.setIgnoresMouseEvents_(True)
        self.window.setHasShadow_(False)
        self.window.setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0,
                0,
                0,
                self.opacity,
            )
        )
        self.window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )

        content = self.window.contentView()
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(8)
        content.layer().setMasksToBounds_(True)

        active = self.sources()
        if len(active) == 1:
            self._add_region(content, active[0], content.bounds())
        else:
            bounds = content.bounds()
            pad = 10
            gap = 6
            half = (bounds.size.height - pad * 2 - gap) / 2
            self._add_region(
                content,
                "sys",
                Foundation.NSMakeRect(pad, pad, bounds.size.width - pad * 2, half),
            )
            self._add_region(
                content,
                "mic",
                Foundation.NSMakeRect(
                    pad,
                    pad + half + gap,
                    bounds.size.width - pad * 2,
                    half,
                ),
            )

        self.window.orderFront_(None)

    @objc.python_method
    def _add_region(self, content, source, rect):
        if len(self.sources()) == 1:
            rect = Foundation.NSMakeRect(10, 5, rect.size.width - 20, rect.size.height - 10)
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(rect)
        scroll.setHasVerticalScroller_(False)
        scroll.setHasHorizontalScroller_(False)
        scroll.setDrawsBackground_(False)

        text = AppKit.NSTextView.alloc().initWithFrame_(rect)
        text.setEditable_(False)
        text.setSelectable_(False)
        text.setDrawsBackground_(False)
        text.setTextColor_(AppKit.NSColor.whiteColor())
        text.setFont_(AppKit.NSFont.monospacedSystemFontOfSize_weight_(14, AppKit.NSFontWeightRegular))
        text.textContainer().setWidthTracksTextView_(True)
        scroll.setDocumentView_(text)
        content.addSubview_(scroll)

        self.regions[source] = {
            "text": text,
            "line_start": 0,
            "date": None,
            "file": None,
        }

    @objc.python_method
    def _start_transcribers(self):
        for source in self.sources():
            device = self.system_device if source == "sys" else None
            try:
                transcriber = AppleSpeechTranscriber(
                    self._callback(source),
                    device=device,
                    language=self.language,
                )
                self.transcribers.append(transcriber)
                transcriber.start()
            except Exception as exc:
                self._post(source, f"[start error: {exc}]", True)

    @objc.python_method
    def _callback(self, source):
        def callback(text, is_final):
            self._post(source, text, is_final)

        return callback

    @objc.python_method
    def _post(self, source, text, is_final):
        payload = Foundation.NSDictionary.dictionaryWithDictionary_(
            {"source": source, "text": text, "is_final": is_final}
        )
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"handleText:",
            payload,
            False,
        )

    @objc.typedSelector(b"v@:@")
    def handleText_(self, payload):
        source = str(payload["source"])
        text = str(payload["text"])
        is_final = bool(payload["is_final"])
        region = self.regions.get(source)
        if not region:
            return
        if is_final:
            self._finalize(region, source, text)
        else:
            self._partial(region, source, text)

    @objc.python_method
    def _prefix(self, source):
        return f"[{source.upper()}] " if len(self.regions) > 1 else ""

    @objc.python_method
    def _partial(self, region, source, text):
        self._replace_current_line(
            region,
            self._prefix(source) + text,
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.72, 0.72, 0.72, 1),
        )

    @objc.python_method
    def _finalize(self, region, source, text):
        line = self._prefix(source) + text + "\n"
        self._replace_current_line(region, line, AppKit.NSColor.whiteColor())
        region["line_start"] = region["text"].textStorage().length()
        self._write(source, text)

    @objc.python_method
    def _replace_current_line(self, region, text, color):
        text_view = region["text"]
        storage = text_view.textStorage()
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.monospacedSystemFontOfSize_weight_(
                14,
                AppKit.NSFontWeightRegular,
            ),
            AppKit.NSForegroundColorAttributeName: color,
        }
        attr = Foundation.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        start = region["line_start"]
        storage.replaceCharactersInRange_withAttributedString_(
            Foundation.NSMakeRange(start, storage.length() - start),
            attr,
        )
        text_view.scrollRangeToVisible_(Foundation.NSMakeRange(storage.length(), 0))

    @objc.python_method
    def _append_status(self, source, text):
        region = self.regions[source]
        self._replace_current_line(
            region,
            text,
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(1, 0.85, 0, 1),
        )
        region["line_start"] = region["text"].textStorage().length()

    @objc.python_method
    def _write(self, source, text):
        today = datetime.now().strftime("%Y-%m-%d")
        suffix = "-sys" if source == "sys" else ""
        path = os.path.join(self.output_dir, f"{today}{suffix}.txt")
        region = self.regions[source]
        if region["date"] != today:
            if region["file"]:
                region["file"].close()
            region["file"] = open(path, "a", encoding="utf-8")
            region["date"] = today
        timestamp = datetime.now().strftime("%H:%M:%S")
        region["file"].write(f"[{timestamp}] {text}\n")
        region["file"].flush()


def parse_args():
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Minimal real-time subtitles")
    parser.add_argument("--source", choices=["mic", "system", "both"], default="mic")
    parser.add_argument("--system-device", help="input device name for system audio, e.g. BlackHole")
    parser.add_argument("--language", default="zh-CN", help="zh, en, zh-CN, en-US; mixed defaults to zh-CN")
    parser.add_argument("--output-dir", default=os.path.join(project_dir, "transcripts"))
    parser.add_argument("--opacity", type=float, default=0.75)
    parser.add_argument("--height", type=int, default=120)
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()
    args.language = locale_id(args.language)
    args.opacity = max(0.1, min(1.0, args.opacity))
    args.height = max(60, min(400, args.height))
    args.system_device_id = None
    return args


def main():
    args = parse_args()
    if args.list_devices:
        list_input_devices()
        return

    if args.source in {"system", "both"}:
        args.system_device_id = find_system_device(args.system_device)
        if args.system_device_id is None:
            print("No system audio input found. Install BlackHole or pass --system-device.", file=sys.stderr)
            print("\nInput devices:", file=sys.stderr)
            list_input_devices()
            sys.exit(1)

    ensure_speech_permission()

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    delegate = SubtitleApp.alloc().init()
    delegate.configure(args)
    app.setDelegate_(delegate)

    signal.signal(signal.SIGINT, lambda *_: os._exit(0))
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))

    print(f"Subtitles running: source={args.source}, language={args.language}")
    print(f"Writing transcripts to: {args.output_dir}")
    app.run()


if __name__ == "__main__":
    main()
