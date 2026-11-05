import ctypes
import tkinter as tk
from ctypes import wintypes

import mss


def window_handle(root):
    get_ancestor = ctypes.windll.user32.GetAncestor
    get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
    get_ancestor.restype = wintypes.HWND
    return get_ancestor(root.winfo_id(), 2) or root.winfo_id()


def exclude_from_capture(hwnd):
    affinity = ctypes.windll.user32.SetWindowDisplayAffinity
    affinity.argtypes = (wintypes.HWND, wintypes.DWORD)
    affinity.restype = wintypes.BOOL
    return affinity(hwnd, 0x11) or affinity(hwnd, 0x01)


class SnippingOverlay:
    def __init__(self, parent, callback):
        self.callback = callback
        self.start = None
        self.rect = None
        with mss.mss() as capture:
            self.bounds = capture.monitors[0]
        self.root = tk.Toplevel(parent)
        try:
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", 0.28)
            self.canvas = tk.Canvas(
                self.root, cursor="crosshair", bg="#000000", highlightthickness=0
            )
            self.canvas.pack(fill=tk.BOTH, expand=True)
            self.canvas.bind("<ButtonPress-1>", self.press)
            self.canvas.bind("<B1-Motion>", self.drag)
            self.canvas.bind("<ButtonRelease-1>", self.release)
            self.root.bind("<Escape>", lambda event: self.finish(None))
            self.root.update_idletasks()
            hwnd = window_handle(self.root)
            position = ctypes.windll.user32.SetWindowPos
            position.argtypes = (
                wintypes.HWND,
                wintypes.HWND,
                *([ctypes.c_int] * 4),
                wintypes.UINT,
            )
            b = self.bounds
            if not position(
                hwnd, -1, b["left"], b["top"], b["width"], b["height"], 0x40
            ):
                raise OSError("SetWindowPos")
            exclude_from_capture(hwnd)
            self.root.focus_force()
        except Exception:
            self.close()
            raise

    def press(self, event):
        self.start = event.x, event.y
        if self.rect is not None:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            *self.start, *self.start, outline="#10b981", width=2, fill="#047857"
        )

    def drag(self, event):
        if self.start is not None:
            self.canvas.coords(self.rect, *self.start, event.x, event.y)

    def release(self, event):
        if self.start is None:
            return
        b = self.bounds
        x1, x2 = sorted(max(0, min(x, b["width"])) for x in (self.start[0], event.x))
        y1, y2 = sorted(max(0, min(y, b["height"])) for y in (self.start[1], event.y))
        bbox = None
        if x2 - x1 > 15 and y2 - y1 > 15:
            bbox = {
                "left": b["left"] + x1,
                "top": b["top"] + y1,
                "width": x2 - x1,
                "height": y2 - y1,
            }
        self.finish(bbox)

    def close(self):
        self.root.destroy()

    def finish(self, bbox):
        self.close()
        self.callback(bbox)
