import tkinter as tk
import ctypes
import queue

# Константы Win32 API
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Скрывает окно со всех записей и демонстраций экрана

class StealthHUD:
    def __init__(self):
        self.root = tk.Tk()
        self.msg_queue = queue.Queue()
        self.is_visible = True
        self._setup_window()
        self._apply_win32_stealth()
        self._poll_queue()

    def _setup_window(self):
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.88)
        self.root.configure(bg="#051c3b")  # Тёмный фон с хорошей читаемостью

        # Размещаем узкую плашку по центру сверху (прямо под веб-камерой)
        screen_w = self.root.winfo_screenwidth()
        width, height = 520, 95
        x_pos = (screen_w // 2) - (width // 2)
        self.root.geometry(f"{width}x{height}+{x_pos}+8")

        self.status_label = tk.Label(
            self.root, text="● [TEST MODE] READY", fg="#10b981", bg="#051c3b",
            font=("Consolas", 9, "bold"), anchor="w"
        )
        self.status_label.pack(fill="x", padx=8, pady=(4, 0))

        self.answer_label = tk.Label(
            self.root, text="Ожидание триггера...", fg="#ffffff", bg="#051c3b",
            font=("Segoe UI", 11, "bold"), wraplength=500, justify="left", anchor="nw"
        )
        self.answer_label.pack(fill="both", expand=True, padx=8, pady=4)

    def _apply_win32_stealth(self):
        self.root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        if not hwnd:
            hwnd = self.root.winfo_id()

        # 1. Запрещаем забирать фокус у активного окна, убираем из Alt+Tab и делаем кликабельным насквозь
        ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        new_style = ex_style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)

        # 2. Делаем окно невидимым для захвата экрана (Screen Share / Прокторинг)
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    def update_text(self, text: str, status: str = None):
        self.msg_queue.put(("TEXT", text, status))

    def toggle_visibility(self):
        self.msg_queue.put(("TOGGLE", None, None))

    def _poll_queue(self):
        try:
            while True:
                action, text, status = self.msg_queue.get_nowait()
                if action == "TEXT":
                    if text is not None:
                        self.answer_label.config(text=text)
                    if status is not None:
                        self.status_label.config(text=status)
                elif action == "TOGGLE":
                    self.is_visible = not self.is_visible
                    self.root.attributes("-alpha", 0.88 if self.is_visible else 0.0)
        except queue.Empty:
            pass
        self.root.after(40, self._poll_queue)

    def run(self):
        self.root.mainloop()