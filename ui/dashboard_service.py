"""Owned service lifecycle and bounded, validated status IPC. No worker calls Tk."""

import json
import subprocess
import sys
import time
from queue import Empty, Full
from threading import Thread
from tkinter import messagebox

from config.settings import ROOT, load_settings, service_signature, validate_hotkeys
from platform_adapters.windows.single_instance import SingleInstance
from ui.dashboard_settings import validate_bindings


def close_input(process):
    try:
        process.stdin.close()
    except (OSError, ValueError):
        pass


def parse_status(raw):
    """Accept only the fixed status protocol; never display arbitrary stdout."""
    try:
        line = raw.decode("utf-8").strip()
        if line.startswith("GHOST_SESSION "):
            parts = line.split()
            if len(parts) == 3 and all(p.isdecimal() and len(p) < 8 for p in parts[1:]):
                return "session", tuple(map(int, parts[1:]))
        if line == "GHOST_ERROR task":
            return (
                "error",
                "Ошибка выполнения задачи. Проверьте настройки и доступность устройства или провайдера.",
            )
        if line.startswith("GHOST_STATE "):
            value = json.loads(line[12:])
            if (
                isinstance(value, list)
                and len(value) == 4
                and value[0] in ("TEST", "INTERVIEW")
                and all(type(v) is bool for v in value[1:])
            ):
                return "state", value
        if line.startswith("GHOST_READY "):
            value = json.loads(line[12:])
            if not isinstance(value, dict) or set(value) != {
                "signature",
                "generation",
                "transcription",
                "vad",
            }:
                return None
            if (
                not isinstance(value["signature"], str)
                or len(value["signature"]) != 64
                or any(c not in "0123456789abcdef" for c in value["signature"])
            ):
                return None
            for purpose in ("generation", "transcription"):
                binding = value[purpose]
                if (
                    not isinstance(binding, list)
                    or len(binding) != 2
                    or binding[0] not in ("gemini", "openai")
                    or not isinstance(binding[1], str)
                    or not 0 < len(binding[1]) < 128
                    or not all(c.isalnum() or c in "._-/" for c in binding[1])
                ):
                    return None
            if type(value["vad"]) is bool:
                return "ready", value
    except (UnicodeError, ValueError, TypeError):
        pass
    return None


def read_service_status(process, updates):
    try:
        while True:
            raw = process.stdout.readline(8193)
            if not raw:
                break
            if len(raw) > 8192:
                while raw and not raw.endswith(b"\n"):
                    raw = process.stdout.readline(8193)
                continue
            status = parse_status(raw)
            if status:
                # Replace oldest telemetry on overflow so READY/current state survive.
                try:
                    updates.put_nowait((process, status))
                except Full:
                    try:
                        updates.get_nowait()
                    except Empty:
                        pass
                    try:
                        updates.put_nowait((process, status))
                    except Full:
                        pass
    finally:
        process.stdout.close()


class ServiceActions:
    def applied_providers(self):
        return (
            {self.applied[purpose][0] for purpose in ("generation", "transcription")}
            if self.applied
            else set()
        )

    def _service_running(self):
        return bool(self.assistant_process and self.assistant_process.poll() is None)

    def toggle_assistant_process(self):
        if self.assistant_process and self.assistant_process.poll() is not None:
            close_input(self.assistant_process)
            self.assistant_process = self.running_settings = self.applied = None
            self.session_counts, self.live_state = (0, 0), None
            self.stopping_until = self.start_deadline = None
            self.service_state = "stopped"
        if self.closing or self.service_state in ("starting", "stopping"):
            return
        if self._service_running():
            self.restart_pending = False
            self._stop_process()
            return
        if self.save_jobs.busy:
            self.set_error("Дождитесь завершения сохранения настроек.")
            return
        if self.integrations and SingleInstance.exists("service"):
            self.external_service = True
            self.set_error(
                "Служба запущена отдельно. Завершите её перед запуском из Dashboard."
            )
            return
        if self.settings_path.resolve() != (ROOT / ".env").resolve():
            self.set_error("Изолированный Dashboard: запуск рабочей службы отключён.")
            return
        self.service_state = "starting"
        self.refresh_summary()

        def prepare():
            settings = load_settings(self.settings_path)
            validate_hotkeys(settings)
            validate_bindings(settings)
            return settings

        def prepared(settings, error):
            if self.closing:
                return
            if error or not settings or not settings.has_api_key:
                self.service_state = "error"
                self.set_error(
                    error
                    or "Укажите ключи выбранных AI-провайдеров на странице «Провайдеры»."
                )
                return
            self.starting_settings = settings
            try:
                self.assistant_process = subprocess.Popen(
                    [sys.executable, "-u", str(ROOT / "main.py"), "--managed"],
                    cwd=ROOT,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )
            except OSError:
                self.service_state = "error"
                self.set_error("Не удалось создать процесс службы.")
                return
            self.start_deadline = time.monotonic() + 12
            Thread(
                target=read_service_status,
                args=(self.assistant_process, self.service_updates),
                daemon=True,
            ).start()
            self.refresh_summary()

        if not self.save_jobs.submit(prepare, prepared):
            self.service_state = "stopped"

    def send_service(self, command):
        if (
            command not in (b"C", b"H", b"D", b"M")
            or self.service_state != "running"
            or not self._service_running()
        ):
            self.set_error("Команда доступна после запуска службы из этого Dashboard.")
            return False
        try:
            self.assistant_process.stdin.write(command)
            self.assistant_process.stdin.flush()
            return True
        except (OSError, ValueError):
            self.set_error("Соединение со службой закрыто.")
            return False

    def apply_and_restart(self):
        if self.external_service:
            self.set_error(
                "Dashboard не управляет внешней службой. Перезапустите её самостоятельно."
            )
            return
        if not self.guard_profile():
            return

        def saved(ok):
            if not ok:
                return
            if not self.settings.has_api_key:
                self.set_error("Сначала сохраните ключи выбранных провайдеров.")
                return
            if self._service_running():
                self.restart_pending = True
                self._stop_process()
            else:
                self.toggle_assistant_process()

        self.request_save(done=saved)

    def _stop_process(self):
        if self.assistant_process and self.stopping_until is None:
            self.service_state = "stopping"
            self.stopping_until = time.monotonic() + 4
            try:
                self.assistant_process.stdin.close()
            except OSError:
                pass
            self.refresh_summary()

    def _reset_session(self):
        if not self._service_running():
            self.set_error("Нет управляемой активной сессии.")
            return
        if messagebox.askyesno(
            "Очистка памяти",
            "Перезапустить службу и очистить обе истории в RAM? Будут применены сохранённые настройки; активная задача отменяется.",
            parent=self,
        ):
            self.apply_and_restart()

    def _poll_process(self):
        for runner in (self.save_jobs, self.probe_jobs, self.device_jobs):
            runner.poll()
        if self.closing and not self.assistant_process:
            return
        if self.dashboard_hotkey and not self.dashboard_hotkey.is_alive():
            self.dashboard_hotkey = None
            self.set_error(
                "Системный hook Dashboard остановился. Переназначьте его на странице горячих клавиш."
            )
        if self.exit_requested.is_set() and not self.closing:
            self.exit_requested.clear()
            self.exit_app()
            if self.closing and not self.assistant_process:
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
            if command in ("show", "hide", "toggle"):
                (
                    self.hide_dashboard
                    if command == "hide"
                    or command == "toggle"
                    and self.dashboard_visible
                    else self.show_dashboard
                )()
            elif command == "start" and not self._service_running():
                self.toggle_assistant_process()
            elif command == "stop":
                self.restart_pending = False
                self._stop_process()
            elif command == "cancel":
                self.send_service(b"C")
        for _ in range(32):
            try:
                source, (kind, value) = self.service_updates.get_nowait()
            except Empty:
                break
            if source is not self.assistant_process or self.stopping_until is not None:
                continue
            if kind == "ready":
                self.applied = value
                self.running_settings = (
                    self.starting_settings
                    if value["signature"] == service_signature(self.starting_settings)
                    else None
                )
                self.service_state = "running"
                self.start_deadline = None
            elif kind == "session":
                self.session_counts = value
            elif kind == "state":
                self.live_state = value
            elif kind == "error":
                self.set_error(value)
        process = self.assistant_process
        if process:
            code = process.poll()
            if code is not None:
                close_input(process)
                self.assistant_process = None
                self.running_settings = self.applied = None
                self.session_counts = (0, 0)
                self.live_state = None
                self.stopping_until = self.start_deadline = None
                self.service_state = "stopped" if code == 0 else "error"
                if code:
                    self.set_error(
                        f"Служба завершилась с кодом {code}. Проверьте конфигурацию и устройства."
                    )
                if self.restart_pending and not self.closing:
                    self.restart_pending = False
                    self.toggle_assistant_process()
            elif self.start_deadline and time.monotonic() >= self.start_deadline:
                self.set_error("Служба не подтвердила готовность за 12 секунд.")
                self._stop_process()
            elif self.stopping_until and time.monotonic() >= self.stopping_until:
                try:
                    process.kill()
                except OSError:
                    self.set_error("Не удалось завершить собственный процесс службы.")
        if self.integrations:
            try:
                self.external_service = (
                    not self._service_running() and SingleInstance.exists("service")
                )
            except OSError:
                self.external_service = False
        changed = (
            self.tray.running != self._service_running()
            or self.tray.externally_running != self.external_service
        )
        self.tray.running = self._service_running()
        self.tray.externally_running = self.external_service
        if changed and self.tray.ready.is_set():
            self.tray.icon.update_menu()
        self.refresh_summary()
        if self.closing and not self.assistant_process:
            self._finish_exit()
        else:
            self.after(100, self._poll_process)

    def hide_dashboard(self):
        if not self.tray.ready.is_set():
            self.set_error("Трей ещё не готов. Окно остаётся доступным.")
            return
        self._end_hotkey_capture()
        self.withdraw()
        self.dashboard_visible = False
        self.tray.visible = False
        self.tray.icon.update_menu()

    def show_dashboard(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        self.dashboard_visible = True
        self.tray.visible = True
        if self.tray.ready.is_set():
            self.tray.icon.update_menu()

    def on_closing(self):
        if not self._window_exists:
            self.after(30, self.on_closing)
        elif self.settings.close_to_tray:
            self.hide_dashboard()
        else:
            self.exit_app()

    def exit_app(self):
        if self.closing:
            return
        if not self._window_exists:
            self.after(30, self.exit_app)
            return
        if self.save_jobs.busy:
            self.set_error(
                "Дождитесь завершения операции с конфигурацией перед выходом."
            )
            return
        if not self.guard_profile():
            return
        if self.draft.changes:
            answer = messagebox.askyesnocancel(
                "Настройки", "Сохранить изменения настроек перед выходом?", parent=self
            )
            if answer is None:
                return
            if answer:
                self.request_save(done=lambda ok: self._begin_exit() if ok else None)
                return
        self._begin_exit()

    def _begin_exit(self):
        self._end_hotkey_capture()
        self.closing = self.tray.closing = True
        self.restart_pending = False
        for runner in (self.save_jobs, self.probe_jobs, self.device_jobs):
            runner.close()
        if self.assistant_process:
            self._stop_process()
        else:
            self._finish_exit()

    def _finish_exit(self):
        if self.dashboard_hotkey:
            self.dashboard_hotkey.stop()
            self.dashboard_hotkey.join(timeout=0)
            self.dashboard_hotkey = None
        if self.integrations:
            self.tray.stop()
        self.destroy()
