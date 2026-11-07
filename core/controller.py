"""UI-independent scenarios. Public commands and poll run on one consumer thread."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from threading import Event
from time import monotonic
from uuid import UUID

from core.audio_queue import AudioError, SegmentQueue
from core.context_manager import ContextManager
from core.contracts import TaskError, TaskRequest, TaskStatus
from core.orchestrator import AwaitInput, Lifecycle, TaskFailure
from core.platform_contracts import ScreenCaptureError
from core.provider_contracts import ProviderError
from core.session_manager import SessionManager


class ViewKind(str, Enum):
    DISPLAY = "display"
    SELECT = "select"
    REVIEW = "review"
    DISMISS = "dismiss"


@dataclass(frozen=True)
class ViewEvent:
    kind: ViewKind
    task_id: UUID | None = None
    text: str | None = field(default=None, repr=False)
    status: str | None = None


@dataclass
class Operation:
    request: TaskRequest
    kind: str
    mode: str
    generation: int
    prompt: str = field(repr=False)
    history: tuple = field(repr=False)
    stop: Event = field(default_factory=Event)
    question: str | None = field(default=None, repr=False)
    discard: bool = False


class ApplicationController:
    def __init__(self, settings, dependencies):
        self.settings = settings
        self.dependencies = dependencies
        self.orchestrator = dependencies.orchestrator
        self.context = ContextManager(settings)
        self.sessions = SessionManager(depth=settings.memory_depth)
        self.active = None
        self._views = deque(maxlen=64)
        self._source = ThreadPoolExecutor(max_workers=1)
        self.source_future = None
        self.source_stop = None
        self.segments = None
        self.source_error = None
        self.dropped = 0
        self.closing = False

    @property
    def monitoring(self):
        return self.source_future is not None and not self.source_stop.is_set()

    @property
    def busy(self):
        return (
            self.active is not None
            or self.orchestrator.busy
            or self.source_future is not None
            or self.segments is not None
        )

    @property
    def stopped(self):
        return (
            self.active is None
            and not self.orchestrator.busy
            and self.source_future is None
        )

    def _display(self, text=None, status=None):
        self._views.append(
            ViewEvent(
                ViewKind.DISPLAY,
                self.active.request.task_id if self.active else None,
                text,
                status,
            )
        )

    def _begin(self, kind, segment=False):
        if self.closing or self.active is not None or self.orchestrator.busy:
            return False
        if not segment and (
            self.source_future is not None or self.segments is not None
        ):
            return False
        if not segment:
            self.source_error = None
            self.dropped = 0
        mode = "INTERVIEW" if kind == "audio" else self.context.mode
        generation, history = self.sessions.snapshot(mode)
        request = TaskRequest("", "audio/wav" if kind == "audio" else "image/jpeg")
        self.orchestrator.prepare(request)
        self.active = Operation(
            request,
            kind,
            mode,
            generation,
            self.context.get_system_prompt(mode),
            history if self.settings.memory_enabled else (),
        )
        return True

    def _start(self, handler, timeout):
        def safe(request, cancel, publish):
            if cancel.is_set():
                return ""
            try:
                return handler(request, cancel, publish)
            except ProviderError as exc:
                raise TaskFailure(TaskError(exc.code.value, str(exc))) from None
            except (AudioError, ScreenCaptureError) as exc:
                raise TaskFailure(TaskError("capture", str(exc))) from None

        self.orchestrator.start(safe, timeout)

    def capture(self):
        if self._begin("screen"):
            self._screen()

    def snip(self):
        if self._begin("screen"):
            self._views.append(ViewEvent(ViewKind.SELECT, self.active.request.task_id))
            self._display("", "ВЫДЕЛИТЕ ОБЛАСТЬ")

    def selected(self, task_id, bbox):
        if (
            not self.active
            or self.active.request.task_id != task_id
            or self.orchestrator.state is not Lifecycle.CREATED
        ):
            return
        if bbox is None:
            self.cancel_current()
        else:
            self._screen(bbox)

    def _screen(self, bbox=None):
        operation = self.active
        self._display("", "СКАН ЭКРАНА")

        def work(request, cancel, publish):
            start = monotonic()
            data = (
                self.dependencies.screen.capture(bbox)
                if bbox is not None
                else self.dependencies.screen.capture()
            )
            if cancel.is_set():
                return ""
            return self.dependencies.llm.generate(
                data,
                "image/jpeg",
                operation.prompt,
                lambda text: publish(TaskStatus.CHUNK, text),
                cancel,
                history=operation.history,
                budget=self.settings.task_timeout - (monotonic() - start),
            )

        self._start(work, self.settings.task_timeout)

    def audio(self):
        if self.settings.vad_enabled:
            if self.source_future is not None:
                self.cancel()
            elif not self.busy and not self.closing:
                self._monitor()
            return
        if (
            self.active
            and self.active.kind == "audio"
            and self.orchestrator.state is Lifecycle.RUNNING
        ):
            self.active.stop.set()
            return
        if self._begin("audio"):
            self._transcribe()

    def _transcribe(self, data=None):
        operation = self.active
        self._display("", "СЛУШАЮ СИСТЕМУ" if data is None else "VAD · ТРАНСКРИБАЦИЯ")

        def work(request, cancel, publish):
            audio = data
            if audio is None:
                audio = self.dependencies.audio.record(
                    operation.stop,
                    cancel,
                    self.settings.audio_max_seconds,
                    self.settings.audio_max_mib * 1024 * 1024,
                    self.settings.audio_device,
                    vad_options=None,
                )
            if cancel.is_set():
                return ""
            operation.stop.set()
            publish(TaskStatus.PROGRESS, "ТРАНСКРИБАЦИЯ")
            text = self.dependencies.llm.transcribe(audio, cancel)
            if cancel.is_set():
                return ""
            if not text or not text.strip():
                raise TaskFailure(TaskError("transcription", "Речь не распознана."))
            return AwaitInput(text)

        timeout = self.settings.task_timeout + (
            self.settings.audio_max_seconds if data is None else 0
        )
        self._start(work, timeout)

    def confirm(self, task_id, question):
        if (
            not self.active
            or self.active.request.task_id != task_id
            or self.orchestrator.state is not Lifecycle.WAITING
            or not question.strip()
        ):
            return False
        if self.orchestrator.future and not self.orchestrator.future.done():
            return False
        operation = self.active
        operation.question = question.strip()
        self._display(question, "ЗАПРОС GEMINI")

        def work(request, cancel, publish):
            return self.dependencies.llm.generate(
                operation.question.encode(),
                "text/plain",
                operation.prompt,
                lambda text: publish(TaskStatus.CHUNK, text),
                cancel,
                history=operation.history,
                instruction="Ответь на вопрос пользователя.",
            )

        self._start(work, self.settings.task_timeout)
        return True

    def _monitor(self):
        self.source_stop = Event()
        self.source_error = None
        self.dropped = 0
        limit = self.settings.audio_max_mib * 1024 * 1024
        self.segments = SegmentQueue(limit - limit // 5)
        queue, stop = self.segments, self.source_stop
        options = {
            "min_speech_ms": self.settings.vad_min_speech_ms,
            "silence_ms": self.settings.vad_silence_ms,
            "max_seconds": self.settings.vad_max_seconds,
            "sensitivity": self.settings.vad_sensitivity,
        }
        self.source_future = self._source.submit(
            self.dependencies.audio.record,
            stop,
            stop,
            self.settings.audio_max_seconds,
            limit // 5,
            self.settings.audio_device,
            vad_options=options,
            on_segment=queue.put,
        )
        self._display("", "VAD · ЗАПИСЬ")

    def cancel_current(self):
        if self.active:
            self.active.discard = True
            self.active.stop.set()
            self.orchestrator.cancel()
            self._views.clear()
            self._views.append(ViewEvent(ViewKind.DISMISS, self.active.request.task_id))
            self._display("Задача отменена.", "ОТМЕНА")

    def cancel(self):
        if self.source_stop:
            self.source_stop.set()
        if self.segments:
            self.segments.close()
            self.segments = None
        self.source_error = None
        self.cancel_current()

    def switch_mode(self):
        if not self.busy and not self.closing:
            mode = self.context.toggle_mode()
            self._display(f"Режим {mode}", f"[{mode}] ГОТОВО")

    def poll(self, cancellation=None):
        # Events are consumed on the caller thread; no callbacks enter GUI workers.
        if cancellation is not None and cancellation.is_set():
            self.cancel()
        operation = self.active
        for event in self.orchestrator.drain():
            if cancellation is not None and cancellation.is_set():
                self.cancel()
            if (
                not operation
                or event.task_id != operation.request.task_id
                or operation.discard
            ):
                continue
            if event.status is TaskStatus.CHUNK:
                self._display(event.text, "ЗАПРОС GEMINI")
            elif event.status is TaskStatus.PROGRESS:
                self._display(status=event.text)
            elif event.status is TaskStatus.WAITING:
                self._views.append(
                    ViewEvent(ViewKind.REVIEW, event.task_id, event.text)
                )
                self._display(event.text, "ПРОВЕРЬТЕ ТРАНСКРИПЦИЮ")
            elif event.status is TaskStatus.FAILED:
                self._display(event.error.message, "ОШИБКА")
            elif event.status is TaskStatus.COMPLETED:
                result = event.text or ""
                question, separator, answer = result.partition("Ответ:")
                if operation.question:
                    question = operation.question
                    answer = answer if separator else result
                elif separator and question.strip().startswith("Вопрос:"):
                    question = question.strip().removeprefix("Вопрос:")
                else:
                    question, answer = "", ""
                if self.settings.memory_enabled and question and answer:
                    self.sessions.add(
                        operation.mode,
                        question,
                        answer,
                        operation.generation,
                        cancellation if cancellation is not None else Event(),
                    )
                self._display(
                    result,
                    "VAD · СЛУШАЮ"
                    if self.monitoring
                    else f"[{self.context.mode}] ГОТОВО",
                )
        if operation and not self.orchestrator.busy:
            self.active = None
        if self.source_future is not None and self.source_future.done():
            try:
                self.source_future.result()
            except AudioError as exc:
                self.source_error = str(exc)
            except Exception:  # noqa: BLE001 - source boundary, no private details
                self.source_error = "Ошибка захвата звука."
            self.source_future = None
        if self.segments and not self.closing:
            self.dropped += self.segments.take_overflow()
            if self.active is None:
                segment = self.segments.get()
                if segment is not None and self._begin("audio", segment=True):
                    self._transcribe(segment[1])
                elif self.source_future is None:
                    self.segments.close()
                    self.segments = None
        if self.source_error:
            self._display(status=f"ОШИБКА ЗАХВАТА: {self.source_error}")
        if self.dropped and self.segments is not None:
            self._display(status=f"ПРОПУЩЕНО СЕГМЕНТОВ: {self.dropped}")
        result = list(self._views)
        self._views.clear()
        return result

    def close(self):
        if not self.closing:
            self.closing = True
            self.cancel()
            self.orchestrator.close()
            self._source.shutdown(wait=False, cancel_futures=True)
            self.sessions.close()
