import queue
import tkinter as tk
import ctypes
from ctypes import wintypes

WDA_EXCLUDEFROMCAPTURE = 0x00000011


class StealthHUD:
    def __init__(self, width=820, initial_height=90, max_height=400):
        self.width = width
        self.initial_height = initial_height
        self.max_height = max_height
        self.is_visible = True
        self.msg_queue = queue.Queue()

        self.root = tk.Tk()
        self._setup_window()
        self._setup_widgets()
        self._apply_stealth_affinity()

        self.root.after(30, self._poll_queue)

    def _setup_window(self):
        self.root.title("GhostHUD")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.root.configure(bg="#030712")

        screen_w = self.root.winfo_screenwidth()
        self.pos_x = (screen_w - self.width) // 2
        self.pos_y = 16

        self.root.geometry(f"{self.width}x{self.initial_height}+{self.pos_x}+{self.pos_y}")

    def _setup_widgets(self):
        # Внешняя рамка с эффектом тонкого бордера
        self.border_frame = tk.Frame(self.root, bg="#1e293b", padx=1, pady=1)
        self.border_frame.pack(fill=tk.BOTH, expand=True)

        self.container = tk.Frame(self.border_frame, bg="#080e1a", padx=16, pady=10)
        self.container.pack(fill=tk.BOTH, expand=True)

        # Хедер плашки
        header = tk.Frame(self.container, bg="#080e1a")
        header.pack(fill=tk.X, pady=(0, 4))

        self.status_label = tk.Label(
            header,
            text="● [TEST] READY",
            font=("Consolas", 9, "bold"),
            fg="#10b981",
            bg="#080e1a"
        )
        self.status_label.pack(side=tk.LEFT)

        hint_label = tk.Label(
            header,
            text="Mouse4: Скрин | Mouse5: Аудио | Alt+M: Режим",
            font=("Segoe UI", 8),
            fg="#475569",
            bg="#080e1a"
        )
        hint_label.pack(side=tk.RIGHT)

        # Блок для текста вопроса и ответа
        wrap_w = self.width - 36
        self.content_label = tk.Label(
            self.container,
            text="Ожидание входящей задачи...",
            font=("Segoe UI", 11),
            fg="#cbd5e1",
            bg="#080e1a",
            anchor="nw",
            justify=tk.LEFT,
            wraplength=wrap_w
        )
        self.content_label.pack(fill=tk.BOTH, expand=True)

    def _apply_stealth_affinity(self):
        try:
            self.root.update_idletasks()
            hwnd = wintypes.HWND(self.root.winfo_id())
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception as e:
            print(f"[HUD ERROR] {e}")

    def _adjust_window_size(self):
        self.root.update_idletasks()
        req_height = self.container.winfo_reqheight() + 8
        target_height = max(self.initial_height, min(req_height, self.max_height))
        self.root.geometry(f"{self.width}x{target_height}+{self.pos_x}+{self.pos_y}")

    def update_text(self, text: str = None, status: str = None):
        self.msg_queue.put(("TEXT", text, status))

    def toggle_visibility(self):
        self.msg_queue.put(("TOGGLE", None, None))

    def _poll_queue(self):
        try:
            while True:
                item = self.msg_queue.get_nowait()
                action = item[0]

                if action == "TEXT":
                    _, text, status = item
                    if status:
                        self.status_label.config(text=status)
                        if "СЛУШАЮ" in status or "ЗАПРОС" in status:
                            self.status_label.config(fg="#f59e0b")
                        elif "READY" in status or "ГОТОВО" in status:
                            self.status_label.config(fg="#10b981")
                    if text:
                        self.content_label.config(text=text)
                    self._adjust_window_size()

                elif action == "TOGGLE":
                    self.is_visible = not self.is_visible
                    self.root.attributes("-alpha", 0.92 if self.is_visible else 0.0)

        except queue.Empty:
            pass
        self.root.after(30, self._poll_queue)

    def run(self):
        self.root.mainloop()