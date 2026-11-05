"""Hotkey capture state and reset tooltip."""

import tkinter as tk

from config.settings import format_hotkey

MODIFIERS = {
    "control_l": "ctrl",
    "control_r": "ctrl",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt_l": "alt",
    "alt_r": "alt",
}
SPECIAL = {
    "escape": "esc",
    "return": "enter",
    "prior": "page_up",
    "next": "page_down",
    "space": "space",
}


class HotkeyCapture:
    def __init__(self):
        self.held = set()
        self.main_keys = []
        self.candidate = None

    def reset(self):
        self.held.clear()
        self.main_keys.clear()
        self.candidate = None

    def _combo(self):
        mods = {MODIFIERS[key] for key in self.held if key in MODIFIERS}
        parts = [f"<{name}>" for name in ("ctrl", "shift", "alt") if name in mods]
        if self.main_keys:
            key = SPECIAL.get(self.main_keys[-1], self.main_keys[-1])
            parts.append(key if len(key) == 1 else f"<{key}>")
        return "+".join(parts)

    def display(self):
        combo = self._combo() if self.main_keys else None
        if not combo and self.candidate:
            return format_hotkey(self.candidate)
        if combo:
            return format_hotkey(combo)
        mods = self._combo()
        return f"{format_hotkey(mods)} + ..." if mods else "Нажмите комбинацию..."

    def press(self, key):
        key = key.lower()
        if key == "escape":
            return "cancel", None
        if key in self.held:
            return None, None
        self.held.add(key)
        if key not in MODIFIERS:
            self.main_keys.append(key)
        if self.main_keys:
            self.candidate = self._combo()
        return None, None

    def release(self, key):
        key = key.lower()
        if key not in self.held:
            return None, None
        self.held.remove(key)
        if key in self.main_keys:
            self.main_keys.remove(key)
        if self.main_keys:
            self.candidate = self._combo()
        if not self.held and self.candidate:
            value = self.candidate
            self.reset()
            return "complete", value
        return None, None


class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.timer = None
        self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, event=None):
        self.hide()
        self.timer = self.widget.after(500, self.show)

    def show(self):
        self.timer = None
        self.window = tk.Toplevel(self.widget)
        self.window.overrideredirect(True)
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window.geometry(f"{x:+d}{y:+d}")
        tk.Label(
            self.window,
            text=self.text,
            bg="#1e293b",
            fg="#e2e8f0",
            padx=8,
            pady=4,
            font=("Segoe UI", 9),
        ).pack()

    def hide(self, event=None):
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window is not None:
            self.window.destroy()
            self.window = None
