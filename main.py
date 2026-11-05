import ctypes
import sys
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic

from pynput import keyboard, mouse

from config.settings import Settings, validate_hotkeys
from core.audio_worker import AudioError, record_wav
from core.context_manager import ContextManager
from core.llm_worker import GenerationError, LLMWorker
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
        self.commands = Queue(maxsize=32)
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.overlay = None
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
        if self.tasks.state is State.RECORDING:
            self.tasks.active.stop.set()
            self.tasks.active.state = State.PROCESSING
            self.hud.update_text(status="АНАЛИЗ ЗВУКА")
            return
        task = self.tasks.begin(State.RECORDING)
        if task:
            self.submit(task, audio=True)

    def submit(self, task, bbox=None, audio=False):
        prompt = self.context.get_system_prompt(mode="INTERVIEW" if audio else None)
        self.hud.update_text("", "СЛУШАЮ СИСТЕМУ" if audio else "СКАН ЭКРАНА")

        def work():
            started = monotonic()
            if audio:
                data = record_wav(
                    task.stop,
                    task.cancel,
                    self.settings.audio_max_seconds,
                    self.settings.audio_max_mib * 1024 * 1024,
                )
            else:
                data = self.llm.capture_screen(bbox)
            if task.cancel.is_set():
                return None
            task.stop.set()
            return self.llm.generate(
                data,
                "audio/wav" if audio else "image/jpeg",
                prompt,
                task.publish,
                task.cancel,
                budget=self.settings.task_timeout
                - (0 if audio else monotonic() - started),
            )

        self.future = self.worker.submit(work)

    def cancel(self):
        task = self.tasks.active
        if not task:
            return
        self.tasks.cancel()
        self.hud.update_text("Задача отменена.", "ОТМЕНА")
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
                    if not task.cancel.is_set():
                        self.hud.update_text(result, f"[{self.context.mode}] ГОТОВО")
                except (AudioError, GenerationError) as exc:
                    if not task.cancel.is_set():
                        self.hud.update_text(str(exc), "ОШИБКА")
                except Exception:  # noqa: BLE001 - isolate the application boundary
                    if not task.cancel.is_set():
                        self.hud.update_text("Не удалось обработать задачу.", "ОШИБКА")
                finally:
                    if task.cancel.is_set():
                        self.hud.update_text(status="ГОТОВО")
                    self.tasks.finish(task)
                    self.future = None
            elif not task.cancel.is_set():
                if task.state is State.RECORDING and task.stop.is_set():
                    task.state = State.PROCESSING
                    self.hud.update_text(status="АНАЛИЗ ЗВУКА")
                preview = task.preview()
                if preview is not None:
                    task.state = State.STREAMING
                    self.hud.update_text(preview, "ЗАПРОС GEMINI")
        if self.closing and self.future is None:
            self.hud.root.destroy()
        else:
            self.hud.root.after(25, self.poll)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.cancel()
        for listener in self.listeners:
            listener.stop()

    def run(self, managed):
        settings = self.settings
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

                def watch_parent():
                    sys.stdin.readline()
                    self.enqueue(self.close)

                Thread(target=watch_parent, daemon=True).start()
            self.hud.root.after(25, self.poll)
            self.hud.run()
            if self.listener_failed:
                raise RuntimeError("Global input listener stopped unexpectedly")
        finally:
            self.close()
            self.worker.shutdown(wait=True)


def main():
    try:
        settings = Settings()
        validate_hotkeys(settings)
        if not settings.has_api_key:
            print("Укажите ключ Gemini в Dashboard или .env.", file=sys.stderr)
            return 2
        # Tk and MSS must see the same physical desktop coordinates.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        GhostAssistApp(settings).run("--managed" in sys.argv)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001 - isolate the application boundary
        print(
            "Не удалось запустить службу. Проверьте настройки и зависимости.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
