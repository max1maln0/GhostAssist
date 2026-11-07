"""Dashboard keyboard lifecycle and interactive capture."""

import ctypes
import os
from ctypes import wintypes
from queue import Full
from tkinter import messagebox

from config.settings import (
    HotkeyValidationError,
    Settings,
    format_hotkey,
    validate_hotkeys,
)
from ui.dashboard_theme import ACCENT_GREEN, BORDER_COLOR
from ui.hotkey_capture import HotkeyCapture


class HotkeyActions:
    def _enqueue_tray(self, command):
        if command == "exit":
            self.exit_requested.set()
            return
        if command == "toggle" and self.capturing_hotkey:
            return
        try:
            self.tray_commands.put_nowait(command)
        except Full:
            pass

    def _create_dashboard_hotkey(self, settings):
        validate_hotkeys(settings)
        listener = self.hotkeys.create_keyboard(
            {settings.hotkey_dashboard: lambda: self._enqueue_tray("toggle")}
        )
        listener.start_verified()
        return listener

    def _replace_dashboard_hotkey(self, listener):
        previous = self.dashboard_hotkey
        self.dashboard_hotkey = listener
        if previous:
            previous.stop()
            previous.join(timeout=0)

    def _signal_hotkey_capture(self, active):
        if self._service_running():
            try:
                self.assistant_process.stdin.write(b"S" if active else b"R")
                self.assistant_process.stdin.flush()
            except (OSError, ValueError):
                pass

    def _end_hotkey_capture(self):
        if self.active_hotkey is None:
            return
        button = self.hotkey_buttons[self.active_hotkey]
        value = getattr(self.settings, self.active_hotkey.lower())
        button.configure(
            text=format_hotkey(value), border_color=BORDER_COLOR, text_color="#94a3b8"
        )
        for sequence, binding in self.hotkey_bindings.items():
            self.unbind(sequence, binding)
        self.hotkey_bindings.clear()
        self.active_hotkey = None
        self.hotkey_capture = None
        self.capturing_hotkey = False
        self._signal_hotkey_capture(False)

    def _hotkey_focus_lost(self, event):
        def check():
            if not self.active_hotkey or self.hotkey_warning:
                return
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetWindowThreadProcessId.argtypes = (
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            )
            foreground = user32.GetForegroundWindow()
            process_id = wintypes.DWORD()
            if foreground:
                user32.GetWindowThreadProcessId(foreground, ctypes.byref(process_id))
                if process_id.value and process_id.value != os.getpid():
                    self._end_hotkey_capture()

        self.after(200, check)

    def _hotkey_error(self, message):
        self.hotkey_capture.reset()
        self.hotkey_buttons[self.active_hotkey].configure(
            text=self.hotkey_capture.display()
        )
        self.hotkey_warning = True
        try:
            messagebox.showwarning("Хоткей", message, parent=self)
        finally:
            self.hotkey_warning = False
            if self.active_hotkey:
                self.hotkey_buttons[self.active_hotkey].focus_set()

    def _start_hotkey_capture(self, env_key):
        if self.external_service:
            self.set_error(
                "Сначала завершите внешнюю службу: Dashboard не может приостановить её горячие клавиши."
            )
            return
        if self.active_hotkey == env_key:
            self._end_hotkey_capture()
            return
        self._end_hotkey_capture()
        self.capturing_hotkey = True
        self.active_hotkey = env_key
        self.hotkey_capture = HotkeyCapture()
        button = self.hotkey_buttons[env_key]
        button.configure(
            text=self.hotkey_capture.display(),
            border_color=ACCENT_GREEN,
            text_color=ACCENT_GREEN,
        )
        self._signal_hotkey_capture(True)

        def on_press(event):
            if self.hotkey_warning:
                return "break"
            action, _ = self.hotkey_capture.press(event.keysym)
            if action == "cancel":
                self._end_hotkey_capture()
            else:
                button.configure(text=self.hotkey_capture.display())
            return "break"

        def on_release(event):
            if self.hotkey_warning:
                return "break"
            action, combo = self.hotkey_capture.release(event.keysym)
            if action != "complete":
                button.configure(text=self.hotkey_capture.display())
                return "break"
            try:
                validate_hotkeys(self.settings, {env_key.lower(): combo})
            except HotkeyValidationError as exc:
                self._hotkey_error(str(exc))
                return "break"
            self.hotkey_warning = True
            capture = self.hotkey_capture

            def completed(saved):
                self.hotkey_warning = False
                if self.hotkey_capture is not capture:
                    return
                if saved:
                    self._end_hotkey_capture()
                else:
                    capture.reset()
                    button.configure(text=capture.display())
                    button.focus_set()

            if not self.request_save({env_key: combo}, completed):
                completed(False)
            return "break"

        self.hotkey_bindings = {
            "<KeyPress>": self.bind("<KeyPress>", on_press, add="+"),
            "<KeyRelease>": self.bind("<KeyRelease>", on_release, add="+"),
            "<FocusOut>": self.bind("<FocusOut>", self._hotkey_focus_lost, add="+"),
        }
        button.focus_set()

    def _reset_hotkey(self, env_key):
        field = env_key.lower()
        value = Settings.model_fields[field].default
        try:
            validate_hotkeys(self.settings, {field: value})
        except HotkeyValidationError as exc:
            if self.active_hotkey:
                self._hotkey_error(str(exc))
            else:
                messagebox.showwarning("Хоткей", str(exc), parent=self)
            return

        def completed(saved):
            if saved:
                self._end_hotkey_capture()
                self.hotkey_buttons[env_key].configure(text=format_hotkey(value))

        self.request_save({env_key: value}, completed)
