import subprocess
import sys
import time
from queue import Empty, Full, Queue
from threading import Event, Thread
from tkinter import messagebox

import customtkinter as ctk
from pydantic import ValidationError
from pynput import keyboard

from config.env_file import update_env
from config.settings import (
    GEMINI_MODELS,
    PROFILES,
    ROOT,
    Settings,
    profile_path,
    validate_hotkeys,
)
from core.audio_worker import AudioError, list_loopback_devices
from core.single_instance import SingleInstance
from ui.tray import Tray

# Фирменная тёмная палитра
BG_DARK = "#030712"  # Глубокий обсидиан
SURFACE_CARD = "#090f1d"  # Цвет карточек Bento
SURFACE_INNER = "#040812"  # Внутренние поля ввода и селекторы
ACCENT_GREEN = "#10b981"  # Изумрудный акцент
ACCENT_HOVER = "#059669"  # Ховер акцента
TEXT_MUTED = "#64748b"  # Приглушенный текст
BORDER_COLOR = "#1e293b"  # Тонкие разделители


class DashboardApp(ctk.CTk):
    def __init__(self, instance=None):
        super().__init__()
        self.instance = instance
        self.tray_commands = Queue(maxsize=64)
        self.exit_requested = Event()
        self.capturing_hotkey = False
        self.tray = Tray(self._enqueue_tray)
        self.dashboard_hotkey = None
        self.dashboard_visible = True

        self.title("GhostAssist // Control Center")
        self.geometry("1040x740")
        self.minsize(960, 680)
        self.configure(fg_color=BG_DARK)

        ctk.set_appearance_mode("dark")

        self.assistant_process = None
        self.external_service = False
        self.session_updates = Queue(maxsize=32)
        self.device_updates = Queue(maxsize=1)
        self.device_refreshing = False
        self.stopping_until = None
        self.restart_pending = False
        self.closing = False
        try:
            self.settings = Settings()
        except ValidationError:
            self.settings = Settings.model_construct()
            self.after_idle(
                lambda: messagebox.showerror(
                    "Настройки", "Проверьте значения в .env перед запуском службы."
                )
            )

        self.hotkey_buttons = {}
        self._setup_layout()
        self._load_profiles_list()
        self._refresh_devices()
        self.tray.start()
        self._bind_dashboard_hotkey()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.after(150, self._poll_process)

    def _setup_layout(self):
        self.grid_columnconfigure(0, weight=4, minsize=400)
        self.grid_columnconfigure(1, weight=5)
        self.grid_rowconfigure(0, weight=1)

        # ==========================================
        # ЛЕВАЯ КОЛОНКА (Служба, Параметры, Хоткеи)
        # ==========================================
        left_container = ctk.CTkFrame(self, fg_color="transparent")
        left_container.grid(row=0, column=0, padx=(20, 10), pady=20, sticky="nsew")
        left_container.grid_rowconfigure(2, weight=1)
        left_container.grid_columnconfigure(0, weight=1)

        # Карточка 1: Служба и статус перехватчика
        service_card = ctk.CTkFrame(
            left_container,
            fg_color=SURFACE_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_COLOR,
        )
        service_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        brand_lbl = ctk.CTkLabel(
            service_card,
            text="GHOST // ASSIST",
            font=ctk.CTkFont(family="Consolas", size=18, weight="bold"),
            text_color=ACCENT_GREEN,
        )
        brand_lbl.pack(pady=(12, 4))

        # Pill Status Badge
        self.status_pill = ctk.CTkFrame(
            service_card,
            fg_color="#1a0d14",
            corner_radius=20,
            border_width=1,
            border_color="#451a24",
        )
        self.status_pill.pack(pady=(0, 10))

        self.status_text = ctk.CTkLabel(
            self.status_pill,
            text="● SERVICE OFFLINE",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#f43f5e",
        )
        self.status_text.pack(padx=14, pady=3)

        self.run_btn = ctk.CTkButton(
            service_card,
            text="ЗАПУСТИТЬ СЛУЖБУ",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=ACCENT_GREEN,
            hover_color=ACCENT_HOVER,
            text_color="#021c14",
            height=36,
            corner_radius=8,
            command=self.toggle_assistant_process,
        )
        self.run_btn.pack(fill="x", padx=16, pady=(0, 4))
        window_actions = ctk.CTkFrame(service_card, fg_color="transparent")
        window_actions.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkButton(
            window_actions,
            text="Скрыть в трей",
            command=self.hide_dashboard,
            fg_color=SURFACE_INNER,
            border_width=1,
            border_color=BORDER_COLOR,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            window_actions,
            text="Завершить GhostAssist",
            command=self.exit_app,
            fg_color=SURFACE_INNER,
            border_width=1,
            border_color=BORDER_COLOR,
        ).pack(side="left", fill="x", expand=True)
        self.close_switch = ctk.CTkSwitch(
            service_card, text="Закрывать в трей", command=self._on_close_changed
        )
        self.close_switch.pack(anchor="w", padx=16, pady=(0, 10))
        if self.settings.close_to_tray:
            self.close_switch.select()

        # Карточка 2: Параметры инференса и снимков
        config_card = ctk.CTkFrame(
            left_container,
            fg_color=SURFACE_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_COLOR,
        )
        config_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        ctk.CTkLabel(
            config_card,
            text="ПАРАМЕТРЫ ЗАПРОСОВ",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).pack(anchor="w", padx=16, pady=(10, 4))

        self.api_entry = ctk.CTkEntry(
            config_card, show="*", placeholder_text="Ключ Gemini API"
        )
        self.api_entry.pack(fill="x", padx=16, pady=2)
        ctk.CTkButton(config_card, text="Сохранить ключ", command=self._save_key).pack(
            anchor="w", padx=16, pady=(0, 4)
        )
        ctk.CTkLabel(config_card, text="Модель Gemini", font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=16
        )
        self.model_combo = ctk.CTkComboBox(
            config_card,
            values=list(GEMINI_MODELS),
            state="readonly",
            fg_color=SURFACE_INNER,
            border_color=BORDER_COLOR,
            button_color=BORDER_COLOR,
            command=self._on_model_changed,
        )
        self.model_combo.set(self.settings.gemini_model)
        self.model_combo.pack(fill="x", padx=16, pady=(2, 8))

        ctk.CTkLabel(
            config_card, text="Разрешение снимка (RAM)", font=ctk.CTkFont(size=12)
        ).pack(anchor="w", padx=16)
        self.res_combo = ctk.CTkComboBox(
            config_card,
            values=["1280x720", "1920x1080"],
            state="readonly",
            fg_color=SURFACE_INNER,
            border_color=BORDER_COLOR,
            button_color=BORDER_COLOR,
            command=self._on_resolution_changed,
        )
        self.res_combo.set(f"{self.settings.crop_width}x{self.settings.crop_height}")
        self.res_combo.pack(fill="x", padx=16, pady=(2, 12))

        ctk.CTkLabel(config_card, text="Устройство системного звука").pack(
            anchor="w", padx=16
        )
        self.audio_devices = ["По умолчанию"]
        self.device_combo = ctk.CTkComboBox(
            config_card,
            values=self.audio_devices,
            state="readonly",
            command=self._on_device_changed,
        )
        self.device_combo.set(self.settings.audio_device or "По умолчанию")
        self.device_combo.pack(fill="x", padx=16, pady=(2, 6))
        self.refresh_devices_button = ctk.CTkButton(
            config_card, text="Обновить устройства", command=self._refresh_devices
        )
        self.refresh_devices_button.pack(anchor="w", padx=16, pady=(0, 4))

        self.vad_switch = ctk.CTkSwitch(
            config_card,
            text="VAD при записи · учитывайте согласие участников",
            command=self._on_vad_changed,
        )
        self.vad_switch.pack(anchor="w", padx=16, pady=4)
        if self.settings.vad_enabled:
            self.vad_switch.select()

        self.auto_send_switch = ctk.CTkSwitch(
            config_card,
            text="Автоматически отправлять распознанное",
            command=self._on_auto_send_changed,
        )
        self.auto_send_switch.pack(anchor="w", padx=16, pady=4)
        if self.settings.auto_send_transcript:
            self.auto_send_switch.select()

        session_row = ctk.CTkFrame(config_card, fg_color="transparent")
        session_row.pack(fill="x", padx=16, pady=(0, 6))
        self.memory_switch = ctk.CTkSwitch(
            session_row, text="Память", command=self._on_memory_changed
        )
        self.memory_switch.pack(side="left")
        if self.settings.memory_enabled:
            self.memory_switch.select()
        self.depth_combo = ctk.CTkComboBox(
            session_row,
            values=[str(n) for n in range(1, 11)],
            width=65,
            state="readonly",
            command=self._on_depth_changed,
        )
        self.depth_combo.set(str(self.settings.memory_depth))
        self.depth_combo.pack(side="left", padx=6)
        ctk.CTkButton(
            session_row,
            text="Очистить сессию",
            width=130,
            command=self._reset_session,
        ).pack(side="left", padx=6)
        self.session_status = ctk.CTkLabel(
            config_card, text="Сессия  TEST 0 · INTERVIEW 0"
        )
        self.session_status.pack(anchor="w", padx=16)

        # Карточка 3: Горячие клавиши
        hotkeys_card = ctk.CTkFrame(
            left_container,
            fg_color=SURFACE_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_COLOR,
        )
        hotkeys_card.grid(row=2, column=0, sticky="nsew")
        hotkeys_card.grid_rowconfigure(1, weight=1)
        hotkeys_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hotkeys_card,
            text="ГОРЯЧИЕ КЛАВИШИ (КЛИК ДЛЯ ЗАПИСИ)",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))

        # Скроллируемый контейнер для биндов (гарантирует идеальное отображение)
        hotkeys_scroll = ctk.CTkScrollableFrame(
            hotkeys_card,
            fg_color="transparent",
            scrollbar_button_color=BORDER_COLOR,
            scrollbar_button_hover_color="#334155",
        )
        hotkeys_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        hotkey_items = [
            (
                "HOTKEY_DASHBOARD",
                "Показать / скрыть Dashboard",
                self.settings.hotkey_dashboard,
            ),
            ("HOTKEY_SCREEN", "Фуллскрин скрин", self.settings.hotkey_screen),
            ("HOTKEY_SNIP", "Снайпер (выделение)", self.settings.hotkey_snip),
            ("HOTKEY_AUDIO", "Звук (Loopback)", self.settings.hotkey_audio),
            ("HOTKEY_MODE", "TEST ↔ INTERVIEW", self.settings.hotkey_mode),
            ("HOTKEY_HUD", "Скрыть / Показать HUD", self.settings.hotkey_hud),
            ("HOTKEY_CANCEL", "Отменить задачу", self.settings.hotkey_cancel),
            ("HOTKEY_PAGE_UP", "Прокрутка HUD вверх", self.settings.hotkey_page_up),
            ("HOTKEY_PAGE_DOWN", "Прокрутка HUD вниз", self.settings.hotkey_page_down),
            ("HOTKEY_DOCK", "Сместить (Верх / Низ)", self.settings.hotkey_dock),
        ]

        for env_key, label_text, default_val in hotkey_items:
            row = ctk.CTkFrame(hotkeys_scroll, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=3)

            ctk.CTkLabel(
                row, text=label_text, font=ctk.CTkFont(size=11), text_color="#94a3b8"
            ).pack(side="left")

            btn = ctk.CTkButton(
                row,
                text=default_val,
                font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                width=120,
                height=26,
                fg_color=SURFACE_INNER,
                border_width=1,
                border_color=BORDER_COLOR,
                hover_color="#1e293b",
                text_color=ACCENT_GREEN,
                corner_radius=6,
                command=lambda k=env_key: self._start_hotkey_capture(k),
            )
            btn.pack(side="right")
            self.hotkey_buttons[env_key] = btn

        # ==========================================
        # ПРАВАЯ КОЛОНКА (Редактор легенды кандидата)
        # ==========================================
        right_card = ctk.CTkFrame(
            self,
            fg_color=SURFACE_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_COLOR,
        )
        right_card.grid(row=0, column=1, padx=(10, 20), pady=20, sticky="nsew")
        right_card.grid_rowconfigure(1, weight=1)
        right_card.grid_columnconfigure(0, weight=1)

        # Шапка редактора
        editor_header = ctk.CTkFrame(right_card, fg_color="transparent")
        editor_header.grid(row=0, column=0, padx=18, pady=(14, 10), sticky="ew")

        ctk.CTkLabel(
            editor_header,
            text="ЛЕГЕНДА КАНДИДАТА",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#f8fafc",
        ).pack(side="left")

        self.save_btn = ctk.CTkButton(
            editor_header,
            text="Сохранить",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=90,
            height=30,
            fg_color=ACCENT_GREEN,
            hover_color=ACCENT_HOVER,
            text_color="#021c14",
            corner_radius=6,
            command=self.save_current_legend,
        )
        self.save_btn.pack(side="right", padx=(8, 0))

        self.new_profile_btn = ctk.CTkButton(
            editor_header,
            text="+ Новый",
            font=ctk.CTkFont(size=12),
            width=75,
            height=30,
            fg_color="#1e293b",
            hover_color="#334155",
            corner_radius=6,
            command=self.create_new_profile,
        )
        self.new_profile_btn.pack(side="right", padx=(8, 0))

        self.profile_selector = ctk.CTkOptionMenu(
            editor_header,
            values=["example_legend.md"],
            fg_color=SURFACE_INNER,
            button_color=BORDER_COLOR,
            dropdown_fg_color=SURFACE_CARD,
            width=170,
            height=30,
            command=self.load_selected_profile,
        )
        self.profile_selector.pack(side="right")

        # Текстовое поле легенды под IDE
        self.legend_textbox = ctk.CTkTextbox(
            right_card,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=SURFACE_INNER,
            border_width=1,
            border_color=BORDER_COLOR,
            corner_radius=8,
            text_color="#e2e8f0",
            wrap="word",
        )
        self.legend_textbox.grid(row=1, column=0, padx=18, pady=(0, 18), sticky="nsew")

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

    def _bind_dashboard_hotkey(self):
        if self.dashboard_hotkey:
            self.dashboard_hotkey.stop()
            self.dashboard_hotkey.join(timeout=1)
            self.dashboard_hotkey = None
        try:
            validate_hotkeys(self.settings)
            self.dashboard_hotkey = keyboard.GlobalHotKeys(
                {self.settings.hotkey_dashboard: lambda: self._enqueue_tray("toggle")}
            )
            self.dashboard_hotkey.start()
        except (ValueError, OSError, RuntimeError):
            self.after_idle(
                lambda: messagebox.showerror(
                    "Хоткей", "Проверьте настройки глобальных сочетаний."
                )
            )

    def hide_dashboard(self):
        if not self.tray.ready.is_set():
            messagebox.showwarning("Трей", "Значок ещё не готов. Повторите попытку.")
            return
        self.withdraw()
        self.dashboard_visible = False
        self.tray.visible = False
        if self.tray.ready.is_set():
            self.tray.icon.update_menu()

    def show_dashboard(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        self.dashboard_visible = True
        self.tray.visible = True
        if self.tray.ready.is_set():
            self.tray.icon.update_menu()

    def _on_close_changed(self):
        self._save(CLOSE_TO_TRAY=str(bool(self.close_switch.get())).lower())

    def _start_hotkey_capture(self, env_key):
        self.capturing_hotkey = True
        button = self.hotkey_buttons[env_key]
        button.focus_set()

        def capture(event):
            key = event.keysym.lower()
            if key.startswith(("alt_", "control_", "shift_")):
                return "break"
            self.unbind("<KeyPress>")
            self.capturing_hotkey = False
            mods = [
                name
                for mask, name in ((1, "<shift>"), (4, "<ctrl>"), (0x20000, "<alt>"))
                if event.state & mask
            ]
            special = {
                "escape": "esc",
                "return": "enter",
                "prior": "page_up",
                "next": "page_down",
            }
            key = special.get(key, key)
            safe_single = key in {
                "pause",
                "scroll_lock",
                *(f"f{i}" for i in range(1, 13)),
            }
            if not mods and not safe_single:
                messagebox.showwarning(
                    "Хоткей", "Добавьте модификатор или выберите F1–F12."
                )
                return "break"
            combo = "+".join([*mods, key if len(key) == 1 else f"<{key}>"])
            try:
                validate_hotkeys(self.settings, {env_key.lower(): combo})
            except ValueError:
                messagebox.showwarning(
                    "Хоткей", "Комбинация системная, недоступна или уже назначена."
                )
                return "break"
            if self._save(**{env_key: combo}):
                button.configure(text=combo)
            return "break"

        self.bind("<KeyPress>", capture)

    def _load_profiles_list(self):
        PROFILES.mkdir(parents=True, exist_ok=True)
        files = []
        for path in sorted(PROFILES.glob("*.md")):
            try:
                if profile_path(path.name).is_file():
                    files.append(path.name)
            except ValueError:
                continue
        self.profile_selector.configure(values=files)
        if files:
            target = (
                self.settings.active_profile
                if self.settings.active_profile in files
                else files[0]
            )
            self.profile_selector.set(target)
            self.load_selected_profile(target, save=False)

    def load_selected_profile(self, name, save=True):
        try:
            content = profile_path(name).read_text(encoding="utf-8")
        except (OSError, ValueError):
            messagebox.showerror("Профиль", "Не удалось прочитать профиль.")
            return
        self.legend_textbox.delete("1.0", "end")
        self.legend_textbox.insert("1.0", content)
        if save:
            self._save(ACTIVE_PROFILE=name)

    def save_current_legend(self):
        name = self.profile_selector.get()
        try:
            profile_path(name).write_text(
                self.legend_textbox.get("1.0", "end-1c"), encoding="utf-8"
            )
        except (OSError, ValueError):
            messagebox.showerror("Профиль", "Не удалось сохранить профиль.")
            return
        self._save(ACTIVE_PROFILE=name)

    def create_new_profile(self):
        name = ctk.CTkInputDialog(
            text="Имя файла профиля .md", title="Новый профиль"
        ).get_input()
        if not name:
            return
        if not name.lower().endswith(".md"):
            name += ".md"
        try:
            with profile_path(name).open("x", encoding="utf-8") as profile:
                profile.write(f"# Профиль {name}\n")
        except (ValueError, OSError):
            messagebox.showerror("Профиль", "Некорректное или уже занятое имя файла.")
            return
        self._load_profiles_list()
        self.profile_selector.set(name)
        self.load_selected_profile(name)

    def _save_key(self):
        key = self.api_entry.get().strip()
        if not key or not key.isascii() or any(char.isspace() for char in key):
            messagebox.showwarning("Gemini", "Введите ключ Gemini API.")
            return
        if self._save(GEMINI_API_KEY=key):
            self.api_entry.delete(0, "end")
            if not self.assistant_process:
                self._show_status("КЛЮЧ СОХРАНЁН")

    def _refresh_devices(self):
        if self.device_refreshing:
            return
        self.device_refreshing = True
        self.refresh_devices_button.configure(state="disabled")

        def scan():
            try:
                result = (list_loopback_devices(), None)
            except AudioError:
                result = ([], "Не удалось получить список устройств WASAPI.")
            try:
                self.device_updates.put_nowait(result)
            except Full:
                pass

        Thread(target=scan, daemon=True).start()

    def _on_auto_send_changed(self):
        self._save(AUTO_SEND_TRANSCRIPT=str(bool(self.auto_send_switch.get())).lower())

    def _on_vad_changed(self):
        self._save(VAD_ENABLED=str(bool(self.vad_switch.get())).lower())

    def _on_device_changed(self, value):
        self._save(AUDIO_DEVICE="" if value == "По умолчанию" else value)

    def _on_memory_changed(self):
        self._save(MEMORY_ENABLED=str(bool(self.memory_switch.get())).lower())

    def _on_depth_changed(self, value):
        self._save(MEMORY_DEPTH=value)

    def _reset_session(self):
        if self.assistant_process and self.assistant_process.poll() is None:
            self.restart_pending = True
            self._stop_process()
        else:
            self.session_status.configure(text="Сессия  TEST 0 · INTERVIEW 0")

    def _on_model_changed(self, model):
        self._save(GEMINI_MODEL=model)

    def _on_resolution_changed(self, value):
        width, height = ("1920", "1080") if "1920" in value else ("1280", "720")
        self._save(CROP_WIDTH=width, CROP_HEIGHT=height)

    def _save(self, **values):
        try:
            Settings(**{key.lower(): value for key, value in values.items()})
            update_env(values)
            self.settings = Settings()
        except (ValueError, OSError):
            messagebox.showerror("Настройки", "Проверьте файл .env и права на запись.")
            return False
        if "HOTKEY_DASHBOARD" in values:
            self._bind_dashboard_hotkey()
        dashboard_only = {"HOTKEY_DASHBOARD", "CLOSE_TO_TRAY"}
        if not values.keys() <= dashboard_only:
            if self.assistant_process and self.assistant_process.poll() is None:
                self.restart_pending = True
                self._stop_process()
        return True

    def _show_status(self, text, active=False):
        self.status_pill.configure(fg_color="#032617" if active else "#1a0d14")
        self.status_text.configure(
            text=f"● {text}", text_color=ACCENT_GREEN if active else "#f43f5e"
        )
        self.run_btn.configure(
            text="ОСТАНОВИТЬ СЛУЖБУ" if active else "ЗАПУСТИТЬ СЛУЖБУ",
            fg_color="#e11d48" if active else ACCENT_GREEN,
            hover_color="#be123c" if active else ACCENT_HOVER,
            text_color="#ffffff" if active else "#021c14",
        )

    def toggle_assistant_process(self):
        if self.assistant_process and self.assistant_process.poll() is not None:
            self.assistant_process.stdin.close()
            self.assistant_process = None
        if self.assistant_process:
            self.restart_pending = False
            self._stop_process()
            return
        try:
            if SingleInstance.exists("service"):
                messagebox.showinfo(
                    "Служба",
                    "Служба уже запущена отдельно. Завершите её перед запуском из Dashboard.",
                )
                return
            self.settings = Settings()
            validate_hotkeys(self.settings)
        except ValueError:
            messagebox.showerror("Настройки", "Проверьте .env и комбинации клавиш.")
            return
        if not self.settings.has_api_key:
            messagebox.showwarning(
                "Gemini", "Укажите ключ Gemini API в Dashboard или .env."
            )
            return
        try:
            self.assistant_process = subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "main.py"), "--managed"],
                cwd=ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
        except OSError:
            self._show_status("НЕ УДАЛОСЬ ЗАПУСТИТЬ СЛУЖБУ")
            return
        process = self.assistant_process
        self.session_status.configure(text="Сессия  TEST 0 · INTERVIEW 0")

        def read_status():
            for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").strip().split()
                if (
                    len(line) == 3
                    and line[0] == "GHOST_SESSION"
                    and all(count.isdecimal() for count in line[1:])
                ):
                    try:
                        self.session_updates.put_nowait((process, line[1], line[2]))
                    except Full:
                        pass

        def read_and_close_status():
            try:
                read_status()
            finally:
                process.stdout.close()

        Thread(target=read_and_close_status, daemon=True).start()
        self._show_status("СЛУЖБА ЗАПУЩЕНА", active=True)

    def _stop_process(self):
        process = self.assistant_process
        if process and self.stopping_until is None:
            self.stopping_until = time.monotonic() + 4
            self.run_btn.configure(state="disabled")
            self.status_text.configure(text="● ОСТАНОВКА СЛУЖБЫ")
            try:
                process.stdin.close()
            except OSError:
                pass

    def _poll_process(self):
        if self.exit_requested.is_set() and not self.closing:
            self.exit_app()
            if self.assistant_process is None:
                return
        if self.instance and self.instance.activated() and not self.closing:
            self.show_dashboard()
        for _ in range(64):
            try:
                command = self.tray_commands.get_nowait()
            except Empty:
                break
            if self.closing:
                continue
            if command == "show":
                self.show_dashboard()
            elif command == "hide":
                self.hide_dashboard()
            elif command == "toggle":
                (
                    self.hide_dashboard
                    if self.dashboard_visible
                    else self.show_dashboard
                )()
            elif command == "start" and not self._service_running():
                self.toggle_assistant_process()
            elif command == "stop" and self._service_running():
                self.restart_pending = False
                self._stop_process()
            elif command == "cancel" and self._service_running():
                try:
                    self.assistant_process.stdin.write(b"C")
                    self.assistant_process.stdin.flush()
                except (OSError, ValueError):
                    pass
            elif command == "exit":
                self.exit_app()
                if self.assistant_process is None:
                    return
        try:
            devices, error = self.device_updates.get_nowait()
        except Empty:
            pass
        else:
            self.device_refreshing = False
            self.refresh_devices_button.configure(state="normal")
            self.audio_devices = ["По умолчанию", *devices]
            self.device_combo.configure(values=self.audio_devices)
            if error and not self.closing:
                messagebox.showwarning("Устройства", error)
        while True:
            try:
                source, test_count, interview_count = self.session_updates.get_nowait()
            except Empty:
                break
            if source is self.assistant_process:
                self.session_status.configure(
                    text=f"Сессия  TEST {test_count} · INTERVIEW {interview_count}"
                )
        process = self.assistant_process
        if process:
            code = process.poll()
            if code is not None:
                process.stdin.close()
                self.assistant_process = None
                self.session_status.configure(text="Сессия  TEST 0 · INTERVIEW 0")
                self.stopping_until = None
                self.run_btn.configure(state="normal")
                self._show_status(
                    "СЛУЖБА ОСТАНОВЛЕНА" if code == 0 else f"СБОЙ СЛУЖБЫ ({code})"
                )
                if self.restart_pending and not self.closing:
                    self.restart_pending = False
                    self.toggle_assistant_process()
            elif (
                self.stopping_until is not None
                and time.monotonic() >= self.stopping_until
            ):
                try:
                    process.kill()
                except OSError:
                    if process.poll() is None:
                        self.status_text.configure(
                            text="● НЕ УДАЛОСЬ ОСТАНОВИТЬ СЛУЖБУ"
                        )
        running = self._service_running()
        try:
            external = not running and SingleInstance.exists("service")
        except OSError:
            external = False
        changed = (
            self.tray.running != running or self.tray.externally_running != external
        )
        if self.external_service != external:
            self.external_service = external
            if self.assistant_process is None and not self.closing:
                self._show_status(
                    "СЛУЖБА ЗАПУЩЕНА ОТДЕЛЬНО" if external else "СЛУЖБА ОСТАНОВЛЕНА"
                )
        if changed:
            self.tray.running = running
            self.tray.externally_running = external
            if self.tray.ready.is_set():
                self.tray.icon.update_menu()
        if self.closing and self.assistant_process is None:
            self._finish_exit()
        else:
            self.after(150, self._poll_process)

    def _service_running(self):
        return bool(self.assistant_process and self.assistant_process.poll() is None)

    def on_closing(self):
        if not self._window_exists:
            self.after(30, self.on_closing)
            return
        if self.settings.close_to_tray:
            self.hide_dashboard()
        else:
            self.exit_app()

    def exit_app(self):
        if not self._window_exists:
            self.after(30, self.exit_app)
            return
        if self.closing:
            return
        self.closing = True
        self.tray.closing = True
        self.restart_pending = False
        if self.assistant_process:
            self._stop_process()
        else:
            self._finish_exit()

    def _finish_exit(self):
        if self.dashboard_hotkey:
            self.dashboard_hotkey.stop()
            self.dashboard_hotkey = None
        self.tray.stop()
        self.destroy()


def main():
    instance = SingleInstance("dashboard")
    if not instance.primary:
        try:
            instance.activate()
        finally:
            instance.close()
        return
    try:
        DashboardApp(instance).mainloop()
    finally:
        instance.close()


if __name__ == "__main__":
    main()
