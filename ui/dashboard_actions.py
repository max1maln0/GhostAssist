"""Tk-side form actions; all settings writes use persist_changes."""

from dataclasses import dataclass, field
from tkinter import StringVar, messagebox

from pydantic import ValidationError

from config.migration import ConfigurationError
from config.settings import (
    HotkeyValidationError,
    format_hotkey,
    load_settings,
    validate_hotkeys,
)
from config.storage import configuration_lock
from providers.access_check import check_access
from ui.dashboard_settings import (
    CATALOGS,
    SettingsEditError,
    cleanup_change,
    compatible_models,
    persist_changes,
)


@dataclass
class SaveOutcome:
    change: object = field(default=None, repr=False)
    error: str | None = None


def safe_save(current, values, path, prepare):
    try:
        return SaveOutcome(persist_changes(current, values, path=path, prepare=prepare))
    except (SettingsEditError, ConfigurationError, HotkeyValidationError) as exc:
        return SaveOutcome(error=str(exc))
    except ValidationError as exc:
        fields = ", ".join(
            str(error["loc"][0]) for error in exc.errors(include_input=False)
        )
        return SaveOutcome(error="Проверьте значения полей: " + fields)
    except (OSError, RuntimeError, ValueError):
        return SaveOutcome(
            error="Не удалось сохранить настройки или установить Windows hook."
        )


class SettingsActions:
    def variable(self, key):
        if key not in self.variables:
            value = self.draft.display(key)
            variable = StringVar(self, value=value)
            self.draft.baseline[key] = value
            variable.trace_add("write", lambda *args, key=key: self.form_changed(key))
            self.variables[key] = variable
        return self.variables[key]

    def form_changed(self, key):
        if not self.syncing_form:
            self.draft.stage(key, self.variables[key].get())
            self.refresh_summary()
            if key.startswith(("GENERATION_", "TRANSCRIPTION_")):
                self.refresh_provider_info()

    def sync_form(self, remaining=None):
        self.syncing_form = True
        try:
            for key, variable in self.variables.items():
                value = self.draft.display(key)
                self.draft.baseline[key] = value
                variable.set(value)
            for key, button in self.hotkey_buttons.items():
                button.configure(
                    text=format_hotkey(getattr(self.settings, key.lower()))
                )
        finally:
            self.syncing_form = False
        for key, value in (remaining or {}).items():
            if key in self.variables:
                self.variables[key].set(value)
        if "audio" in self.pages:
            self.device_combo.set(self.variable("AUDIO_DEVICE").get() or "По умолчанию")
        self.refresh_provider_info()
        self.refresh_key_states()
        self.refresh_summary()

    def discard_changes(self):
        if not self.guard_profile():
            return
        try:
            with configuration_lock(self.settings_path, timeout=0.05):
                settings = load_settings(self.settings_path)
        except (ValueError, OSError):
            self.set_error("Не удалось обновить конфигурацию. Проверьте .env.")
            return
        self.settings = settings
        self.draft.reset(settings)
        self.sync_form()
        if "profiles" in self.pages:
            self.load_selected_profile(settings.active_profile, save=False)

    def provider_changed(self, purpose, provider):
        models = compatible_models(provider, purpose)
        self.variable(purpose.upper() + "_MODEL").set(models[0])
        self.refresh_provider_info()

    def refresh_provider_info(self):
        for purpose, (menu, info) in self.provider_controls.items():
            provider = self.variable(purpose.upper() + "_PROVIDER").get()
            model = self.variable(purpose.upper() + "_MODEL").get()
            models = compatible_models(provider, purpose)
            menu.configure(values=list(models))
            if model not in models:
                info.configure(
                    text="Неизвестная или несовместимая модель. Выберите модель из списка.",
                    text_color="#fb7185",
                )
                continue
            caps = CATALOGS[provider][model]
            active = self.applied[purpose] if self.applied else None
            active_text = (
                "\nАктивно в службе: " + " / ".join(active)
                if active
                else "\nСлужба не подтвердила активную модель"
            )
            formats = ", ".join(sorted(caps.mime_types or ()))
            text = (
                "Текст и изображения · потоковый ответ"
                if purpose == "generation"
                else "Транскрибация готового WAV"
            )
            info.configure(
                text=f"{text}{active_text}\nФорматы: {formats}\nЛимит входа: {caps.max_input_bytes / 1_000_000:g} МБ · ограничения API и доступа зависят от аккаунта",
                text_color="#94a3b8",
            )

    def prepare_hotkeys(self, candidate, changes):
        if "hotkey_dashboard" in changes:
            if self.external_service:
                raise SettingsEditError(
                    "Сначала завершите внешнюю службу: её активные сочетания неизвестны Dashboard."
                )
            if self._service_running() and self.running_settings is None:
                raise SettingsEditError(
                    "Дождитесь готовности службы или остановите её перед сменой клавиши Dashboard."
                )
            if self.running_settings:
                try:
                    validate_hotkeys(
                        self.running_settings.model_copy(
                            update={"hotkey_dashboard": candidate.hotkey_dashboard}
                        )
                    )
                except HotkeyValidationError:
                    raise SettingsEditError(
                        "Сочетание занято работающей службой. Сначала примените сохранённые настройки службы."
                    ) from None
            return self._create_dashboard_hotkey(candidate)
        # Validate installation without replacing the still-running service hook.
        listener = self.hotkeys.create_keyboard(
            {value: lambda: None for value in changes.values()}
        )
        try:
            listener.start_verified()
        finally:
            listener.stop()
            listener.join(timeout=0.5)
        return None

    def request_save(self, values=None, done=None):
        if self.save_jobs.busy:
            self.set_error("Сохранение уже выполняется. Дождитесь завершения.")
            return False
        if values is None and self.profile_dirty and not self.save_current_legend():
            return False
        values = dict(self.draft.changes if values is None else values)
        if not values:
            if done:
                done(True)
            return True
        current = self.settings.model_copy(deep=True)

        def complete(outcome, error):
            self.set_saving(False)
            success = bool(
                outcome and outcome.change and not outcome.error and not error
            )
            if success:
                self.adopt_change(outcome.change, values)
            else:
                self.set_error(
                    error
                    or (outcome.error if outcome else "Не удалось сохранить настройки.")
                )
            if done:
                done(success)

        accepted = self.save_jobs.submit(
            lambda: safe_save(
                current, values, self.settings_path, self.prepare_hotkeys
            ),
            complete,
            lambda outcome: cleanup_change(outcome.change) if outcome else None,
        )
        if accepted:
            self.set_saving(True)
        return accepted

    def adopt_change(self, change, values):
        remaining = {
            key: value for key, value in self.draft.changes.items() if key not in values
        }
        self.settings = change.settings
        self.draft.reset(self.settings)
        if change.listener:
            self._replace_dashboard_hotkey(change.listener)
        self.probe_revision += 1
        self.probe_states.clear()
        self.sync_form(remaining)

    def save_key(self, provider):
        entry = self.key_controls[provider][0]
        key = entry.get().strip()
        if not key or not key.isascii() or any(char.isspace() for char in key):
            self.set_error("Введите API-ключ без пробелов и переносов строк.")
            return
        self.request_save(
            {provider.upper() + "_API_KEY": key},
            lambda ok: entry.delete(0, "end") if ok else None,
        )

    def delete_key(self, provider):
        if not messagebox.askyesno(
            "Удалить ключ",
            "Удалить сохранённый ключ? Если он используется управляемой службой, она будет остановлена для удаления ключа из памяти.",
            parent=self,
        ):
            return

        def deleted(success):
            if success:
                self.key_controls[provider][0].delete(0, "end")
                if self._service_running() and provider in self.applied_providers():
                    self.restart_pending = False
                    self._stop_process()

        self.request_save({provider.upper() + "_API_KEY": ""}, deleted)

    def refresh_key_states(self):
        for provider, (_, label, _) in self.key_controls.items():
            present = bool(
                getattr(self.settings, provider + "_api_key").get_secret_value().strip()
            )
            label.configure(
                text=self.probe_states.get(
                    provider,
                    "Ключ сохранён · доступ ещё не проверен"
                    if present
                    else "Ключ отсутствует",
                )
            )

    def probe_access(self, provider):
        if self.probe_jobs.busy:
            self.set_error("Проверка доступа уже выполняется.")
            return
        snapshot = self.settings.model_copy(deep=True)
        models = [
            model
            for purpose in ("generation", "transcription")
            for identifier, model in (snapshot.resolve_binding(purpose),)
            if identifier == provider
        ]
        if not models:
            models = [next(iter(CATALOGS[provider]))]
        if any(model not in CATALOGS[provider] for model in models):
            self.set_error("Сначала выберите и сохраните известную модель.")
            return
        revision = self.probe_revision
        self.probe_states[provider] = "Проверяем доступ…"
        self.refresh_key_states()

        def finished(result, error):
            for _, _, control in self.key_controls.values():
                control.configure(state="normal")
            if revision == self.probe_revision:
                self.probe_states[provider] = error or result.message
                self.refresh_key_states()

        if self.probe_jobs.submit(
            lambda: check_access(snapshot, provider, models), finished
        ):
            for _, _, control in self.key_controls.values():
                control.configure(state="disabled")

    def device_selected(self, value):
        self.variable("AUDIO_DEVICE").set("" if value == "По умолчанию" else value)

    def _refresh_devices(self):
        if self.device_jobs.busy:
            return
        self.refresh_devices_button.configure(state="disabled")

        def scan():
            return self.audio_capture.list_devices()

        def finished(devices, error):
            self.refresh_devices_button.configure(state="normal")
            devices = devices or []
            selected = self.variable("AUDIO_DEVICE").get()
            self.device_combo.configure(values=["По умолчанию", *devices])
            self.device_combo.set(selected or "По умолчанию")
            message = error or (
                "Выбранное устройство недоступно. Выберите другое или устройство по умолчанию."
                if selected and selected not in devices
                else f"Доступно loopback-устройств: {len(devices)}"
            )
            self.device_status.configure(text=message)

        self.device_jobs.submit(scan, finished)
