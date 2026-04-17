#!/usr/bin/env python3
"""
Meeting Helper — 实时字幕浮动窗口

支持多种 ASR 后端，通过 --model 参数切换：
  zipformer   — Sherpa-ONNX 流式 Zipformer (默认)
  paraformer  — Sherpa-ONNX 流式 Paraformer
  qwen3-asr   — Qwen3-ASR-0.6B (torch/transformers)
  whisper     — Faster-Whisper large-v3-turbo (CTranslate2)
  moonshine   — Moonshine v2 (事件驱动流式)
  voxtral     — Voxtral-Mini-4B-Realtime (MLX, chunked)

音频源（--source 参数）：
  mic      仅麦克风 (默认)
  system   仅系统音频 (需 BlackHole 等虚拟音频设备)
  both     麦克风 + 系统音频同时录制

用法:
    python3 scripts/live-subtitle.py --model zipformer [选项]
    python3 scripts/live-subtitle.py --source both
    python3 scripts/live-subtitle.py --source system --system-device "BlackHole 2ch"

快捷键:
    Cmd+Shift+S  显示/隐藏字幕窗口
    Ctrl+C       退出
"""

import argparse
import os
import signal
import sys
import threading
import time as _time
from datetime import datetime

import numpy as np
import sounddevice as sd

import AppKit
import Foundation
import objc


MODELS = {
    "zipformer": "Sherpa-ONNX Zipformer (streaming, zh+en)",
    "paraformer": "Sherpa-ONNX Paraformer (streaming, zh+en)",
    "qwen3-asr": "Qwen3-ASR-0.6B (chunked, 52 languages)",
    "whisper": "Faster-Whisper large-v3-turbo (chunked, 99 languages)",
    "moonshine": "Moonshine v2 (streaming, 8 languages)",
    "voxtral": "Voxtral-Mini-4B-Realtime (chunked, 13 languages)",
}

HEAVY_MODELS = {"whisper", "voxtral", "qwen3-asr"}


# ── ASR 后端基类 ──────────────────────────────────────────────────────────────

class BaseTranscriber:
    """所有后端共享的接口。"""

    def __init__(self, callback, sample_rate=16000, device=None):
        self.sample_rate = sample_rate
        self.callback = callback
        self.device = device
        self._running = False
        self.device_name = "cpu"

    def start(self):
        raise NotImplementedError

    def stop(self):
        self._running = False


# ── Sherpa-ONNX 流式后端（Zipformer / Paraformer 共用录音循环）────────────────

class SherpaTranscriber(BaseTranscriber):
    """Sherpa-ONNX 真正的流式 ASR：逐帧解码，逐词输出。"""

    def __init__(self, callback, sample_rate=16000, model_type="zipformer",
                 device=None):
        super().__init__(callback, sample_rate, device)
        import sherpa_onnx

        models_base = os.path.expanduser("~/.meeting-helper/models")

        if model_type == "zipformer":
            model_dir = os.path.join(
                models_base,
                "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
            )
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                encoder=os.path.join(model_dir, "encoder-epoch-99-avg-1.onnx"),
                decoder=os.path.join(model_dir, "decoder-epoch-99-avg-1.onnx"),
                joiner=os.path.join(model_dir, "joiner-epoch-99-avg-1.onnx"),
                tokens=os.path.join(model_dir, "tokens.txt"),
                num_threads=4,
                sample_rate=sample_rate,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=2.4,
                rule2_min_trailing_silence=1.2,
                rule3_min_utterance_length=20.0,
            )
        elif model_type == "paraformer":
            model_dir = os.path.join(
                models_base,
                "sherpa-onnx-streaming-paraformer-bilingual-zh-en",
            )
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_paraformer(
                encoder=os.path.join(model_dir, "encoder.int8.onnx"),
                decoder=os.path.join(model_dir, "decoder.int8.onnx"),
                tokens=os.path.join(model_dir, "tokens.txt"),
                num_threads=4,
                sample_rate=sample_rate,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=2.4,
                rule2_min_trailing_silence=1.2,
                rule3_min_utterance_length=20.0,
            )
        else:
            raise ValueError(f"Unknown sherpa model type: {model_type}")

        self.stream = self.recognizer.create_stream()
        self.device_name = "cpu (sherpa-onnx)"

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        """音频捕获 + 流式识别循环。"""
        block_size = int(self.sample_rate * 0.1)  # 100ms per frame
        last_text = ""

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=block_size,
                device=self.device,
            ) as mic:
                while self._running:
                    data, overflowed = mic.read(block_size)
                    if overflowed:
                        continue

                    samples = data.flatten()
                    self.stream.accept_waveform(self.sample_rate, samples)

                    while self.recognizer.is_ready(self.stream):
                        self.recognizer.decode_stream(self.stream)

                    text = self.recognizer.get_result(self.stream).strip()
                    is_endpoint = self.recognizer.is_endpoint(self.stream)

                    if is_endpoint and text:
                        self.callback(text, True)
                        self.recognizer.reset(self.stream)
                        last_text = ""
                    elif text and text != last_text:
                        self.callback(text, False)
                        last_text = text
                    elif is_endpoint:
                        self.recognizer.reset(self.stream)
                        last_text = ""

        except Exception as e:
            self.callback(f"[Error: {e}]", True)


# ── Qwen3-ASR 后端（chunked 转录）────────────────────────────────────────────

class QwenASRTranscriber(BaseTranscriber):
    """Qwen3-ASR：累积音频 + 定期转录（非真正流式）。"""

    def __init__(self, callback, sample_rate=16000,
                 model_name="Qwen/Qwen3-ASR-0.6B", device=None):
        super().__init__(callback, sample_rate, device)
        import torch
        from qwen_asr import Qwen3ASRModel

        if torch.backends.mps.is_available():
            compute_device = "mps"
            dtype = torch.float16
        else:
            compute_device = "cpu"
            dtype = torch.float32
        self.device_name = compute_device

        self.model = Qwen3ASRModel.from_pretrained(
            model_name,
            dtype=dtype,
            device_map=compute_device,
            max_inference_batch_size=1,
            max_new_tokens=512,
        )

        self._buffer = []
        self._buffer_lock = threading.Lock()
        self._silence_frames = 0
        self._has_speech = False
        self._should_finalize = False

        self.silence_threshold = 0.008
        self.silence_finalize = 1.5
        self.transcribe_interval = 2.0
        self.min_audio_len = 0.5

    def start(self):
        self._running = True
        threading.Thread(target=self._capture_loop, daemon=True).start()
        threading.Thread(target=self._transcribe_loop, daemon=True).start()

    def _capture_loop(self):
        block_size = int(self.sample_rate * 0.1)
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=block_size,
                device=self.device,
            ) as mic:
                while self._running:
                    data, overflowed = mic.read(block_size)
                    if overflowed:
                        continue
                    samples = data.flatten()
                    rms = float(np.sqrt(np.mean(samples ** 2)))
                    with self._buffer_lock:
                        self._buffer.append(samples)
                    if rms >= self.silence_threshold:
                        self._has_speech = True
                        self._silence_frames = 0
                    else:
                        self._silence_frames += 1
                    if self._has_speech and self._silence_frames * 0.1 >= self.silence_finalize:
                        self._should_finalize = True
        except Exception as e:
            self.callback(f"[Mic Error: {e}]", True)

    def _transcribe_loop(self):
        last_partial = ""
        while self._running:
            _time.sleep(self.transcribe_interval)
            should_finalize = self._should_finalize
            with self._buffer_lock:
                if not self._buffer:
                    continue
                audio = np.concatenate(self._buffer)
                if should_finalize:
                    self._buffer.clear()
                    self._should_finalize = False
                    self._has_speech = False
                    self._silence_frames = 0
            if len(audio) / self.sample_rate < self.min_audio_len:
                if should_finalize:
                    with self._buffer_lock:
                        self._buffer.clear()
                continue
            try:
                results = self.model.transcribe(
                    audio=[(audio, self.sample_rate)], language=None,
                )
                text = results[0].text.strip()
            except Exception as e:
                self.callback(f"[ASR Error: {e}]", True)
                continue
            if not text:
                if should_finalize:
                    last_partial = ""
                continue
            if should_finalize:
                self.callback(text, True)
                last_partial = ""
            elif text != last_partial:
                self.callback(text, False)
                last_partial = text


# ── Faster-Whisper 后端（chunked 转录）─────────────────────────────────────────

class WhisperTranscriber(BaseTranscriber):
    """Faster-Whisper：累积音频 + 定期转录（非真正流式）。"""

    def __init__(self, callback, sample_rate=16000,
                 model_size=None, device=None):
        super().__init__(callback, sample_rate, device)
        from faster_whisper import WhisperModel

        if model_size is None:
            model_size = os.environ.get("WHISPER_MODEL_SIZE", "large-v3-turbo")

        self.model = WhisperModel(
            model_size, device="cpu", compute_type="int8",
        )
        self.device_name = "cpu (faster-whisper)"

        self._buffer = []
        self._buffer_lock = threading.Lock()
        self._silence_frames = 0
        self._has_speech = False
        self._should_finalize = False

        self.silence_threshold = 0.008
        self.silence_finalize = 1.5
        self.transcribe_interval = 2.0
        self.min_audio_len = 0.5

    def start(self):
        self._running = True
        threading.Thread(target=self._capture_loop, daemon=True).start()
        threading.Thread(target=self._transcribe_loop, daemon=True).start()

    def _capture_loop(self):
        block_size = int(self.sample_rate * 0.1)
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=block_size,
                device=self.device,
            ) as mic:
                while self._running:
                    data, overflowed = mic.read(block_size)
                    if overflowed:
                        continue
                    samples = data.flatten()
                    rms = float(np.sqrt(np.mean(samples ** 2)))
                    with self._buffer_lock:
                        self._buffer.append(samples)
                    if rms >= self.silence_threshold:
                        self._has_speech = True
                        self._silence_frames = 0
                    else:
                        self._silence_frames += 1
                    if self._has_speech and self._silence_frames * 0.1 >= self.silence_finalize:
                        self._should_finalize = True
        except Exception as e:
            self.callback(f"[Mic Error: {e}]", True)

    def _transcribe_loop(self):
        last_partial = ""
        while self._running:
            _time.sleep(self.transcribe_interval)
            should_finalize = self._should_finalize
            with self._buffer_lock:
                if not self._buffer:
                    continue
                audio = np.concatenate(self._buffer)
                if should_finalize:
                    self._buffer.clear()
                    self._should_finalize = False
                    self._has_speech = False
                    self._silence_frames = 0
            if len(audio) / self.sample_rate < self.min_audio_len:
                if should_finalize:
                    with self._buffer_lock:
                        self._buffer.clear()
                continue
            try:
                segments, _ = self.model.transcribe(
                    audio, beam_size=5, language=None,
                    vad_filter=True,
                )
                text = "".join(seg.text for seg in segments).strip()
            except Exception as e:
                self.callback(f"[ASR Error: {e}]", True)
                continue
            if not text:
                if should_finalize:
                    last_partial = ""
                continue
            if should_finalize:
                self.callback(text, True)
                last_partial = ""
            elif text != last_partial:
                self.callback(text, False)
                last_partial = text


# ── Moonshine v2 后端（事件驱动流式）──────────────────────────────────────────

class MoonshineTranscriber(BaseTranscriber):
    """Moonshine v2：事件驱动流式识别（英语真流式，其他语言 base 模型）。

    内置麦克风捕获，通过 MOONSHINE_LANGUAGE 环境变量切换语言（默认 en）。
    支持语言: en, zh, ar, es, ja, ko, vi, uk
    """

    def __init__(self, callback, sample_rate=16000, language=None, device=None):
        super().__init__(callback, sample_rate, device)
        from moonshine_voice import (
            MicTranscriber as _MoonMic,
            TranscriptEventListener,
            get_model_for_language,
        )

        lang = language or os.environ.get("MOONSHINE_LANGUAGE", "en")
        model_path, model_arch = get_model_for_language(lang)

        self._mic = _MoonMic(
            model_path=model_path,
            model_arch=model_arch,
            samplerate=sample_rate,
            device=self.device,
        )
        self.device_name = f"cpu (moonshine-{lang})"

        outer = self

        class _Listener(TranscriptEventListener):
            def on_line_text_changed(self_, event):
                outer.callback(event.line.text, False)

            def on_line_completed(self_, event):
                outer.callback(event.line.text, True)

        self._listener = _Listener()
        self._mic.add_listener(self._listener)

    def start(self):
        self._running = True
        self._mic.start()

    def stop(self):
        self._running = False
        self._mic.stop()
        self._mic.close()


# ── Voxtral Realtime 后端（MLX 加速，chunked 转录）───────────────────────────

class VoxtralTranscriber(BaseTranscriber):
    """Voxtral-Mini-4B-Realtime：MLX 加速，累积音频 + 转录。

    通过 VOXTRAL_MODEL 环境变量切换模型（默认 4bit 量化版）。
    自动检测 13 种语言，无需手动指定。
    """

    def __init__(self, callback, sample_rate=16000, model_path=None,
                 device=None):
        super().__init__(callback, sample_rate, device)
        from voxmlx import load_model, _build_prompt_tokens
        from voxmlx.generate import generate as _vox_generate
        from mistral_common.tokens.tokenizers.tekken import SpecialTokenPolicy

        if model_path is None:
            model_path = os.environ.get(
                "VOXTRAL_MODEL",
                "mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit",
            )

        self._vox_model, self._sp, _ = load_model(model_path)
        self._prompt_tokens, self._n_delay = _build_prompt_tokens(self._sp)
        self._vox_generate = _vox_generate
        self._stp = SpecialTokenPolicy.IGNORE
        self.device_name = "mlx (voxtral)"

        self._buffer = []
        self._buffer_lock = threading.Lock()
        self._silence_frames = 0
        self._has_speech = False
        self._should_finalize = False

        self.silence_threshold = 0.008
        self.silence_finalize = 1.5
        self.transcribe_interval = 2.0
        self.min_audio_len = 0.5

    def start(self):
        self._running = True
        threading.Thread(target=self._capture_loop, daemon=True).start()
        threading.Thread(target=self._transcribe_loop, daemon=True).start()

    def _capture_loop(self):
        block_size = int(self.sample_rate * 0.1)
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=block_size,
                device=self.device,
            ) as mic:
                while self._running:
                    data, overflowed = mic.read(block_size)
                    if overflowed:
                        continue
                    samples = data.flatten()
                    rms = float(np.sqrt(np.mean(samples ** 2)))
                    with self._buffer_lock:
                        self._buffer.append(samples)
                    if rms >= self.silence_threshold:
                        self._has_speech = True
                        self._silence_frames = 0
                    else:
                        self._silence_frames += 1
                    if self._has_speech and self._silence_frames * 0.1 >= self.silence_finalize:
                        self._should_finalize = True
        except Exception as e:
            self.callback(f"[Mic Error: {e}]", True)

    def _transcribe_loop(self):
        last_partial = ""
        while self._running:
            _time.sleep(self.transcribe_interval)
            should_finalize = self._should_finalize
            with self._buffer_lock:
                if not self._buffer:
                    continue
                audio = np.concatenate(self._buffer)
                if should_finalize:
                    self._buffer.clear()
                    self._should_finalize = False
                    self._has_speech = False
                    self._silence_frames = 0
            if len(audio) / self.sample_rate < self.min_audio_len:
                if should_finalize:
                    with self._buffer_lock:
                        self._buffer.clear()
                continue
            try:
                text = self._transcribe_audio(audio)
            except Exception as e:
                self.callback(f"[ASR Error: {e}]", True)
                continue
            if not text:
                if should_finalize:
                    last_partial = ""
                continue
            if should_finalize:
                self.callback(text, True)
                last_partial = ""
            elif text != last_partial:
                self.callback(text, False)
                last_partial = text

    def _transcribe_audio(self, audio_np):
        """将 numpy 音频数组转录为文字。"""
        import soundfile as sf
        import tempfile

        tmp = tempfile.mktemp(suffix=".wav")
        try:
            sf.write(tmp, audio_np, self.sample_rate)
            output_tokens = self._vox_generate(
                self._vox_model, tmp, self._prompt_tokens,
                n_delay_tokens=self._n_delay, temperature=0.0,
                eos_token_id=self._sp.eos_id,
            )
            return self._sp.decode(
                output_tokens, special_token_policy=self._stp,
            )
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


# ── 工厂函数 + 设备检测 ─────────────────────────────────────────────────────

def create_transcriber(model, callback, sample_rate=16000, device=None):
    """根据 --model 参数创建对应的转录器。"""
    if model == "zipformer":
        return SherpaTranscriber(callback, sample_rate, model_type="zipformer",
                                 device=device)
    elif model == "paraformer":
        return SherpaTranscriber(callback, sample_rate, model_type="paraformer",
                                 device=device)
    elif model == "qwen3-asr":
        return QwenASRTranscriber(callback, sample_rate, device=device)
    elif model == "whisper":
        return WhisperTranscriber(callback, sample_rate, device=device)
    elif model == "moonshine":
        return MoonshineTranscriber(callback, sample_rate, device=device)
    elif model == "voxtral":
        return VoxtralTranscriber(callback, sample_rate, device=device)
    else:
        available = ", ".join(MODELS.keys())
        raise ValueError(f"Unknown model '{model}'. Available: {available}")


def detect_system_audio_device(preferred_name=None):
    """检测系统音频虚拟设备（BlackHole 等）。

    Args:
        preferred_name: 指定设备名（部分匹配）。None 则自动搜索 BlackHole。

    Returns:
        设备索引 (int) 或 None。
    """
    devices = sd.query_devices()
    search = preferred_name.lower() if preferred_name else "blackhole"
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and search in d["name"].lower():
            return i
    return None


# ── 字幕窗口 ────────────────────────────────────────────────────────────────

class SubtitleDelegate(AppKit.NSObject):
    """主应用代理：管理字幕窗口、ASR 转录、快捷键。"""

    def init(self):
        self = objc.super(SubtitleDelegate, self).init()
        if self is None:
            return None
        self._opacity = 0.75
        self._height = 120
        self._visible = True
        self._window = None
        self._output_dir = None
        self._model = None
        self._source_mode = "mic"
        self._system_device = None
        # Per-source regions: {key: {text_view, line_start, output_file, output_date}}
        self._regions = {}
        self._transcribers = {}
        return self

    @objc.python_method
    def configure_(self, config):
        self._opacity = config.get("opacity", 0.75)
        self._height = config.get("height", 120)
        self._model = config.get("model", "zipformer")
        self._output_dir = config.get("output_dir")
        self._source_mode = config.get("source", "mic")
        self._system_device = config.get("system_device")
        if self._output_dir:
            os.makedirs(self._output_dir, exist_ok=True)
        if self._source_mode == "both":
            self._height = max(self._height, 200)

    @objc.python_method
    def _active_sources(self):
        if self._source_mode == "mic":
            return ["mic"]
        elif self._source_mode == "system":
            return ["sys"]
        else:
            return ["mic", "sys"]

    # ── NSApplication 生命周期 ───────────────────────────────────────────────

    def applicationDidFinishLaunching_(self, notification):
        self._setupWindow()
        self._setupHotkey()
        model_desc = MODELS.get(self._model, self._model)
        for key in self._regions:
            self._appendRegionStatus(key, f"🎧 加载模型: {model_desc}\n")
            self._appendRegionStatus(key, "   Cmd+Shift+S 显示/隐藏 | Ctrl+C 退出\n\n")

        threading.Thread(target=self._loadModelAndStart, daemon=True).start()

    @objc.python_method
    def _loadModelAndStart(self):
        sources = self._active_sources()
        dual = len(sources) > 1

        for source in sources:
            audio_device = None if source == "mic" else self._system_device
            label = "MIC" if source == "mic" else "SYS"

            try:
                transcriber = create_transcriber(
                    model=self._model,
                    callback=self._make_callback(source),
                    device=audio_device,
                )
                self._transcribers[source] = transcriber
                dev = transcriber.device_name
                if audio_device is not None:
                    dev_name = sd.query_devices(audio_device)["name"]
                    status = f"✅ [{label}] 模型已加载 ({dev})，监听: {dev_name}\n"
                elif dual:
                    status = f"✅ [{label}] 模型已加载 ({dev})，监听: 默认麦克风\n"
                else:
                    status = f"✅ 模型已加载 ({dev})，正在监听麦克风...\n"
                self._postStatus(source, status)
                transcriber.start()
            except Exception as e:
                self._postStatus(source, f"❌ [{label}] 模型加载失败: {e}\n")

        if dual:
            self._postStatus(sources[0], "\n")

    @objc.python_method
    def _postStatus(self, source, text):
        payload = Foundation.NSDictionary.dictionaryWithDictionary_({
            "text": text,
            "source": source,
        })
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            b"handleModelLoaded:", payload, False
        )

    @objc.typedSelector(b"v@:@")
    def handleModelLoaded_(self, payload):
        source = str(payload["source"])
        text = str(payload["text"])
        if source in self._regions:
            self._appendRegionStatus(source, text)

    # ── 窗口创建 ─────────────────────────────────────────────────────────────

    def _setupWindow(self):
        screen = AppKit.NSScreen.mainScreen()
        sf = screen.frame()

        margin = 50
        rect = Foundation.NSMakeRect(
            sf.origin.x + margin,
            sf.origin.y + margin,
            sf.size.width - margin * 2,
            self._height,
        )

        self._window = (
            AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                rect,
                AppKit.NSWindowStyleMaskBorderless,
                AppKit.NSBackingStoreBuffered,
                False,
            )
        )

        w = self._window
        w.setLevel_(AppKit.NSFloatingWindowLevel)
        w.setOpaque_(False)
        w.setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0, 0, 0, self._opacity
            )
        )
        w.setIgnoresMouseEvents_(True)
        w.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        w.setHasShadow_(False)

        cv = w.contentView()
        cv.setWantsLayer_(True)
        cv.layer().setCornerRadius_(10)
        cv.layer().setMasksToBounds_(True)

        bounds = cv.bounds()
        sources = self._active_sources()

        if len(sources) == 1:
            self._setupSingleRegion(cv, bounds, sources[0])
        else:
            self._setupDualRegion(cv, bounds)

        w.orderFront_(None)

    @objc.python_method
    def _setupSingleRegion(self, cv, bounds, source_key):
        inner = Foundation.NSMakeRect(
            10, 5, bounds.size.width - 20, bounds.size.height - 10
        )
        scroll, tv = self._createTextRegion(inner)
        cv.addSubview_(scroll)
        self._regions[source_key] = {
            "text_view": tv,
            "line_start": 0,
            "output_file": None,
            "output_date": None,
        }

    @objc.python_method
    def _setupDualRegion(self, cv, bounds):
        padding = 10
        gap = 6
        available_h = bounds.size.height - padding * 2 - gap
        half_h = available_h / 2.0

        # SYS region (bottom — NSView y=0 is bottom)
        sys_rect = Foundation.NSMakeRect(
            padding, padding, bounds.size.width - padding * 2, half_h
        )
        sys_scroll, sys_tv = self._createTextRegion(sys_rect)
        cv.addSubview_(sys_scroll)

        # Separator line
        sep_y = padding + half_h + 1
        sep = AppKit.NSView.alloc().initWithFrame_(
            Foundation.NSMakeRect(
                padding + 5, sep_y, bounds.size.width - padding * 2 - 10, 1
            )
        )
        sep.setWantsLayer_(True)
        sep.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.4, 0.4, 0.4, 0.6
            ).CGColor()
        )
        cv.addSubview_(sep)

        # MIC region (top)
        mic_y = sep_y + gap - 1
        mic_rect = Foundation.NSMakeRect(
            padding, mic_y, bounds.size.width - padding * 2, half_h
        )
        mic_scroll, mic_tv = self._createTextRegion(mic_rect)
        cv.addSubview_(mic_scroll)

        self._regions["mic"] = {
            "text_view": mic_tv,
            "line_start": 0,
            "output_file": None,
            "output_date": None,
        }
        self._regions["sys"] = {
            "text_view": sys_tv,
            "line_start": 0,
            "output_file": None,
            "output_date": None,
        }

    @objc.python_method
    def _createTextRegion(self, rect):
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(rect)
        scroll.setHasVerticalScroller_(False)
        scroll.setHasHorizontalScroller_(False)
        scroll.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        scroll.setDrawsBackground_(False)

        tv = AppKit.NSTextView.alloc().initWithFrame_(rect)
        tv.setEditable_(False)
        tv.setSelectable_(False)
        tv.setDrawsBackground_(False)
        tv.setTextColor_(AppKit.NSColor.whiteColor())
        tv.setFont_(
            AppKit.NSFont.monospacedSystemFontOfSize_weight_(
                13, AppKit.NSFontWeightRegular
            )
        )
        tv.textContainer().setWidthTracksTextView_(True)

        scroll.setDocumentView_(tv)
        return scroll, tv

    # ── 流式转录回调 ─────────────────────────────────────────────────────────

    @objc.python_method
    def _make_callback(self, source):
        """为指定音频源创建转录回调。"""
        def cb(text, is_final):
            payload = Foundation.NSDictionary.dictionaryWithDictionary_({
                "text": text,
                "is_final": is_final,
                "source": source,
            })
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                b"handleSourceTranscription:", payload, False
            )
        return cb

    @objc.typedSelector(b"v@:@")
    def handleSourceTranscription_(self, payload):
        text = str(payload["text"])
        is_final = bool(payload["is_final"])
        source = str(payload["source"])
        region = self._regions.get(source)
        if not region:
            return
        if is_final:
            self._finalizeRegionLine(region, source, text)
        else:
            self._updateRegionLine(region, text)

    @objc.python_method
    def _updateRegionLine(self, region, text):
        tv = region["text_view"]
        storage = tv.textStorage()
        current_len = storage.length()
        gray = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.7, 0.7, 0.7, 1.0
        )
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.monospacedSystemFontOfSize_weight_(
                13, AppKit.NSFontWeightRegular
            ),
            AppKit.NSForegroundColorAttributeName: gray,
        }
        attr_str = Foundation.NSAttributedString.alloc().initWithString_attributes_(
            text, attrs
        )
        line_start = region["line_start"]
        replace_range = Foundation.NSMakeRange(
            line_start, current_len - line_start
        )
        storage.replaceCharactersInRange_withAttributedString_(
            replace_range, attr_str
        )
        tv.scrollRangeToVisible_(Foundation.NSMakeRange(storage.length(), 0))

    @objc.python_method
    def _finalizeRegionLine(self, region, source, text):
        tv = region["text_view"]
        storage = tv.textStorage()
        dual = len(self._regions) > 1
        prefix = f"[{source.upper()}] " if dual else ""
        display_text = f"{prefix}{text}\n"
        white = AppKit.NSColor.whiteColor()
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.monospacedSystemFontOfSize_weight_(
                13, AppKit.NSFontWeightRegular
            ),
            AppKit.NSForegroundColorAttributeName: white,
        }
        attr_str = Foundation.NSAttributedString.alloc().initWithString_attributes_(
            display_text, attrs
        )
        line_start = region["line_start"]
        current_len = storage.length()
        replace_range = Foundation.NSMakeRange(
            line_start, current_len - line_start
        )
        storage.replaceCharactersInRange_withAttributedString_(
            replace_range, attr_str
        )
        region["line_start"] = storage.length()
        tv.scrollRangeToVisible_(Foundation.NSMakeRange(storage.length(), 0))
        time_str = datetime.now().strftime("%H:%M:%S")
        self._writeToRegionFile(region, source, f"[{time_str}] {text}\n")

    # ── 快捷键 ───────────────────────────────────────────────────────────────

    def _setupHotkey(self):
        mask = AppKit.NSEventMaskKeyDown

        def on_key(event):
            flags = event.modifierFlags()
            has_cmd = bool(flags & AppKit.NSEventModifierFlagCommand)
            has_shift = bool(flags & AppKit.NSEventModifierFlagShift)
            chars = event.charactersIgnoringModifiers()
            if has_cmd and has_shift and chars and chars.lower() == "s":
                self._toggleVisibility()

        AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, on_key)

        def on_key_local(event):
            on_key(event)
            return event

        AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, on_key_local
        )

    def _toggleVisibility(self):
        if self._visible:
            self._window.orderOut_(None)
        else:
            self._window.orderFront_(None)
        self._visible = not self._visible

    # ── 文件导出 ─────────────────────────────────────────────────────────────

    @objc.python_method
    def _writeToRegionFile(self, region, source, text):
        if not self._output_dir:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        if region["output_date"] != today or region["output_file"] is None:
            if region["output_file"]:
                region["output_file"].close()
            suffix = "-sys" if source == "sys" else ""
            filepath = os.path.join(self._output_dir, f"{today}{suffix}.txt")
            region["output_file"] = open(filepath, "a", encoding="utf-8")
            region["output_date"] = today
        region["output_file"].write(text)
        region["output_file"].flush()

    # ── 文本输出辅助 ─────────────────────────────────────────────────────────

    @objc.python_method
    def _appendRegionStatus(self, region_key, text):
        yellow = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            1.0, 0.85, 0.0, 1.0
        )
        self._appendRegionWithColor(region_key, text, yellow)

    @objc.python_method
    def _appendRegionWithColor(self, region_key, text, color):
        region = self._regions.get(region_key)
        if not region:
            return
        tv = region["text_view"]
        storage = tv.textStorage()
        attrs = {
            AppKit.NSFontAttributeName: AppKit.NSFont.monospacedSystemFontOfSize_weight_(
                13, AppKit.NSFontWeightRegular
            ),
            AppKit.NSForegroundColorAttributeName: color,
        }
        attr_str = Foundation.NSAttributedString.alloc().initWithString_attributes_(
            text, attrs
        )
        storage.appendAttributedString_(attr_str)
        region["line_start"] = storage.length()
        tv.scrollRangeToVisible_(Foundation.NSMakeRange(storage.length(), 0))


def main():
    parser = argparse.ArgumentParser(
        description="Meeting Helper 实时字幕浮动窗口"
    )
    parser.add_argument(
        "--model",
        default="zipformer",
        choices=list(MODELS.keys()),
        help="ASR 模型 (默认 zipformer)",
    )
    parser.add_argument(
        "--source",
        default="mic",
        choices=["mic", "system", "both"],
        help="音频源: mic=麦克风, system=系统音频, both=同时双路 (默认 mic)",
    )
    parser.add_argument(
        "--system-device",
        default=None,
        help="系统音频设备名 (部分匹配，默认自动检测 BlackHole)",
    )
    parser.add_argument(
        "--opacity",
        type=float,
        default=0.75,
        help="背景不透明度 (0.0-1.0, 默认 0.75)",
    )
    parser.add_argument(
        "--height", type=int, default=120, help="窗口高度像素 (默认 120)"
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.expanduser("~/.meeting-helper/transcripts"),
        help="字幕文件输出目录 (默认 ~/.meeting-helper/transcripts)",
    )
    args = parser.parse_args()

    # ── 系统音频设备检测 ─────────────────────────────────────────────────────
    system_device_id = None
    if args.source in ("system", "both"):
        system_device_id = detect_system_audio_device(args.system_device)
        if system_device_id is None:
            if args.system_device:
                print(f"❌ 未找到设备: '{args.system_device}'", file=sys.stderr)
            else:
                print("❌ 未找到系统音频设备 (BlackHole)", file=sys.stderr)
                print("   安装: brew install blackhole-2ch", file=sys.stderr)
            print("\n可用输入设备:", file=sys.stderr)
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0:
                    marker = " ← default" if i == sd.default.device[0] else ""
                    print(f"   {i}: {d['name']}{marker}", file=sys.stderr)
            sys.exit(1)

        dev_name = sd.query_devices(system_device_id)["name"]
        print(f"系统音频设备: {dev_name} (index {system_device_id})")

        if args.model in HEAVY_MODELS and args.source == "both":
            print(
                f"⚠️  双路 + {args.model} 将使用约 2x 内存，"
                "推荐 zipformer 或 paraformer"
            )

    config = {
        "opacity": max(0.1, min(1.0, args.opacity)),
        "height": max(60, min(600, args.height)),
        "model": args.model,
        "output_dir": args.output_dir,
        "source": args.source,
        "system_device": system_device_id,
    }

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    delegate = SubtitleDelegate.alloc().init()
    delegate.configure_(config)
    app.setDelegate_(delegate)

    signal.signal(signal.SIGINT, lambda *_: os._exit(0))
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))

    model_desc = MODELS.get(config["model"], config["model"])
    source_desc = {"mic": "麦克风", "system": "系统音频", "both": "麦克风+系统音频"}
    print(
        f"字幕窗口已启动 "
        f"(模型: {config['model']}, 音频源: {source_desc[config['source']]}, "
        f"透明度: {config['opacity']}, 高度: {config['height']}px)"
    )
    print(f"后端: {model_desc}")
    print(f"字幕导出: {config['output_dir']}/YYYY-MM-DD.txt")
    if config["source"] in ("system", "both"):
        print(f"系统音频导出: {config['output_dir']}/YYYY-MM-DD-sys.txt")
    print("Cmd+Shift+S 显示/隐藏 | Ctrl+C 退出")

    app.run()


if __name__ == "__main__":
    main()
