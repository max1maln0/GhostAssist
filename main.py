import os
import sys
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic

from config.settings import Settings, validate_hotkeys
from core.audio_queue import AudioError, SegmentQueue
from core.context_manager import ContextManager
from core.contracts import TaskError, TaskRequest, TaskStatus
from core.llm_worker import GenerationError
from core.orchestrator import TaskFailure
from core.platform_contracts import ScreenCaptureError
from core.provider_contracts import ProviderError
from core.session_manager import SessionManager
from core.task_state import State, TaskState
from platform_adapters.windows.composition import build_dependencies, configure_dpi
from platform_adapters.windows.single_instance import SingleInstance
from ui.snipping_tool import SnippingOverlay
from ui.stealth_hud import StealthHUD


class GhostAssistApp:
    def __init__(self, settings, dependencies=None):
        self.settings = settings
        dependencies = dependencies or build_dependencies(settings)
        self.context = ContextManager(settings)
        self.hud = StealthHUD()
        self.llm = dependencies.llm
        self.screen_capture = dependencies.screen
        self.audio_capture = dependencies.audio
        self.hotkeys = dependencies.hotkeys
        self.tasks = TaskState()
        self.screenshots = dependencies.screenshots
        self.screenshot_id = None
        self.screenshot_discard = False
        self.screenshot_session = None
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
        self.shutdown_deadline = None
        self.shutdown_reported = False
        self.listeners = []
        self.listener_failed = False
        self.cancel_requested = Event()
        self.close_requested = Event()
        self.hotkeys_suspended = Event()
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
        if (
            self.closing
            or self.screenshot_id is not None
            or self.tasks.state is not State.IDLE
            or self.monitoring
            or self.capture_future is not None
            or self.segments is not None
        ):
            return
        request = TaskRequest("", "image/jpeg")
        mode = self.context.mode
        prompt = self.context.get_system_prompt(mode=mode)
        generation, history = self.sessions.snapshot(mode)
        if not self.settings.memory_enabled:
            history = ()
        self.screenshots.prepare(request)
        self.screenshot_id = request.task_id
        self.screenshot_discard = False
        self.screenshot_session = (mode, generation)
        self.hud.update_text("", "СКАН ЭКРАНА")

        def work(request, token, publish):
            started = monotonic()
            if token.is_set():
                return ""
            try:
                image = self.screen_capture.capture()
                if token.is_set():
                    return ""
                return self.llm.generate(
                    image,
                    "image/jpeg",
                    prompt,
                    lambda text: publish(TaskStatus.CHUNK, text),
                    token,
                    history=history,
                    budget=self.settings.task_timeout - (monotonic() - started),
                )
            except ProviderError as exc:
                raise TaskFailure(TaskError(exc.code.value, str(exc))) from None
            except ScreenCaptureError as exc:
                raise TaskFailure(TaskError("screen_capture", str(exc))) from None

        self.screenshots.start(work, self.settings.task_timeout)
        self.future = self.screenshots.future

    def snip(self):
        if self.screenshot_id is not None:
            return
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
        if self.screenshot_id is not None:
            return
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
                self.audio_capture.record,
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
                data = self.audio_capture.record(
                    task.stop,
                    task.cancel,
                    self.settings.audio_max_seconds,
                    self.settings.audio_max_mib * 1024 * 1024,
                    self.settings.audio_device,
                    vad_options=None,
                )
            else:
                data = self.screen_capture.capture(bbox)
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
        if self.screenshot_id is not None:
            self.screenshot_discard = True
            self.screenshots.cancel()
            self.hud.update_text("Задача отменена.", "ОТМЕНА")
            return
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
        if self.tasks.state is State.IDLE and self.screenshot_id is None:
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

        self._poll_screenshot()
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
                except (AudioError, ProviderError, ScreenCaptureError) as exc:
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

        if (
            self.closing
            and self.shutdown_deadline is not None
            and monotonic() >= self.shutdown_deadline
            and (self.future is not None or self.capture_future is not None)
            and not self.shutdown_reported
        ):
            self.shutdown_reported = True
            print("Ожидание фоновой задачи при завершении службы.", file=sys.stderr)

        if self.closing and self.future is None and self.capture_future is None:
            self.hud.root.destroy()
        else:
            self.hud.root.after(25, self.poll)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.shutdown_deadline = monotonic() + 3
        self.cancel()
        self.screenshots.close()
        self.sessions.close()
        for listener in self.listeners:
            listener.stop()

    def _poll_screenshot(self):
        if self.screenshot_id is None:
            return
        if self.cancel_requested.is_set() or self.close_requested.is_set():
            self.screenshot_discard = True
            self.screenshots.cancel()
        for event in self.screenshots.drain():
            if event.task_id != self.screenshot_id or self.screenshot_discard:
                continue
            if event.status is TaskStatus.CHUNK:
                self.hud.update_text(event.text, "ЗАПРОС GEMINI")
            elif event.status is TaskStatus.FAILED:
                self.hud.update_text(event.error.message, "ОШИБКА")
            elif event.status is TaskStatus.COMPLETED:
                result = event.text or ""
                if self.settings.memory_enabled and result and self.screenshot_session:
                    mode, generation = self.screenshot_session
                    question, separator, answer = result.partition("Ответ:")
                    if separator and question.strip().startswith("Вопрос:"):
                        self.sessions.add(
                            mode,
                            question.removeprefix("Вопрос:"),
                            answer,
                            generation,
                            self.cancel_requested,
                        )
                if self.managed:
                    test_count = len(self.sessions.snapshot("TEST")[1])
                    interview_count = len(self.sessions.snapshot("INTERVIEW")[1])
                    print(f"GHOST_SESSION {test_count} {interview_count}", flush=True)
                self.hud.update_text(result, f"[{self.context.mode}] ГОТОВО")
        if not self.screenshots.busy:
            if self.screenshot_discard:
                self.hud.update_text(status="ГОТОВО")
            self.screenshot_id = None
            self.screenshot_session = None
            self.future = None

    def watch_parent(self, fd):
        try:
            while True:
                command = os.read(fd, 1)
                if not command:
                    break
                if command == b"C":
                    self.enqueue(self.cancel)
                elif command == b"S":
                    self.hotkeys_suspended.set()
                elif command == b"R":
                    self.hotkeys_suspended.clear()
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
            action = self.hotkeys.button_action(button)
            if pressed and action:
                self.enqueue(self.capture if action == "screen" else self.audio)

        def dispatch_hotkey(action):
            if not self.hotkeys_suspended.is_set():
                self.enqueue(action)

        try:
            self.listeners = [
                self.hotkeys.create_keyboard(
                    {
                        key: lambda action=action: dispatch_hotkey(action)
                        for key, action in bindings.items()
                    }
                ),
                self.hotkeys.create_mouse(on_click=click),
            ]
            self.listeners[0].start_verified()
            self.listeners[1].start()
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
    configure_dpi()
    GhostAssistApp(settings).run("--managed" in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
