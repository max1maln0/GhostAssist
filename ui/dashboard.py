"""Dashboard 2.0 composition: widgets on Tk thread, bounded background jobs."""

from queue import Queue
from threading import Event

import customtkinter as ctk

from config.settings import (
    ENV_FILE,
    HOTKEY_LABELS,
    PROFILES,
    Settings,
    format_hotkey,
    load_settings,
    service_signature,
)
from platform_adapters.windows.audio import WindowsAudioCapture
from platform_adapters.windows.hotkeys import WindowsHotkeyBackend
from platform_adapters.windows.single_instance import SingleInstance
from ui.dashboard_actions import SettingsActions
from ui.dashboard_components import button, label
from ui.dashboard_hotkeys import HotkeyActions
from ui.dashboard_jobs import JobRunner
from ui.dashboard_pages import (
    audio,
    hotkeys,
    overview,
    profiles,
    providers,
    screen,
    system,
)
from ui.dashboard_profiles import ProfileActions
from ui.dashboard_service import ServiceActions
from ui.dashboard_settings import SavedChange, SettingsDraft, cleanup_change
from ui.dashboard_theme import (
    ACCENT_GREEN,
    BG_DARK,
    ERROR,
    SURFACE_CARD,
    TEXT_MUTED,
)
from ui.tray import Tray

PAGES = {
    "overview": ("Обзор", overview),
    "providers": ("Провайдеры", providers),
    "audio": ("Аудио и VAD", audio),
    "screen": ("Экран и HUD", screen),
    "hotkeys": ("Горячие клавиши", hotkeys),
    "profiles": ("Профили и память", profiles),
    "system": ("Система", system),
}


class DashboardApp(
    SettingsActions, HotkeyActions, ProfileActions, ServiceActions, ctk.CTk
):
    def __init__(
        self,
        instance=None,
        *,
        settings_path=ENV_FILE,
        profiles_directory=PROFILES,
        start_integrations=True,
    ):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=BG_DARK)
        self.instance, self.settings_path = instance, settings_path
        self.profiles_directory, self.integrations = (
            profiles_directory,
            start_integrations,
        )
        self.last_error = ""
        try:
            self.settings = load_settings(settings_path)
        except (ValueError, OSError):
            self.settings = Settings.model_construct()
            self.last_error = "Конфигурация недоступна или некорректна. Исправьте .env перед запуском службы."
        self.draft = SettingsDraft(self.settings)
        self.variables, self.form_widgets, self.hotkey_buttons = {}, [], {}
        self.provider_controls, self.key_controls, self.tooltips = {}, {}, []
        self.syncing_form = True
        self.profile_dirty, self.profile_original = False, ""
        self.loaded_profile = self.settings.active_profile
        self.probe_revision, self.probe_states = 0, {}
        self.save_jobs, self.probe_jobs, self.device_jobs = (
            JobRunner(),
            JobRunner(),
            JobRunner(),
        )
        self.audio_capture, self.hotkeys = WindowsAudioCapture(), WindowsHotkeyBackend()
        self.tray_commands, self.service_updates = Queue(64), Queue(32)
        self.exit_requested = Event()
        self.tray, self.dashboard_hotkey = Tray(self._enqueue_tray), None
        self.dashboard_visible, self.capturing_hotkey = True, False
        self.active_hotkey = self.hotkey_capture = None
        self.hotkey_bindings, self.hotkey_warning = {}, False
        self.assistant_process = self.running_settings = self.starting_settings = (
            self.applied
        ) = None
        self.stopping_until = self.start_deadline = None
        self.service_state, self.external_service = "stopped", False
        self.restart_pending = self.closing = self.saving = False
        self.live_state, self.session_counts = None, (0, 0)
        self.current_page = "overview"
        self.title("GhostAssist — Dashboard 2.0")
        scale = self._get_window_scaling()
        width = min(1100, int(self.winfo_screenwidth() / scale) - 40)
        height = min(750, int(self.winfo_screenheight() / scale) - 80)
        self.geometry(f"{width}x{height}")
        self.minsize(min(760, width), min(520, height))
        self._setup_layout()
        self.syncing_form = False
        self.sync_form()
        self._load_profiles_list()
        self.load_selected_profile(self.settings.active_profile, save=False)
        self.bind("<Configure>", self._resize_footer, add="+")
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        if self.integrations:
            self.tray.start()
            self._bind_dashboard_hotkey()
            self._refresh_devices()
        self.after(100, self._poll_process)

    def _setup_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        sidebar = ctk.CTkFrame(self, width=190, corner_radius=0, fg_color=SURFACE_CARD)
        sidebar.grid(row=0, column=0, rowspan=3, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        label(sidebar, "◈ GhostAssist", size=21, bold=True).grid(
            row=0, column=0, padx=18, pady=(26, 4), sticky="ew"
        )
        label(sidebar, "РАБОЧЕЕ ПРОСТРАНСТВО", muted=True, size=10).grid(
            row=1, column=0, padx=18, pady=(0, 26), sticky="ew"
        )
        self.nav_buttons = {}
        for index, (key, (title, _)) in enumerate(PAGES.items(), 2):
            control = button(
                sidebar, title, lambda key=key: self.navigate(key), width=164
            )
            control.grid(row=index, column=0, padx=12, pady=5, sticky="ew")
            self.nav_buttons[key] = control
        sidebar.grid_rowconfigure(10, weight=1)
        label(sidebar, "LOCAL • PRIVATE\nStage 4.2.1", muted=True, size=11).grid(
            row=11, column=0, padx=18, pady=22, sticky="sw"
        )
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=1, sticky="ew", padx=24, pady=(20, 12))
        header.grid_columnconfigure(0, weight=1)
        self.page_title = label(header, "Обзор", size=25, bold=True)
        self.page_title.grid(row=0, column=0, sticky="ew")
        self.run_btn = button(
            header,
            "Запустить службу",
            self.toggle_assistant_process,
            primary=True,
            width=190,
        )
        self.run_btn.grid(row=0, column=1, padx=(10, 0))
        self.status_text = label(header, "● Служба остановлена", muted=True, size=12)
        self.status_text.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew", padx=(18, 16))
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.pages = {}
        for key, (_, module) in PAGES.items():
            frame = ctk.CTkScrollableFrame(
                self.content, fg_color=BG_DARK, corner_radius=0
            )
            module.build(self, frame)
            self.pages[key] = frame
        footer = ctk.CTkFrame(self, fg_color=SURFACE_CARD, corner_radius=0)
        footer.grid(row=2, column=1, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self.change_status = label(footer, "Настройки сохранены", muted=True, size=12)
        self.change_status.grid(
            row=0, column=0, columnspan=3, sticky="ew", padx=20, pady=(12, 8)
        )
        self.save_button = button(
            footer, "Сохранить", self.request_save, primary=True, width=130
        )
        self.cancel_button = button(
            footer, "Отменить правки", self.discard_changes, width=165
        )
        self.apply_button = button(
            footer, "Применить и перезапустить", self.apply_and_restart, width=245
        )
        for column, control in enumerate(
            (self.save_button, self.cancel_button, self.apply_button)
        ):
            control.grid(
                row=1,
                column=column,
                padx=(20 if column == 0 else 6, 20 if column == 2 else 0),
                pady=(0, 14),
                sticky="w" if column == 0 else "e",
            )
        self.variable("ACTIVE_PROFILE")
        self.navigate("overview")

    def destroy(self):
        # Tcl timers can otherwise retain deleted widget callbacks across roots.
        for callback in self.tk.call("after", "info"):
            self.tk.call("after", "cancel", callback)
        super().destroy()

    def _resize_footer(self, event):
        if event.widget is not self:
            return
        narrow = self.winfo_width() / self._get_window_scaling() < 1020
        self.apply_button.grid_configure(
            row=2 if narrow else 1,
            column=0 if narrow else 2,
            columnspan=3 if narrow else 1,
            sticky="e",
        )

    def navigate(self, page):
        if page not in PAGES:
            return
        if (
            self.current_page == "profiles"
            and page != "profiles"
            and not self.guard_profile()
        ):
            return
        self._end_hotkey_capture()
        for frame in self.pages.values():
            frame.grid_remove()
        self.pages[page].grid(row=0, column=0, sticky="nsew")
        self.current_page = page
        self.page_title.configure(text=PAGES[page][0])
        for key, control in self.nav_buttons.items():
            control.configure(
                fg_color="#064e3b" if key == page else "transparent",
                text_color=ACCENT_GREEN if key == page else TEXT_MUTED,
            )

    def set_error(self, text):
        self.last_error = text or "Не удалось выполнить операцию."
        self.refresh_summary()

    def clear_error(self):
        self.last_error = ""
        self.refresh_summary()

    def set_saving(self, value):
        self.saving = value
        state = "disabled" if value else "normal"
        for widget in [
            *self.form_widgets,
            *self.hotkey_buttons.values(),
            self.save_button,
            self.cancel_button,
            self.apply_button,
            self.profile_selector,
            self.legend_textbox,
            *(controls[0] for controls in self.key_controls.values()),
        ]:
            widget.configure(state=state)
        self.refresh_summary()

    def refresh_summary(self):
        if not hasattr(self, "overview_summary"):
            return
        snapshot = (
            self.external_service,
            self.service_state,
            self.saving,
            id(self.settings),
            tuple(self.draft.changes.items()),
            self.last_error,
            self.session_counts,
            tuple(self.live_state or ()),
            bool(self.dashboard_hotkey),
            self.applied and self.applied["signature"],
        )
        if snapshot == getattr(self, "_summary_snapshot", None):
            return
        self._summary_snapshot = snapshot
        self.refresh_provider_info()
        state = "external" if self.external_service else self.service_state
        text = {
            "stopped": "Служба остановлена",
            "starting": "Запуск службы…",
            "running": "Служба работает",
            "stopping": "Остановка службы…",
            "error": "Ошибка службы",
            "external": "Служба запущена отдельно",
        }[state]
        self.status_text.configure(
            text="● " + text,
            text_color=ACCENT_GREEN if state == "running" else TEXT_MUTED,
        )
        self.overview_status.configure(text=text)
        for control in (self.run_btn, self.overview_run):
            control.configure(
                text="Остановить службу"
                if self._service_running()
                else "Запустить службу",
                state="disabled"
                if state in ("starting", "stopping", "external") or self.saving
                else "normal",
            )
        bindings = self.applied or {
            purpose: self.settings.resolve_binding(purpose)
            for purpose in ("generation", "transcription")
        }
        if self.external_service:
            summary = "Конфигурация внешней службы неизвестна Dashboard.\nСохранённые параметры доступны на странице «Провайдеры»."
        else:
            summary = f"Генерация: {' / '.join(bindings['generation'])}\nТранскрибация: {' / '.join(bindings['transcription'])}"
            live = self.live_state
            summary += f"\nРежим: {live[0] if live else '—'} · запись: {'активна' if live and live[3] else 'не активна'}"
            summary += "\nVAD: " + (
                "включён в конфигурации; мониторинг запускается пользователем"
                if (self.applied["vad"] if self.applied else self.settings.vad_enabled)
                else "выключен"
            )
        self.overview_summary.configure(text=summary)
        session = f"Сессия  TEST {self.session_counts[0]} · INTERVIEW {self.session_counts[1]}"
        self.session_status.configure(text=session)
        self.memory_status.configure(text=session + " · история только в RAM")
        self.hud_status.configure(
            text=(
                f"HUD: {'виден' if self.live_state[1] else 'скрыт'} · {'сверху' if self.live_state[2] else 'снизу'}"
                if self.live_state
                else "Статус HUD недоступен до запуска управляемой службы"
            )
        )
        for control in self.hud_buttons:
            control.configure(state="normal" if state == "running" else "disabled")
        self.screen_hotkeys.configure(
            text=" · ".join(
                f"{HOTKEY_LABELS[key.lower()]}: {format_hotkey(getattr(self.settings, key.lower()))}"
                for key in ("HOTKEY_SCREEN", "HOTKEY_SNIP", "HOTKEY_HUD")
            )
        )
        diagnostic = self.last_error or "Ошибок не зарегистрировано"
        for widget in (self.system_error, self.overview_error):
            widget.configure(
                text=diagnostic, text_color=ERROR if self.last_error else TEXT_MUTED
            )
        dirty = len(self.draft.changes)
        pending = bool(
            self.applied
            and self.applied["signature"] != service_signature(self.settings)
        )
        self.change_status.configure(
            text="Сохранение…"
            if self.saving
            else f"Несохранённых полей: {dirty}"
            if dirty
            else "Сохранено · для службы требуется «Применить и перезапустить»"
            if pending
            else "Настройки сохранены"
        )
        self.configuration_status.configure(
            text=f"Схема {self.settings.schema_version} · переменные окружения имеют приоритет над .env"
        )
        self.hotkey_runtime_status.configure(
            text="Dashboard: hook активен"
            if self.dashboard_hotkey
            else "Dashboard: hook не зарегистрирован"
        )

    def _bind_dashboard_hotkey(self):
        snapshot = self.settings.model_copy(deep=True)

        def finished(listener, error):
            if error:
                self.set_error(
                    "Не удалось зарегистрировать глобальную клавишу Dashboard."
                )
            else:
                self._replace_dashboard_hotkey(listener)

        self.save_jobs.submit(
            lambda: self._create_dashboard_hotkey(snapshot),
            finished,
            lambda listener: cleanup_change(SavedChange(snapshot, listener)),
        )


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
