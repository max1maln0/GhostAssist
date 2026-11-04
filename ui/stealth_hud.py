import queue
import tkinter as tk
import ctypes
from ctypes import wintypes

# Win32 константы
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000      # Запрет перехвата фокуса клавиатуры
WS_EX_TRANSPARENT = 0x00000020     # Сквозной клик (мышь полностью игнорирует окно)
WDA_EXCLUDEFROMCAPTURE = 0x00000011 # Скрытие из OBS/Zoom/Meet (Windows 10 2004+)
WDA_MONITOR = 0x00000001            # Fallback для старых сборок Windows
GA_ROOT = 2                         # Дескриптор корневого окна верхнего уровня


class StealthHUD:
    def __init__(self, width=820, initial_height=90, max_height=480):
        self.width = width
        self.initial_height = initial_height
        self.max_height = max_height
        self.is_visible = True
        self.current_height = initial_height
        
        self.msg_queue = queue.Queue()

        self.root = tk.Tk()
        self._setup_window()
        self._setup_widgets()
        self._apply_win32_styles()

        self.root.after(30, self._poll_queue)

    def _setup_window(self):
        self.root.title("GhostHUD")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.root.configure(bg="#030712")

        # Стартовая позиция: верх экрана по центру
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        self.pos_x = (self.screen_w - self.width) // 2
        self.pos_y = 16
        self.is_docked_top = True

        self.root.geometry(f"{self.width}x{self.initial_height}+{self.pos_x}+{self.pos_y}")

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
            bg="#080e1a"
        )
        self.status_label.pack(side=tk.LEFT)

        self.hint_label = tk.Label(
            self.header,
            text="[Alt+Down]: Верх/Низ | [Alt+X]: Скрыть | Click-Through: ON",
            font=("Segoe UI", 8),
            fg="#475569",
            bg="#080e1a"
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
            wrap=tk.WORD
        )
        self.text_widget.pack(fill=tk.BOTH, expand=True)
        self.text_widget.insert("1.0", "Ожидание входящей задачи...")
        self.text_widget.config(state=tk.DISABLED)

    def _apply_win32_styles(self):
        """Гарантирует stealth-невидимость и 100% сквозной клик через окно."""
        try:
            self.root.update()
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            raw_id = self.root.winfo_id()
            hwnd = user32.GetAncestor(raw_id, GA_ROOT)
            if not hwnd:
                hwnd = raw_id

            # 1. Защита от захвата экрана и скриншотеров
            res = user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            if res == 0:
                err = kernel32.GetLastError()
                print(f"[STEALTH WARN] WDA_EXCLUDEFROMCAPTURE не применился (код {err}), пробуем WDA_MONITOR...")
                user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR)
            else:
                print(f"[STEALTH OK] Окно скрыто от захвата экрана (HWND: {hex(hwnd)})")

            # 2. Не забирать фокус + пропускать клики мыши насквозь в браузер/IDE
            current_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            new_style = current_style | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
            print("[STEALTH OK] Включен режим Click-Through (сквозной клик)")

        except Exception as e:
            print(f"[HUD ERROR] Ошибка настройки Win32 стилей: {e}")

    def _adjust_window_size(self):
        self.root.update_idletasks()
        num_lines = int(self.text_widget.index('end-1c').split('.')[0])
        line_height = 18
        calculated_height = max(self.initial_height, min(num_lines * line_height + 44, self.max_height))
        
        self.current_height = calculated_height

        if not self.is_docked_top:
            self.pos_y = self.screen_h - self.current_height - 35

        self.root.geometry(f"{self.width}x{self.current_height}+{self.pos_x}+{self.pos_y}")

    def update_text(self, text: str = None, status: str = None):
        self.msg_queue.put(("TEXT", text, status))

    def toggle_visibility(self):
        self.msg_queue.put(("TOGGLE", None, None))

    def toggle_dock_position(self):
        self.msg_queue.put(("DOCK_TOGGLE", None, None))

    def _poll_queue(self):
        try:
            while True:
                item = self.msg_queue.get_nowait()
                action = item[0]

                if action == "TEXT":
                    _, text, status = item
                    if status:
                        self.status_label.config(text=status)
                        if any(k in status for k in ("СЛУШАЮ", "ЗАПРОС", "СКАН", "АНАЛИЗ")):
                            self.status_label.config(fg="#f59e0b")
                        elif any(k in status for k in ("READY", "ГОТОВО", "ОТВЕТ")):
                            self.status_label.config(fg="#10b981")
                        elif "ОШИБКА" in status:
                            self.status_label.config(fg="#f43f5e")

                    if text:
                        self.text_widget.config(state=tk.NORMAL)
                        self.text_widget.delete("1.0", tk.END)
                        self.text_widget.insert("1.0", text)
                        self.text_widget.config(state=tk.DISABLED)
                    self._adjust_window_size()

                elif action == "TOGGLE":
                    self.is_visible = not self.is_visible
                    self.root.attributes("-alpha", 0.92 if self.is_visible else 0.0)

                elif action == "DOCK_TOGGLE":
                    if self.is_docked_top:
                        self.pos_y = self.screen_h - self.current_height - 35
                        self.is_docked_top = False
                    else:
                        self.pos_y = 16
                        self.is_docked_top = True
                    self.root.geometry(f"{self.width}x{self.current_height}+{self.pos_x}+{self.pos_y}")

        except queue.Empty:
            pass
        self.root.after(30, self._poll_queue)

    def run(self):
        self.root.mainloop()