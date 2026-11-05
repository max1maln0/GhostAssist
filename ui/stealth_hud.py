import ctypes
import textwrap
import tkinter as tk
from ctypes import wintypes
from tkinter import font

from ui.snipping_tool import exclude_from_capture, window_handle

# Win32 константы
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000  # Запрет перехвата фокуса клавиатуры
WS_EX_TRANSPARENT = 0x00000020  # Сквозной клик (мышь полностью игнорирует окно)


class StealthHUD:
    def __init__(self, width=820, initial_height=90, max_height=480):
        self.width = width
        self.initial_height = initial_height
        self.max_height = max_height
        self.is_visible = True
        self.current_height = initial_height

        self.root = tk.Tk()
        self._setup_window()
        self._setup_widgets()
        self._apply_win32_styles()

    def _setup_window(self):
        self.root.title("GhostHUD")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.root.configure(bg="#030712")

        # Стартовая позиция: верх экрана по центру
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        self.width = min(self.width, self.screen_w - 32)
        self.max_height = min(self.max_height, self.screen_h - 64)
        self.pos_x = (self.screen_w - self.width) // 2
        self.pos_y = 16
        self.is_docked_top = True

        self.root.geometry(
            f"{self.width}x{self.initial_height}+{self.pos_x}+{self.pos_y}"
        )

    def _setup_widgets(self):
        self.border_frame = tk.Frame(self.root, bg="#1e293b", padx=1, pady=1)
        self.border_frame.pack(fill=tk.BOTH, expand=True)

        self.container = tk.Frame(self.border_frame, bg="#080e1a", padx=14, pady=8)
        self.container.pack(fill=tk.BOTH, expand=True)

        # Верхняя статусная строка
        self.header = tk.Frame(self.container, bg="#080e1a")
        self.header.pack(fill=tk.X, pady=(0, 4))

        self.status_label = tk.Label(
            self.header,
            text="● [TEST] READY",
            font=("Consolas", 9, "bold"),
            fg="#10b981",
            bg="#080e1a",
        )
        self.status_label.pack(side=tk.LEFT)

        self.hint_label = tk.Label(
            self.header,
            text="GhostAssist",
            font=("Segoe UI", 8),
            fg="#475569",
            bg="#080e1a",
        )
        self.hint_label.pack(side=tk.RIGHT)

        # Моноширинное текстовое поле с автопереносом строк
        self.text_widget = tk.Text(
            self.container,
            font=("Consolas", 10),
            fg="#cbd5e1",
            bg="#080e1a",
            bd=0,
            highlightthickness=0,
            wrap=tk.WORD,
        )
        self.text_widget.pack(fill=tk.BOTH, expand=True)
        self.text_widget.insert("1.0", "Ожидание входящей задачи...")
        self.text_widget.config(state=tk.DISABLED)

    def _apply_win32_styles(self):
        self.root.update_idletasks()
        hwnd = window_handle(self.root)
        user32 = ctypes.windll.user32
        user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
        if not exclude_from_capture(hwnd):
            print("Windows не применил ограничение захвата HUD.")
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT
        )

    def _adjust_window_size(self, text):
        hud_font = font.Font(font=self.text_widget["font"])
        columns = max(1, (self.width - 36) // hud_font.measure("M"))
        height = hud_font.metrics("linespace")
        lines = sum(
            max(1, len(textwrap.wrap(line, width=columns))) for line in text.split("\n")
        )
        self.current_height = max(
            self.initial_height, min(lines * height + 44, self.max_height)
        )
        self._position()

    def _position(self):
        y = 16 if self.is_docked_top else self.screen_h - self.current_height - 35
        self.root.geometry(f"{self.width}x{self.current_height}+{self.pos_x}+{y}")

    def update_text(self, text=None, status=None):
        if status is not None:
            color = "#10b981" if "ГОТОВО" in status else "#f59e0b"
            if status == "ОШИБКА":
                color = "#f43f5e"
            self.status_label.config(text=status, fg=color)
        if text is not None:
            self.text_widget.config(state=tk.NORMAL)
            self.text_widget.delete("1.0", tk.END)
            self.text_widget.insert("1.0", text)
            self.text_widget.config(state=tk.DISABLED)
            self.text_widget.see("1.0")
            self._adjust_window_size(text)

    def toggle_visibility(self):
        self.is_visible = not self.is_visible
        self.root.attributes("-alpha", 0.92 if self.is_visible else 0.0)

    def toggle_dock_position(self):
        self.is_docked_top = not self.is_docked_top
        self._position()

    def scroll(self, pages):
        self.text_widget.yview_scroll(pages, "pages")

    def run(self):
        self.root.mainloop()
