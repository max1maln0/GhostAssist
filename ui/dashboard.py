import subprocess
import sys
import time
from tkinter import messagebox

import customtkinter as ctk
from pydantic import ValidationError

from config.env_file import update_env
from config.settings import PROFILES, ROOT, Settings, profile_path, validate_hotkeys

# Фирменная тёмная палитра
BG_DARK = "#030712"  # Глубокий обсидиан
SURFACE_CARD = "#090f1d"  # Цвет карточек Bento
SURFACE_INNER = "#040812"  # Внутренние поля ввода и селекторы
ACCENT_GREEN = "#10b981"  # Изумрудный акцент
ACCENT_HOVER = "#059669"  # Ховер акцента
TEXT_MUTED = "#64748b"  # Приглушенный текст
BORDER_COLOR = "#1e293b"  # Тонкие разделители


class DashboardApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("GhostAssist // Control Center")
        self.geometry("1040x740")
        self.minsize(960, 680)
        self.configure(fg_color=BG_DARK)

        ctk.set_appearance_mode("dark")

        self.assistant_process = None
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
            text="АКТИВИРОВАТЬ ПЕРЕХВАТ",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=ACCENT_GREEN,
            hover_color=ACCENT_HOVER,
            text_color="#021c14",
            height=36,
            corner_radius=8,
            command=self.toggle_assistant_process,
        )
        self.run_btn.pack(fill="x", padx=16, pady=(0, 12))

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
            values=[
                "gemini-3.5-flash-lite",
                "gemini-flash-latest",
                "gemini-3.5-flash",
                "gemini-3.1-flash-lite",
            ],
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
            values=["1280x720 (Быстро, ~45 КБ)", "1920x1080 (Четко, ~90 КБ)"],
            fg_color=SURFACE_INNER,
            border_color=BORDER_COLOR,
            button_color=BORDER_COLOR,
            command=self._on_resolution_changed,
        )
        current_res = f"{self.settings.crop_width}x{self.settings.crop_height}"
        self.res_combo.set(
            "1280x720 (Быстро, ~45 КБ)"
            if "1280" in current_res
            else "1920x1080 (Четко, ~90 КБ)"
        )
        self.res_combo.pack(fill="x", padx=16, pady=(2, 12))

        # Карточка 3: Полный список горячих клавиш (все 6 биндов)
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

        # Все 6 действий системы
        hotkey_items = [
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

    def _start_hotkey_capture(self, env_key):
        button = self.hotkey_buttons[env_key]
        button.focus_set()

        def capture(event):
            key = event.keysym.lower()
            if key.startswith(("alt_", "control_", "shift_")):
                return "break"
            self.unbind("<KeyPress>")
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
                    "Хоткей", "Комбинация недоступна или уже назначена."
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
                [sys.executable, str(ROOT / "main.py"), "--managed"],
                cwd=ROOT,
                stdin=subprocess.PIPE,
            )
        except OSError:
            self._show_status("НЕ УДАЛОСЬ ЗАПУСТИТЬ СЛУЖБУ")
            return
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
        process = self.assistant_process
        if process:
            code = process.poll()
            if code is not None:
                process.stdin.close()
                self.assistant_process = None
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
        if self.closing and self.assistant_process is None:
            self.destroy()
        else:
            self.after(150, self._poll_process)

    def on_closing(self):
        self.closing = True
        self.restart_pending = False
        if self.assistant_process:
            self._stop_process()
        else:
            self.destroy()


if __name__ == "__main__":
    DashboardApp().mainloop()
