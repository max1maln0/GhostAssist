"""Windows keyboard listener with an explicit hook installation handshake.

Pynput marks its listener ready before installing the Win32 hook and does not
check the returned hook handle. Keep this small Windows-specific adapter in
step with pynput's message-loop implementation when upgrading the dependency.
"""

from threading import Event

from pynput import keyboard
from pynput._util.win32 import MessageLoop, SystemHook


class VerifiedGlobalHotKeys(keyboard.GlobalHotKeys):
    def __init__(self, bindings):
        super().__init__(bindings)
        self.hook_ready = Event()
        self.hook_installed = False

    def _run(self):
        try:
            self._message_loop = MessageLoop()
            with self._receive():
                self._mark_ready()
                self._message_loop.start()
                if not self.running:
                    return
                with SystemHook(self._EVENTS, self._handler) as hook:
                    if not hook._hook:
                        return
                    self.hook_installed = True
                    self.hook_ready.set()
                    for msg in self._message_loop:
                        if not self.running:
                            break
                        if msg.message == self._WM_PROCESS:
                            self._process(msg.wParam, msg.lParam)
                        elif msg.message in self._WM_NOTIFICATIONS:
                            self._on_notification(msg.message, msg.wParam, msg.lParam)
        except Exception:  # noqa: BLE001 - failure is reported without sensitive details
            pass
        finally:
            self.hook_ready.set()

    def start_verified(self, timeout=1.0):
        self.start()
        if (
            not self.hook_ready.wait(timeout)
            or not self.hook_installed
            or not self.is_alive()
        ):
            self.stop()
            self.join(timeout=0.2)
            raise OSError("Не удалось установить глобальный клавиатурный hook.")
