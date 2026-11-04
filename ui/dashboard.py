import os
import subprocess
import sys
import customtkinter as ctk
from tkinter import messagebox
from config.settings import settings


# Фирменная палитра
BG_DARK = "#030712"       # Глубокий обсидиан
SURFACE_CARD = "#090f1d"  # Цвет карточек
SURFACE_INNER = "#040812" # Внутренние поля ввода
ACCENT_GREEN = "#10b981"  # Изумрудный акцент
ACCENT_HOVER = "#059669"
TEXT_MUTED = "#64748b"
BORDER_COLOR = "#1e293b"


class DashboardApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("GhostAssist // Terminal")
        self.geometry("960x640")
        self.minsize(880, 560)
        self.configure(fg_color=BG_DARK)

        ctk.set_appearance_mode("dark")

        self.assistant_process = None
        self.profiles_dir = os.path.join("config", "profiles")
        os.makedirs(self.profiles_dir, exist_ok=True)

        self._setup_layout()
        self._load_profiles_list()

    def _setup_layout(self):
        self.grid_columnconfigure(0, weight=3, minsize=320)
        self.grid_columnconfigure(1, weight=5)
        self.grid_rowconfigure(0, weight=1)

        # ==========================================
        # ЛЕВАЯ КОЛОНКА (Служба, Конфигурация, Хоткеи)
        # ==========================================
        left_container = ctk.CTkFrame(self, fg_color="transparent")
        left_container.grid(row=0, column=0, padx=(20, 10), pady=20, sticky="nsew")
        left_container.grid_rowconfigure(2, weight=1)
        left_container.grid_columnconfigure(0, weight=1)

        # Карточка 1: Статус службы и запуск
        service_card = ctk.CTkFrame(
            left_container, 
            fg_color=SURFACE_CARD, 
            corner_radius=12,
            border_width=1,
            border_color=BORDER_COLOR
        )
        service_card.grid(row=0, column=0, sticky="ew", pady=(0, 14))

        brand_lbl = ctk.CTkLabel(
            service_card, 
            text="GHOST // ASSIST", 
            font=ctk.CTkFont(family="Consolas", size=18, weight="bold"),
            text_color=ACCENT_GREEN
        )
        brand_lbl.pack(pady=(16, 6))

        # Pill Status Badge
        self.status_pill = ctk.CTkFrame(
            service_card, 
            fg_color="#1a0d14", 
            corner_radius=20,
            border_width=1,
            border_color="#451a24"
        )
        self.status_pill.pack(pady=(0, 14))

        self.status_text = ctk.CTkLabel(
            self.status_pill,
            text="● SERVICE OFFLINE",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#f43f5e"
        )
        self.status_text.pack(padx=14, pady=4)

        self.run_btn = ctk.CTkButton(
            service_card,
            text="АКТИВИРОВАТЬ ПЕРЕХВАТ",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=ACCENT_GREEN,
            hover_color=ACCENT_HOVER,
            text_color="#021c14",
            height=38,
            corner_radius=8,
            command=self.toggle_assistant_process
        )
        self.run_btn.pack(fill="x", padx=16, pady=(0, 16))

        # Карточка 2: Параметры инференса
        config_card = ctk.CTkFrame(
            left_container, 
            fg_color=SURFACE_CARD, 
            corner_radius=12,
            border_width=1,
            border_color=BORDER_COLOR
        )
        config_card.grid(row=1, column=0, sticky="ew", pady=(0, 14))

        ctk.CTkLabel(
            config_card, 
            text="ПАРАМЕТРЫ ЗАПРОСОВ", 
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=TEXT_MUTED
        ).pack(anchor="w", padx=16, pady=(14, 8))

        ctk.CTkLabel(config_card, text="Модель Gemini", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=16)
        self.model_combo = ctk.CTkComboBox(
            config_card,
            values=["gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-3.5-flash"],
            fg_color=SURFACE_INNER,
            border_color=BORDER_COLOR,
            button_color=BORDER_COLOR,
            command=self._on_model_changed
        )
        self.model_combo.set(settings.gemini_model)
        self.model_combo.pack(fill="x", padx=16, pady=(4, 12))

        ctk.CTkLabel(config_card, text="Разрешение снимка (RAM)", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=16)
        self.res_combo = ctk.CTkComboBox(
            config_card,
            values=["1280x720 (Быстро, ~45 КБ)", "1920x1080 (Четко, ~90 КБ)"],
            fg_color=SURFACE_INNER,
            border_color=BORDER_COLOR,
            button_color=BORDER_COLOR,
            command=self._on_resolution_changed
        )
        current_res = f"{settings.crop_width}x{settings.crop_height}"
        self.res_combo.set("1280x720 (Быстро, ~45 КБ)" if "1280" in current_res else "1920x1080 (Четко, ~90 КБ)")
        self.res_combo.pack(fill="x", padx=16, pady=(4, 16))

        # Карточка 3: Шпаргалка по горячим клавишам
        hotkeys_card = ctk.CTkFrame(
            left_container, 
            fg_color=SURFACE_CARD, 
            corner_radius=12,
            border_width=1,
            border_color=BORDER_COLOR
        )
        hotkeys_card.grid(row=2, column=0, sticky="nsew")

        ctk.CTkLabel(
            hotkeys_card, 
            text="БЫСТРЫЕ КЛАВИШИ", 
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=TEXT_MUTED
        ).pack(anchor="w", padx=16, pady=(12, 6))

        shortcuts = [
            ("Mouse4 / Alt+Q", "Скриншот + Решение"),
            ("Mouse5 / Alt+R", "Голос (Loopback)"),
            ("Alt + M", "Смена TEST ↔ INTERVIEW"),
            ("Alt + X", "Скрыть / Показать HUD")
        ]
        for key, desc in shortcuts:
            row = ctk.CTkFrame(hotkeys_card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=3)
            ctk.CTkLabel(
                row, 
                text=key, 
                font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                text_color=ACCENT_GREEN
            ).pack(side="left")
            ctk.CTkLabel(
                row, 
                text=desc, 
                font=ctk.CTkFont(size=11),
                text_color="#94a3b8"
            ).pack(side="right")

        # ==========================================
        # ПРАВАЯ КОЛОНКА (Редактор легенды кандидата)
        # ==========================================
        right_card = ctk.CTkFrame(
            self, 
            fg_color=SURFACE_CARD, 
            corner_radius=12,
            border_width=1,
            border_color=BORDER_COLOR
        )
        right_card.grid(row=0, column=1, padx=(10, 20), pady=20, sticky="nsew")
        right_card.grid_rowconfigure(1, weight=1)
        right_card.grid_columnconfigure(0, weight=1)

        # Шапка редактора
        editor_header = ctk.CTkFrame(right_card, fg_color="transparent")
        editor_header.grid(row=0, column=0, padx=18, pady=(16, 12), sticky="ew")

        ctk.CTkLabel(
            editor_header, 
            text="ЛЕГЕНДА КАНДИДАТА", 
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color="#f8fafc"
        ).pack(side="left")

        self.save_btn = ctk.CTkButton(
            editor_header,
            text="Сохранить",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=95,
            height=30,
            fg_color=ACCENT_GREEN,
            hover_color=ACCENT_HOVER,
            text_color="#021c14",
            corner_radius=6,
            command=self.save_current_legend
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
            command=self.create_new_profile
        )
        self.new_profile_btn.pack(side="right", padx=(8, 0))

        self.profile_selector = ctk.CTkOptionMenu(
            editor_header,
            values=["example_legend.md"],
            fg_color=SURFACE_INNER,
            button_color=BORDER_COLOR,
            dropdown_fg_color=SURFACE_CARD,
            width=160,
            height=30,
            command=self.load_selected_profile
        )
        self.profile_selector.pack(side="right")

        # Текстовое поле в стиле редактора кода
        self.legend_textbox = ctk.CTkTextbox(
            right_card,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=SURFACE_INNER,
            border_width=1,
            border_color=BORDER_COLOR,
            corner_radius=8,
            text_color="#e2e8f0",
            wrap="word"
        )
        self.legend_textbox.grid(row=1, column=0, padx=18, pady=(0, 18), sticky="nsew")

    def _load_profiles_list(self):
        files = [f for f in os.listdir(self.profiles_dir) if f.endswith(".md")]
        if not files:
            files = ["example_legend.md"]
        self.profile_selector.configure(values=files)
        target = settings.active_profile if settings.active_profile in files else files[0]
        self.profile_selector.set(target)
        self.load_selected_profile(target)

    def load_selected_profile(self, profile_name):
        path = os.path.join(self.profiles_dir, profile_name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.legend_textbox.delete("1.0", "end")
            self.legend_textbox.insert("1.0", content)
            self._update_env_var("ACTIVE_PROFILE", profile_name)

    def save_current_legend(self):
        profile_name = self.profile_selector.get()
        path = os.path.join(self.profiles_dir, profile_name)
        content = self.legend_textbox.get("1.0", "end-1c")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        messagebox.showinfo("Сохранено", f"Профиль {profile_name} обновлен!")

    def create_new_profile(self):
        dialog = ctk.CTkInputDialog(text="Название файла профиля (например: sber_sa.md):", title="Новая легенда")
        name = dialog.get_input()
        if name:
            if not name.endswith(".md"):
                name += ".md"
            path = os.path.join(self.profiles_dir, name)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"# Профиль: {name}\n- Системный анализ, REST API, брокеры сообщений\n- SQL, PostgreSQL, проектирование структур данных\n")
            self._load_profiles_list()
            self.profile_selector.set(name)
            self.load_selected_profile(name)

    def _on_model_changed(self, new_model):
        self._update_env_var("GEMINI_MODEL", new_model)

    def _on_resolution_changed(self, val):
        if "1920" in val:
            self._update_env_var("CROP_WIDTH", "1920")
            self._update_env_var("CROP_HEIGHT", "1080")
        else:
            self._update_env_var("CROP_WIDTH", "1280")
            self._update_env_var("CROP_HEIGHT", "720")

    def _update_env_var(self, key, value):
        env_path = ".env"
        lines = []
        found = False
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        new_lines = []
        for line in lines:
            if line.startswith(f"{key}="):
                new_lines.append(f"{key}={value}\n")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    def toggle_assistant_process(self):
        if self.assistant_process is None or self.assistant_process.poll() is not None:
            self.assistant_process = subprocess.Popen([sys.executable, "main.py"])
            self.status_pill.configure(fg_color="#032617", border_color="#065f46")
            self.status_text.configure(text="● SERVICE ACTIVE (HOTKEYS ON)", text_color=ACCENT_GREEN)
            self.run_btn.configure(text="ОСТАНОВИТЬ СЛУЖБУ", fg_color="#e11d48", hover_color="#be123c", text_color="#ffffff")
        else:
            self.assistant_process.terminate()
            self.assistant_process = None
            self.status_pill.configure(fg_color="#1a0d14", border_color="#451a24")
            self.status_text.configure(text="● SERVICE OFFLINE", text_color="#f43f5e")
            self.run_btn.configure(text="АКТИВИРОВАТЬ ПЕРЕХВАТ", fg_color=ACCENT_GREEN, hover_color=ACCENT_HOVER, text_color="#021c14")

    def on_closing(self):
        if self.assistant_process and self.assistant_process.poll() is None:
            self.assistant_process.terminate()
        self.destroy()


if __name__ == "__main__":
    app = DashboardApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()