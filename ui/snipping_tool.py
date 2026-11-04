import tkinter as tk
import ctypes
from ctypes import wintypes

WDA_EXCLUDEFROMCAPTURE = 0x00000011


class SnippingOverlay:
    def __init__(self, on_snippet_taken_callback):
        self.callback = on_snippet_taken_callback
        self.start_x = None
        self.start_y = None
        self.cur_x = None
        self.cur_y = None

        self.root = tk.Toplevel()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.28)  # Легкое полупрозрачное затемнение
        self.root.config(bg="#000000")
        self.root.config(cursor="crosshair")

        # На весь экран
        w = self.root.winfo_screenwidth()
        h = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+0+0")

        # Применяем защиту от записи экрана
        self._apply_stealth_affinity()

        # Канвас для рисования рамки
        self.canvas = tk.Canvas(self.root, cursor="crosshair", bg="#000000", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_button_press)
        self.canvas.bind("<B1-Motion>", self._on_move_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_button_release)
        self.root.bind("<Escape>", lambda e: self._cancel())

        self.rect_id = None

    def _apply_stealth_affinity(self):
        try:
            self.root.update()
            GA_ROOT = 2
            hwnd = ctypes.windll.user32.GetAncestor(self.root.winfo_id(), GA_ROOT)
            if not hwnd:
                hwnd = self.root.winfo_id()
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception as e:
            print(f"[SNIP WARN] Affinity error: {e}")

    def _on_button_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect_id = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="#10b981", width=2, fill="#047857"
        )

    def _on_move_press(self, event):
        self.cur_x, self.cur_y = (event.x, event.y)
        self.canvas.coords(self.rect_id, self.start_x, self.start_y, self.cur_x, self.cur_y)

    def _on_button_release(self, event):
        end_x = event.x
        end_y = event.y

        # Вычисляем нормализованный bbox (top, left, width, height)
        x1 = min(self.start_x, end_x)
        y1 = min(self.start_y, end_y)
        x2 = max(self.start_x, end_x)
        y2 = max(self.start_y, end_y)

        width = x2 - x1
        height = y2 - y1

        self.root.destroy()

        # Игнорируем случайный единичный клик без растягивания
        if width > 15 and height > 15:
            bbox = {"top": y1, "left": x1, "width": width, "height": height}
            self.callback(bbox)

    def _cancel(self):
        self.root.destroy()