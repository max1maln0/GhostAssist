import os
import sys
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic

from config.settings import Settings, validate_hotkeys
from core.controller import ApplicationController, ViewKind
from platform_adapters.windows.composition import build_dependencies, configure_dpi
from platform_adapters.windows.single_instance import SingleInstance
from ui.snipping_tool import SnippingOverlay
from ui.stealth_hud import StealthHUD
from ui.transcript_review import TranscriptReview


class GhostAssistApp:
    def __init__(self, settings, dependencies=None):
        dependencies = dependencies or build_dependencies(settings)
        self.settings = settings
        self.controller = ApplicationController(settings, dependencies)
        self.llm = dependencies.llm
        self.screen_capture = dependencies.screen
        self.audio_capture = dependencies.audio
        self.hotkeys = dependencies.hotkeys
        self.hud = StealthHUD()
        self.commands = Queue(maxsize=32)
        self.cancel_requested = Event()
        self.close_requested = Event()
        self.hotkeys_suspended = Event()
        self.listeners = []
        self.listener_failed = False
        self.managed = False
        self.overlay = None
        self.review = None
        self.closing = False
        self.shutdown_deadline = None
        self.shutdown_reported = False
        self.hud.root.protocol("WM_DELETE_WINDOW", self.close)

    @property
    def sessions(self):
        return self.controller.sessions

    @property
    def future(self):
        return self.controller.orchestrator.future

    def enqueue(self, action):
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
        self.controller.capture()

    def snip(self):
        self.controller.snip()

    def audio(self):
        self.controller.audio()

    def switch_mode(self):
        self.controller.switch_mode()

    def _dismiss(self):
        if self.overlay:
            self.overlay.close()
            self.overlay = None
        if self.review:
            self.review.destroy()
            self.review = None

    def cancel(self):
        self.controller.cancel()
        self._dismiss()

    def cancel_review(self):
        self.controller.cancel_current()
        self._dismiss()

    def _select(self, task_id):
        def selected(bbox):
            self.overlay = None
            self.controller.selected(task_id, bbox)

        try:
            self.overlay = SnippingOverlay(self.hud.root, selected)
            return True
        except Exception:  # noqa: BLE001 - UI boundary
            self.controller.cancel_current()
            self.hud.update_text("Не удалось открыть окно выделения.", "ОШИБКА")
            return False

    def _review(self, task_id, text):
        def submit(question):
            if self.controller.confirm(task_id, question):
                if self.review:
                    self.review.destroy()
                    self.review = None

        self.review = TranscriptReview(self.hud.root, text, submit, self.cancel_review)
        if self.settings.auto_send_transcript:

            def auto_submit():
                if (
                    self.review
                    and self.controller.active
                    and self.controller.active.request.task_id == task_id
                ):
                    self.review.submit()
                    if self.review:
                        self.hud.root.after(25, auto_submit)

            self.review.after(0, auto_submit)

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
            while not self.commands.empty():
                try:
                    self.commands.get_nowait()
                except Empty:
                    break
        if not self.closing:
            for _ in range(32):
                try:
                    self.commands.get_nowait()()
                except Empty:
                    break
        for event in self.controller.poll(self.cancel_requested):
            # An input hook can request cancellation while controller.poll runs.
            if self.cancel_requested.is_set() or self.close_requested.is_set():
                self.cancel()
                break
            if event.kind is ViewKind.DISPLAY:
                self.hud.update_text(event.text, event.status)
            elif event.kind is ViewKind.SELECT and not self.closing:
                if not self._select(event.task_id):
                    break
            elif event.kind is ViewKind.REVIEW and not self.closing:
                try:
                    self._review(event.task_id, event.text)
                except Exception:  # noqa: BLE001 - isolate a failing UI adapter
                    self.controller.cancel_current()
                    self._dismiss()
                    self.hud.update_text(
                        "Не удалось открыть проверку текста.", "ОШИБКА"
                    )
                    break
            elif event.kind is ViewKind.DISMISS:
                self._dismiss()
        if self.managed:
            counts = tuple(
                len(self.sessions.snapshot(mode)[1]) for mode in ("TEST", "INTERVIEW")
            )
            if counts != getattr(self, "_last_counts", None):
                self._last_counts = counts
                print(f"GHOST_SESSION {counts[0]} {counts[1]}", flush=True)
        if self.closing:
            if self.controller.stopped:
                self.hud.root.destroy()
                return
            if monotonic() >= self.shutdown_deadline and not self.shutdown_reported:
                self.shutdown_reported = True
                print("Ожидание фоновой задачи при завершении службы.", file=sys.stderr)
        self.hud.root.after(25, self.poll)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.shutdown_deadline = monotonic() + 3
        self.controller.close()
        self._dismiss()
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
