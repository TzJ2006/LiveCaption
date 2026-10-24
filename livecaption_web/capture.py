"""Windows mic and system-audio capture. Heavy imports stay inside start_capture."""

from __future__ import annotations

import math
import queue
import threading
import time


def rms_db(samples) -> float:
    if not samples:
        return -90.0
    mean_sq = sum(sample * sample for sample in samples) / len(samples)
    rms = math.sqrt(mean_sq)
    if rms <= 1e-6:
        return -90.0
    return max(-90.0, min(6.0, 20.0 * math.log10(rms)))


def downmix(int16_bytes: bytes, channels: int) -> list[float]:
    import array

    raw = array.array("h")
    raw.frombytes(int16_bytes)
    if channels <= 1:
        return [sample / 32768.0 for sample in raw]
    frames = len(raw) // channels
    mixed = []
    for index in range(frames):
        total = 0.0
        for channel in range(channels):
            total += raw[index * channels + channel]
        mixed.append(total / channels / 32768.0)
    return mixed


class AudioGate:
    """Keep the channel that is talking. System audio wins while it has voice, then hangs over.

    Port of LiveCaption's AudioGate: -45 dB threshold, 0.6 s hangover, linear resample to 16 kHz.
    """

    TARGET_RATE = 16000
    SYSTEM_VOICE_DB = -45.0
    HANGOVER = 0.6

    def __init__(self, clock=None):
        self.last_system_voice = float("-inf")
        self.selected = "mic"
        self.positions: dict[str, float] = {}
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()

    def process(self, source: str, rate: int, samples):
        if not samples or rate <= 0:
            return None
        with self._lock:
            now = self.clock()
            if source == "sys" and rms_db(samples) >= self.SYSTEM_VOICE_DB:
                self.last_system_voice = now
            self.selected = "sys" if now - self.last_system_voice <= self.HANGOVER else "mic"
            if source != self.selected:
                return None
            return self._resample(source, rate, samples)

    def _resample(self, source: str, rate: int, samples):
        if abs(rate - self.TARGET_RATE) <= 1:
            return list(samples)
        step = rate / self.TARGET_RATE
        start = self.positions.get(source, 0.0)
        count = max(0, math.ceil((len(samples) - start) / step))
        output = []
        last = len(samples) - 1
        for index in range(count):
            tap = start + step * index
            lower = int(tap)
            if lower > last:
                break
            upper = min(lower + 1, last)
            fraction = tap - lower
            output.append(samples[lower] + (samples[upper] - samples[lower]) * fraction)
        self.positions[source] = start + step * count - len(samples)
        return output


def offer(audio_queue: queue.Queue, item, stats: dict) -> None:
    """Keep the queue bounded. Drop the oldest frame instead of growing without limit."""
    try:
        audio_queue.put(item, timeout=0)
    except queue.Full:
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            audio_queue.put(item, timeout=0)
        except queue.Full:
            pass
        stats["dropped"] = stats.get("dropped", 0) + 1
    stats["max"] = max(stats.get("max", 0), audio_queue.qsize())
    stats["length"] = audio_queue.qsize()


def start_capture(on_frame, stop: threading.Event, on_disconnect, on_status=None) -> list[threading.Thread]:
    """Open the default mic and the WASAPI loopback device. A missing device is reported, not fatal."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        on_disconnect(f"sounddevice is not installed ({exc})")
        return []

    threads = []

    def run(source, stream_factory):
        try:
            with stream_factory() as stream:
                stream.start()
                while not stop.wait(0.1):
                    pass
        except Exception as exc:
            if not stop.is_set():
                on_disconnect(f"{source} capture stopped: {exc}")

    def mic_factory():
        def callback(indata, frames, time_info, status):
            if status and on_status:
                on_status(f"mic status: {status}")
            samples = [float(value) for value in indata[:, 0]]
            on_frame("mic", int(stream.samplerate), samples)

        stream = sd.InputStream(callback=callback, dtype="float32", channels=1)
        return stream

    def run_loopback():
        # sounddevice has no WASAPI loopback; same library and loop as old LiveCaption win_host.py.
        try:
            import numpy as np
            import pyaudiowpatch as pyaudio

            pa = pyaudio.PyAudio()
            try:
                device = pa.get_default_wasapi_loopback()
                channels = max(1, int(device["maxInputChannels"]))
                rate = int(device["defaultSampleRate"])
                stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                                 input_device_index=device["index"], frames_per_buffer=rate // 10)
                try:
                    while not stop.is_set():
                        data = stream.read(rate // 10, exception_on_overflow=False)
                        frames = np.frombuffer(data, dtype=np.int16).reshape(-1, channels)
                        on_frame("sys", rate, (frames.mean(axis=1) / 32768.0).tolist())
                finally:
                    stream.stop_stream()
                    stream.close()
            finally:
                pa.terminate()
        except Exception as exc:
            if not stop.is_set():
                on_disconnect(f"sys capture stopped: {exc}")

    for name, target, args in (("mic", run, ("mic", mic_factory)), ("sys", run_loopback, ())):
        thread = threading.Thread(target=target, args=args, name=f"capture-{name}", daemon=True)
        thread.start()
        threads.append(thread)
    return threads
