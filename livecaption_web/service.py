"""Caption sessions. Partials stay on the SSE page; finals get a stable (session, seq) id."""

from __future__ import annotations

from datetime import datetime, timezone

import json
import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from modules.caption.asr_worker import OfflineModelError, streaming_layout
from modules.caption.capture import AudioGate, offer, start_capture


class CaptionService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.generation = 0
        self.session_id = ""
        self.seq = 0
        self.last_final = ""
        self.error = ""
        self.lock = threading.Lock()
        self._lifecycle = threading.RLock()
        self.subscribers: list[queue.Queue] = []
        self.audio: queue.Queue = queue.Queue(maxsize=8)
        self.stats = {"length": 0, "max": 0, "dropped": 0}
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._threads: list[threading.Thread] = []
        self._io_threads: list[threading.Thread] = []
        self._worker: subprocess.Popen | None = None
        self._gate = AudioGate()
        self._final_ids: set[tuple[str, int]] = set()
        self._stderr = ""

    def subscribe(self) -> queue.Queue:
        box: queue.Queue = queue.Queue()
        with self.lock:
            self.subscribers.append(box)
        return box

    def unsubscribe(self, box: queue.Queue) -> None:
        with self.lock:
            if box in self.subscribers:
                self.subscribers.remove(box)

    def start(self) -> str:
        with self._lifecycle:
            return self._start_session()

    def _start_session(self) -> str:
        model_dir = self.ctx.config.caption_model_dir
        if not model_dir:
            self.error = "还没有设置语音识别模型。请按 archive/docs/asr-choice.md 准备模型，在 config.toml 的 caption.model_dir 中填写模型文件夹，再重启助手。"
            return self.error
        try:
            streaming_layout(Path(model_dir))
        except (OfflineModelError, FileNotFoundError, OSError) as exc:
            self.error = str(exc)
            return self.error
        self.stop(summarize=True)
        with self.lock:
            self.generation += 1
            generation = self.generation
            self.session_id = uuid.uuid4().hex
            self.seq = 0
            self.last_final = ""
            self.error = ""
            self._stderr = ""
            self._final_ids = set()
            self._stop = threading.Event()
            self._ready = threading.Event()
            self.stats = {"length": 0, "max": 0, "dropped": 0}
        self._drain_audio()
        self._start_worker(generation)
        if not self._ready.wait(30):
            self.error = self.error or "语音识别模型启动超过 30 秒，请检查模型文件和电脑资源后重试。"
            self.stop(summarize=False)
            return self.error
        if self.error:
            message = self.error
            self.stop(summarize=False)
            self.error = message
            return message
        self._threads = start_capture(
            lambda source, rate, samples: self._on_audio(generation, source, rate, samples),
            self._stop,
            self._on_disconnect,
            self._on_status,
        )
        if not self._threads and self.error:
            message = self.error
            self.stop(summarize=False)
            self.error = message
            return message
        return ""

    def stop(self, *, summarize: bool = True) -> None:
        with self._lifecycle:
            self._stop_session(summarize=summarize)

    def _stop_session(self, *, summarize: bool) -> None:
        with self.lock:
            session_id = self.session_id
        # Keep this generation valid until EOF finals have been read and saved.
        self._stop.set()
        self._ready.set()
        worker = self._worker
        self._worker = None
        for thread in self._threads:
            thread.join(timeout=1)
        for thread in self._io_threads:
            if thread.name == 'asr-in':
                thread.join(timeout=3)
        if worker is not None:
            if worker.stdin is not None:
                try:
                    worker.stdin.close()
                except OSError:
                    pass
            try:
                worker.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.error = "停止字幕时识别程序未及时结束，最后一句可能不完整。已保存的文字仍然保留。"
                worker.kill()
                try:
                    worker.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        for thread in self._threads + self._io_threads:
            if thread is threading.current_thread():
                continue
            thread.join(timeout=1)
        with self.lock:
            self.generation += 1
            self.session_id = ""
        self._threads = []
        self._io_threads = []
        self._drain_audio()
        if summarize and session_id:
            self._enqueue_session_summary(session_id)

    def accept(self, generation: int, text: str, *, final: bool, utterance: int | None = None) -> bool:
        """Write a final only when `generation` is still the current session."""
        text = (text or "").strip()
        if not text:
            return False
        with self.lock:
            if generation != self.generation or not self.session_id:
                return False
            if not final:
                self._publish({"text": text, "final": False, "session_id": self.session_id})
                return True
            if utterance is not None:
                key = (self.session_id, int(utterance))
                if key in self._final_ids:
                    return False
                self._final_ids.add(key)
            elif text == self.last_final:
                return False
            self.seq += 1
            seq = self.seq
            session_id = self.session_id
            self.last_final = text
        row = {
            "source": "caption",
            "conversation_id": session_id,
            "message_id": f"{session_id}:{seq}",
            "role": "caption",
            "ts": datetime.fromtimestamp(self.ctx.clock(), timezone.utc).isoformat(),
            "seq": seq,
            "text": text,
            "content_hash": _hash(text),
        }
        conn = self.ctx.connect()
        try:
            self.ctx.calls["insert_texts"](conn, [row])
        finally:
            conn.close()
        with self.lock:
            if generation != self.generation:
                self.last_final = ""
                conn = self.ctx.connect()
                try:
                    conn.execute(
                        "DELETE FROM texts WHERE source = 'caption' AND message_id = ?",
                        (row["message_id"],),
                    )
                    conn.commit()
                finally:
                    conn.close()
                return False
            self._publish(
                {
                    "text": text,
                    "final": True,
                    "session_id": session_id,
                    "seq": seq,
                    "utterance": utterance,
                }
            )
            return True

    def _on_audio(self, generation: int, source: str, rate: int, samples) -> None:
        with self.lock:
            current = self.generation
        if generation != current or self._stop.is_set():
            return
        selected = self._gate.process(source, rate, samples)
        if selected is None:
            return
        offer(self.audio, {"generation": generation, "samples": selected}, self.stats)

    def _on_status(self, message: str) -> None:
        self.error = message

    def _on_disconnect(self, message: str) -> None:
        """One device failing is reported. It does not invalidate the other device or the session."""
        self.error = message
        with self.lock:
            self._publish({"text": message, "final": False, "error": True})

    def _start_worker(self, generation: int) -> None:
        command = [
            sys.executable,
            "-m",
            "modules.caption.asr_worker",
            "--model-dir",
            self.ctx.config.caption_model_dir,
            "--num-threads",
            str(self.ctx.config.caption_num_threads),
        ]
        try:
            worker = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            self.error = str(exc)
            self._ready.set()
            return
        self._worker = worker
        reader = threading.Thread(target=self._read_worker, args=(worker, generation), name="asr-out", daemon=True)
        writer = threading.Thread(target=self._write_audio, args=(worker, generation), name="asr-in", daemon=True)
        errors = threading.Thread(target=self._read_stderr, args=(worker,), name="asr-err", daemon=True)
        self._io_threads = [reader, writer, errors]
        reader.start()
        writer.start()
        errors.start()

    def _read_stderr(self, worker) -> None:
        if worker.stderr is None:
            return
        chunks = []
        for line in worker.stderr:
            chunks.append(line)
            text = "".join(chunks)[-500:]
            self._stderr = text.strip()
            if text.strip():
                self.error = text.strip()

    def _write_audio(self, worker, generation: int) -> None:
        import array
        import base64

        while not self._stop.is_set() or not self.audio.empty():
            try:
                item = self.audio.get(timeout=0.2)
            except queue.Empty:
                continue
            if item["generation"] != generation:
                continue
            values = array.array("f", item["samples"])
            line = json.dumps({"source": "auto", "pcm": base64.b64encode(values.tobytes()).decode("ascii")})
            try:
                worker.stdin.write(line + "\n")
                worker.stdin.flush()
            except (OSError, ValueError, AttributeError):
                return

    def _read_worker(self, worker, generation: int) -> None:
        assert worker.stdout is not None
        failed = False
        for line in worker.stdout:
            with self.lock:
                current = self.generation
            if current != generation:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("error"):
                self.error = str(event["error"])
                failed = True
                break
            if event.get("status") == "ready":
                self._ready.set()
                continue
            utterance = event.get("utterance")
            self.accept(
                generation,
                event.get("text", ""),
                final=bool(event.get("final")),
                utterance=int(utterance) if utterance is not None else None,
            )
        self._ready.set()
        code = worker.poll()
        with self.lock:
            still = self.generation == generation and bool(self.session_id)
        if still and (failed or code not in (0, None)):
            detail = self.error or self._stderr or f"语音识别程序意外停止（退出代码 {code}），请重新开始字幕。"
            self._abandon(generation, detail)

    def _abandon(self, generation: int, message: str) -> None:
        session_id = ""
        with self.lock:
            if self.generation != generation or not self.session_id:
                return
            self.generation += 1
            session_id = self.session_id
            self.session_id = ""
            self.error = message
        self._stop.set()
        self._enqueue_session_summary(session_id)

    def _enqueue_session_summary(self, session_id: str) -> None:
        if not session_id or "enqueue_summary" not in self.ctx.calls:
            return
        try:
            self.ctx.calls["enqueue_summary"](self.ctx, "caption", session_id)
        except Exception as exc:
            self.error = f"语音识别已停止，文字已保存，但未能开始生成摘要。请到摘要页点击「继续生成摘要」。具体原因：{exc}"

    def _drain_audio(self) -> None:
        while True:
            try:
                self.audio.get_nowait()
            except queue.Empty:
                break

    def _publish(self, payload: dict) -> None:
        for box in list(self.subscribers):
            try:
                box.put_nowait(payload)
            except queue.Full:
                pass


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
