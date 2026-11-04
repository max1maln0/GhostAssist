import queue
import tkinter as tk
import ctypes
from ctypes import wintypes

# Win32 API константы для stealth-режима
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


class StealthHUD:
    def __init__(self, width=820, initial_height=110, max_height=420):
        self.width = width
        self.initial_height = initial_height
        self.max_height = max_height
        self.is_visible = True
        self.msg_queue = queue.Queue()

        self.root = tk.Tk()
        self._setup_window()
        self._setup_widgets()
        self._apply_stealth_affinity()

        # Запуск цикла опроса очереди
        self.root.after(30, self._poll_queue)

    def _setup_window(self):
        self.root.title("GhostHUD")
        self.root.overrideredirect(True)  # Убираем рамки Windows
        self.root.attributes("-topmost", True)  # Всегда поверх окон
        self.root.attributes("-alpha", 0.90)  # Полупрозрачность
        self.root.configure(bg="#0b0f19")

        # Центрируем плашку вверху экрана
        screen_w = self.root.winfo_screenwidth()
        self.pos_x = (screen_w - self.width) // 2
        self.pos_y = 18

        self.root.geometry(f"{self.width}x{self.initial_height}+{self.pos_x}+{self.pos_y}")

    def _setup_widgets(self):
        # Контейнер с внутренними отступами
        self.container = tk.Frame(self.root, bg="#0b0f19", padx=16, pady=10)
        self.container.pack(fill=tk.BOTH, expand=True)

        # Верхняя статусная строка (режим, состояние)
        self.status_label = tk.Label(
            self.container,
            text="● [READY]",
            font=("Segoe UI", 9, "bold"),
            fg="#10b981",  # Изумрудный акцент
            bg="#0b0f19",
            anchor="w"
        )
        self.status_label.pack(fill=tk.X, pady=(0, 4))

        # Основной текст ответа: wraplength переносит текст, не вылезая за границы
        wrap_width = self.width - 40
        self.answer_label = tk.Label(
            self.container,
            text="Ожидание триггера...",
            font=("Segoe UI", 11),
            fg="#e2e8f0",
            bg="#0b0f19",
            anchor="nw",
            justify=tk.LEFT,
            wraplength=wrap_width
        )
        self.answer_label.pack(fill=tk.BOTH, expand=True)

    def _apply_stealth_affinity(self):
        """Прячет окно от систем видеозахвата, скриншотов и демонстрации экрана."""
        try:
            self.root.update_idletasks()
            hwnd = wintypes.HWND(self.root.winfo_id())
            user32 = ctypes.windll.user32
            res = user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            if res:
                print("[HUD] Stealth Affinity активирован (невидим для захвата экрана).")
            else:
                print(f"[HUD WARN] SetWindowDisplayAffinity вернул 0. Код ошибки: {ctypes.GetLastError()}")
        except Exception as e:
            print(f"[HUD ERROR] Ошибка применения stealth-атрибута: {e}")

    def _adjust_window_size(self):
        """Динамически пересчитывает высоту окна под размер входящего текста."""
        self.root.update_idletasks()
        
        # Получаем реальную требуемую высоту виджетов
        req_height = self.container.winfo_reqheight() + 10
        target_height = max(self.initial_height, min(req_height, self.max_height))
        
        # Обновляем геометрию без сдвига по X и Y
        self.root.geometry(f"{self.width}x{target_height}+{self.pos_x}+{self.pos_y}")

    def update_text(self, text: str = None, status: str = None):
        """Потокобезопасная отправка данных в UI через очередь."""
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
                    if text:
                        self.answer_label.config(text=text)
                    self._adjust_window_size()

                elif action == "TOGGLE":
                    self.is_visible = not self.is_visible
                    self.root.attributes("-alpha", 0.90 if self.is_visible else 0.0)

        except queue.Empty:
            pass
        self.root.after(30, self._poll_queue)

    def run(self):
        self.root.mainloop()