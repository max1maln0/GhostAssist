import ctypes
import os
import sys
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic

from pynput import keyboard, mouse

from config.settings import Settings, validate_hotkeys
from core.audio_worker import AudioError, SegmentQueue, record_wav
from core.context_manager import ContextManager
from core.llm_worker import GenerationError, LLMWorker
from core.session_manager import SessionManager
from core.single_instance import SingleInstance
from core.task_state import State, TaskState
from ui.snipping_tool import SnippingOverlay
from ui.stealth_hud import StealthHUD


class GhostAssistApp:
    def __init__(self, settings):
        self.settings = settings
        self.context = ContextManager(settings)
        self.hud = StealthHUD()
        self.llm = LLMWorker(settings)
        self.tasks = TaskState()
        self.sessions = SessionManager(depth=settings.memory_depth)
        self.session_task = None
        self.session_question = None
        self.task_started = None
        self.monitoring = False
        self.managed = False
        self.commands = Queue(maxsize=32)
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.capture_worker = ThreadPoolExecutor(max_workers=1)
        self.capture_future = None
        self.capture_stop = None
        self.capture_error = None
        self.segments = None
        self.lost_segments = 0
        self.future = None
        self.overlay = None
        self.review = None
        self.closing = False
        self.listeners = []
        self.listener_failed = False
        self.cancel_requested = Event()
        self.close_requested = Event()
        self.hud.root.protocol("WM_DELETE_WINDOW", self.close)

    def enqueue(self, action):
        # Hooks never call Tk. Keep capacity for cancellation and shutdown.
        if action == self.close:
            self.close_requested.set()
        elif action == self.cancel:
            self.cancel_requested.set()
        else:
            try:
                self.commands.put_nowait(action)
            except Full:
                pass

    def capture(self):
        task = self.tasks.begin(State.PROCESSING)
        if task:
            self.submit(task)

    def snip(self):
        task = self.tasks.begin(State.CAPTURING)
        if not task:
            return
        self.hud.update_text("", "ВЫДЕЛИТЕ ОБЛАСТЬ")
        try:
            self.overlay = SnippingOverlay(self.hud.root, self.selected)
        except Exception:  # noqa: BLE001 - isolate the application boundary
            self.tasks.finish(task)
            self.hud.update_text("Не удалось открыть окно выделения.", "ОШИБКА")

    def selected(self, bbox):
        self.overlay = None
        task = self.tasks.active
        if bbox is None:
            self.tasks.finish(task)
            self.hud.update_text("Выделение отменено или слишком мало.", "ГОТОВО")
        else:
            task.state = State.PROCESSING
            self.submit(task, bbox=bbox)

    def audio(self):
        if self.settings.vad_enabled:
            if self.monitoring:
                self.cancel()
                return
            if (
                self.capture_future is not None
                or self.segments is not None
                or self.tasks.state is not State.IDLE
            ):
                return
            self.monitoring = True
            self.capture_error = None
            self.lost_segments = 0
            self.capture_stop = Event()
            max_bytes = self.settings.audio_max_mib * 1024 * 1024
            segment_limit = max_bytes // 5
            self.segments = SegmentQueue(max_bytes - segment_limit)
            queue = self.segments
            stop = self.capture_stop
            options = {
                "min_speech_ms": self.settings.vad_min_speech_ms,
                "silence_ms": self.settings.vad_silence_ms,
                "max_seconds": self.settings.vad_max_seconds,
                "sensitivity": self.settings.vad_sensitivity,
            }
            self.hud.update_text("", "VAD · ЗАПИСЬ")
            self.capture_future = self.capture_worker.submit(
                record_wav,
                stop,
                stop,
                self.settings.audio_max_seconds,
                segment_limit,
                self.settings.audio_device,
                vad_options=options,
                on_segment=queue.put,
            )
            return
        if self.tasks.state is State.RECORDING:
            self.tasks.active.stop.set()
            self.tasks.active.state = State.PROCESSING
            self.hud.update_text(status="АНАЛИЗ ЗВУКА")
            return
        task = self.tasks.begin(State.RECORDING)
        if task:
            self.submit(task, audio=True)

    def submit(self, task, bbox=None, audio=False, audio_data=None):
        mode = "INTERVIEW" if audio else self.context.mode
        prompt = self.context.get_system_prompt(mode=mode)
        generation, history = self.sessions.snapshot(mode)
        if not self.settings.memory_enabled:
            history = ()
        self.session_task = (task, mode, generation)
        self.session_question = None
        self.task_started = monotonic()
        status = (
            "VAD · ТРАНСКРИБАЦИЯ"
            if audio_data is not None
            else ("СЛУШАЮ СИСТЕМУ" if audio else "СКАН ЭКРАНА")
        )
        self.hud.update_text("", status)

        def work():
            started = monotonic()
            if audio_data is not None:
                data = audio_data
            elif audio:
                data = record_wav(
                    task.stop,
                    task.cancel,
                    self.settings.audio_max_seconds,
                    self.settings.audio_max_mib * 1024 * 1024,
                    self.settings.audio_device,
                    vad_options=None,
                )
            else:
                data = self.llm.capture_screen(bbox)
            if task.cancel.is_set():
                return None
            task.stop.set()
            if audio:
                print(f"[metrics] audio_bytes={len(data)}", file=sys.stderr)
                transcription_start = monotonic()
                transcript = self.llm.transcribe(data, task.cancel)
                print(
                    f"[metrics] transcription_ms={(monotonic() - transcription_start) * 1000:.0f}",
                    file=sys.stderr,
                )
                return ("transcript", transcript)
            print(
                f"[metrics] context_chars={sum(len(item.question) + len(item.answer) for item in history)}",
                file=sys.stderr,
            )
            return self.llm.generate(
                data,
                "audio/wav" if audio else "image/jpeg",
                prompt,
                task.publish,
                task.cancel,
                history=history,
                budget=self.settings.task_timeout
                - (0 if audio else monotonic() - started),
            )

        self.future = self.worker.submit(work)

    def show_transcript(self, task, text):
        if not text or not text.strip():
            raise GenerationError("Речь не распознана.")
        status = (
            "VAD · ПРОВЕРЬТЕ ТЕКСТ" if self.monitoring else "ПРОВЕРЬТЕ ТРАНСКРИПЦИЮ"
        )
        self.hud.update_text(text, status)
        self.review = tk.Toplevel(self.hud.root)
        self.review.title("Проверка распознанной речи")
        self.review.attributes("-topmost", True)
        self.review.configure(bg="#080e1a")
        editor = tk.Text(
            self.review,
            width=65,
            height=8,
            wrap="word",
            bg="#080e1a",
            fg="#cbd5e1",
            insertbackground="white",
        )
        editor.pack(fill="both", expand=True, padx=12, pady=12)
        editor.insert("1.0", text)

        def submit():
            if (
                self.review is None
                or self.tasks.active is not task
                or task.cancel.is_set()
            ):
                return
            question = editor.get("1.0", "end-1c").strip()
            if not question:
                return
            self.review.destroy()
            self.review = None
            self.session_question = question
            task.state = State.PROCESSING
            _, mode, generation = self.session_task
            current_generation, history = self.sessions.snapshot(mode)
            if generation != current_generation or not self.settings.memory_enabled:
                history = ()
            prompt = self.context.get_system_prompt(mode=mode)
            status = "VAD · ЗАПРОС GEMINI" if self.monitoring else "ЗАПРОС GEMINI"
            self.hud.update_text(question, status)

            def work():
                return self.llm.generate(
                    question.encode("utf-8"),
                    "text/plain",
                    prompt,
                    task.publish,
                    task.cancel,
                    history=history,
                    instruction="Ответь на вопрос пользователя.",
                )

            self.future = self.worker.submit(work)

        controls = tk.Frame(self.review, bg="#080e1a")
        controls.pack(pady=(0, 12))
        tk.Button(controls, text="Отправить", command=submit).pack(side="left", padx=5)
        tk.Button(controls, text="Отменить", command=self.cancel_review).pack(
            side="left", padx=5
        )
        self.review.protocol("WM_DELETE_WINDOW", self.cancel_review)
        if self.settings.auto_send_transcript:
            self.review.after(0, submit)

    def cancel_review(self):
        task = self.tasks.active
        if self.review is None or task is None:
            return
        task.cancel.set()
        self.review.destroy()
        self.review = None
        self.tasks.finish(task)
        self.session_task = None
        self.task_started = None
        status = "VAD · СЛУШАЮ" if self.monitoring else "ГОТОВО"
        self.hud.update_text("Реплика отменена.", status)

    def cancel(self):
        self.monitoring = False
        self.capture_error = None
        if self.capture_stop:
            self.capture_stop.set()
        if self.segments:
            self.segments.close()
            self.segments = None
        task = self.tasks.active
        if not task:
            self.hud.update_text(
                status="VAD · ОСТАНОВКА" if self.capture_future else "ГОТОВО"
            )
            return
        self.tasks.cancel()
        self.hud.update_text("Задача отменена.", "ОТМЕНА")
        if self.review:
            self.review.destroy()
            self.review = None
            self.tasks.finish(task)
            self.session_task = None
            self.hud.update_text(status="ГОТОВО")
        if self.overlay:
            self.overlay.close()
            self.overlay = None
            self.tasks.finish(task)
            self.hud.update_text(status="ГОТОВО")

    def switch_mode(self):
        if self.tasks.state is State.IDLE:
            mode = self.context.toggle_mode()
            self.hud.update_text(f"Режим {mode}", f"[{mode}] ГОТОВО")

    def poll(self):
        if not self.closing and any(
            not listener.is_alive() for listener in self.listeners
        ):
            self.listener_failed = True
            self.close()
        if self.close_requested.is_set():
            self.close()
        if self.cancel_requested.is_set():
            self.cancel_requested.clear()
            self.cancel()
            for _ in range(32):
                try:
                    self.commands.get_nowait()
                except Empty:
                    break
        if not self.closing:
            for _ in range(32):
                try:
                    action = self.commands.get_nowait()
                except Empty:
                    break
                action()

        task = self.tasks.active
        if self.future and task:
            if self.future.done():
                try:
                    result = self.future.result()
                    if not task.cancel.is_set() and isinstance(result, tuple):
                        self.show_transcript(task, result[1])
                        task.state = State.REVIEW
                    elif not task.cancel.is_set():
                        if (
                            self.settings.memory_enabled
                            and result
                            and self.session_task
                        ):
                            _, mode, generation = self.session_task
                            question, separator, answer = result.partition("Ответ:")
                            if separator and question.strip().startswith("Вопрос:"):
                                question = (
                                    self.session_question
                                    or question.removeprefix("Вопрос:")
                                )
                            elif self.session_question:
                                question, answer = self.session_question, result
                            else:
                                question, answer = "", ""
                            if question and answer:
                                self.sessions.add(
                                    mode, question, answer, generation, task.cancel
                                )
                        if self.managed:
                            test_count = len(self.sessions.snapshot("TEST")[1])
                            interview_count = len(
                                self.sessions.snapshot("INTERVIEW")[1]
                            )
                            print(
                                f"GHOST_SESSION {test_count} {interview_count}",
                                flush=True,
                            )
                        status = (
                            "VAD · СЛУШАЮ"
                            if self.monitoring
                            else f"[{self.context.mode}] ГОТОВО"
                        )
                        self.hud.update_text(result, status)
                except (AudioError, GenerationError) as exc:
                    if not task.cancel.is_set():
                        self.hud.update_text(str(exc), "ОШИБКА")
                except Exception:  # noqa: BLE001 - isolate the application boundary
                    if self.review:
                        self.review.destroy()
                        self.review = None
                    if not task.cancel.is_set():
                        self.hud.update_text("Не удалось обработать задачу.", "ОШИБКА")
                finally:
                    if task.cancel.is_set():
                        self.hud.update_text(status="ГОТОВО")
                    if task.state is not State.REVIEW:
                        if self.task_started is not None:
                            print(
                                f"[metrics] total_ms={(monotonic() - self.task_started) * 1000:.0f}",
                                file=sys.stderr,
                            )
                            self.task_started = None
                        self.tasks.finish(task)
                        self.session_task = None
                    self.future = None
            elif not task.cancel.is_set():
                if task.state is State.RECORDING and task.stop.is_set():
                    task.state = State.PROCESSING
                    self.hud.update_text(status="АНАЛИЗ ЗВУКА")
                preview = task.preview()
                if preview is not None:
                    task.state = State.STREAMING
                    status = (
                        "VAD · ЗАПРОС GEMINI" if self.monitoring else "ЗАПРОС GEMINI"
                    )
                    self.hud.update_text(preview, status)
        if self.capture_future is not None and self.capture_future.done():
            try:
                self.capture_future.result()
            except AudioError as exc:
                if not self.closing and self.monitoring:
                    self.capture_error = str(exc)
                    self.hud.update_text(self.capture_error, "ОШИБКА ЗАХВАТА")
            except Exception:  # noqa: BLE001 - isolate the capture boundary
                if not self.closing and self.monitoring:
                    self.capture_error = "Ошибка захвата звука."
                    self.hud.update_text(self.capture_error, "ОШИБКА ЗАХВАТА")
            was_stopping = self.capture_stop is not None and self.capture_stop.is_set()
            self.monitoring = False
            self.capture_future = None
            self.capture_stop = None
            if was_stopping and self.tasks.state is State.IDLE and not self.closing:
                self.hud.update_text(status="ГОТОВО")

        if self.segments and not self.closing:
            dropped = self.segments.take_overflow()
            if dropped:
                self.lost_segments += dropped
                self.hud.update_text(
                    f"Очередь аудио заполнена. Пропущено сегментов: {self.lost_segments}.",
                    "ОШИБКА ОЧЕРЕДИ",
                )
            if self.tasks.state is State.IDLE and self.future is None:
                segment = self.segments.get()
                if segment is not None:
                    print(f"[metrics] vad_silence_ms={segment[2]}", file=sys.stderr)
                    task = self.tasks.begin(State.PROCESSING)
                    self.submit(task, audio=True, audio_data=segment[1])
                elif not self.monitoring and self.capture_future is None:
                    self.segments.close()
                    self.segments = None

        alerts = []
        if self.capture_error:
            alerts.append(f"ОШИБКА ЗАХВАТА: {self.capture_error}")
        if self.lost_segments and self.segments is not None:
            alerts.append(f"ПРОПУЩЕНО СЕГМЕНТОВ: {self.lost_segments}")
        if alerts and not self.closing:
            self.hud.update_text(status=" · ".join(alerts))

        if self.closing and self.future is None and self.capture_future is None:
            self.hud.root.destroy()
        else:
            self.hud.root.after(25, self.poll)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.cancel()
        self.sessions.close()
        for listener in self.listeners:
            listener.stop()

    def watch_parent(self, fd):
        try:
            while True:
                command = os.read(fd, 1)
                if not command:
                    break
                if command == b"C":
                    self.enqueue(self.cancel)
        except OSError:
            pass
        self.enqueue(self.close)

    def run(self, managed):
        settings = self.settings
        self.managed = managed
        bindings = {
            settings.hotkey_screen: self.capture,
            settings.hotkey_snip: self.snip,
            settings.hotkey_audio: self.audio,
            settings.hotkey_mode: self.switch_mode,
            settings.hotkey_hud: self.hud.toggle_visibility,
            settings.hotkey_dock: self.hud.toggle_dock_position,
            settings.hotkey_cancel: self.cancel,
            settings.hotkey_page_up: lambda: self.hud.scroll(-1),
            settings.hotkey_page_down: lambda: self.hud.scroll(1),
        }

        def click(x, y, button, pressed):
            if pressed and button in (mouse.Button.x1, mouse.Button.x2):
                self.enqueue(self.capture if button == mouse.Button.x1 else self.audio)

        try:
            self.listeners = [
                keyboard.GlobalHotKeys(
                    {
                        key: lambda action=action: self.enqueue(action)
                        for key, action in bindings.items()
                    }
                ),
                mouse.Listener(on_click=click),
            ]
            for listener in self.listeners:
                listener.start()
            if managed:
                Thread(
                    target=self.watch_parent, args=(sys.stdin.fileno(),), daemon=True
                ).start()
            self.hud.root.after(25, self.poll)
            self.hud.run()
            if self.listener_failed:
                raise RuntimeError("Global input listener stopped unexpectedly")
        finally:
            self.close()
            self.worker.shutdown(wait=True)
            self.capture_worker.shutdown(wait=True)


def main():
    try:
        instance = SingleInstance("service")
        if not instance.primary:
            instance.close()
            print("Служба GhostAssist уже запущена.", file=sys.stderr)
            return 3
        try:
            return run_service()
        finally:
            instance.close()
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001 - isolate the application boundary
        print(
            "Не удалось запустить службу. Проверьте настройки и зависимости.",
            file=sys.stderr,
        )
        return 1


def run_service():
    settings = Settings()
    validate_hotkeys(settings)
    if not settings.has_api_key:
        print("Укажите ключ Gemini в Dashboard или .env.", file=sys.stderr)
        return 2
    # Tk and MSS must see the same physical desktop coordinates.
    set_dpi_context = getattr(
        ctypes.windll.user32, "SetProcessDpiAwarenessContext", None
    )
    if set_dpi_context is None or not set_dpi_context(ctypes.c_void_p(-4)):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    GhostAssistApp(settings).run("--managed" in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
