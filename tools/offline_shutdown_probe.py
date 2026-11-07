"""Offline shutdown QA. Real lifecycle/UI/hooks/tray; fake captures and provider.

Use from a VS Code terminal. No credentials, recordings or paid API calls.
"""

import argparse
import io
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def settings_for(scenario):
    from config.settings import Settings

    return Settings(
        _env_file=None,
        schema_version=2,
        gemini_api_key="offline-placeholder",
        generation_provider="gemini",
        transcription_provider="gemini",
        active_profile="ghostassist_shutdown_qa.md",
        vad_enabled=scenario == "vad",
        close_to_tray=False,
        task_timeout=120,
    )


class MockProvider:
    def __init__(self, scenario):
        self.scenario = scenario

    def generate_response(self, request, publish, cancel):
        from core.provider_contracts import GenerationResult

        publish("Offline streaming response")
        if self.scenario == "hang":
            Event().wait()
        while not cancel.is_set():
            time.sleep(0.02)
        return GenerationResult("Discarded cancelled result")

    def transcribe_audio(self, request, cancel):
        from core.provider_contracts import TranscriptionResult

        return TranscriptionResult("Offline review question")


def dependencies(settings, scenario):
    from core.llm_worker import LLMWorker
    from core.orchestrator import TaskOrchestrator
    from platform_adapters.windows.hotkeys import WindowsHotkeyBackend

    def record(stop, cancel, *args, on_segment=None, **kwargs):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(bytes(3200))
        if scenario == "review":
            return output.getvalue()
        if on_segment:
            on_segment(1, output.getvalue())
        while not stop.is_set() and not cancel.is_set():
            time.sleep(0.02)
        return output.getvalue()

    return SimpleNamespace(
        orchestrator=TaskOrchestrator(),
        llm=LLMWorker(settings, MockProvider(scenario)),
        screen=SimpleNamespace(capture=lambda *args: b"offline-jpeg"),
        audio=SimpleNamespace(record=record, list_devices=lambda: []),
        hotkeys=WindowsHotkeyBackend(),
        close=lambda: None,
    )


def activate(controller, scenario):
    if scenario in ("generation", "hang"):
        controller.capture()
    elif scenario in ("record", "review", "vad"):
        controller.audio()


def run(args, arm):
    settings = settings_for(args.scenario)
    print("GHOST_QA_OWNER", os.getpid(), flush=True)
    report = os.environ.get("GHOST_QA_PID_FILE")
    if report:
        import json

        guardian = arm.__self__.process.pid
        with open(report, "a", encoding="utf-8") as output:
            output.write(
                json.dumps({"owner": os.getpid(), "guardian": guardian}) + "\n"
            )
    if args.mode == "controller":
        from core.controller import ApplicationController

        controller = ApplicationController(
            settings, dependencies(settings, args.scenario)
        )
        activate(controller, args.scenario)
        until = time.monotonic() + 0.3
        while time.monotonic() < until:
            controller.poll()
            time.sleep(0.01)
        if args.scenario == "vad" and not controller.monitoring:
            raise RuntimeError("Offline VAD did not remain active")
        if (
            args.scenario == "review"
            and controller.orchestrator.state.value != "waiting"
        ):
            raise RuntimeError("Offline review did not reach user input")
        controller.cancel()
        controller.poll()
        arm()
        controller.close()
        return 0
    if args.mode == "service":
        import main

        original = main.GhostAssistApp

        def create(settings, **kwargs):
            app = original(settings, dependencies(settings, args.scenario), **kwargs)
            app.hud.root.after(200, lambda: activate(app.controller, args.scenario))
            if args.auto_exit:
                app.hud.root.after(int(args.auto_exit * 1000), app.close)
            return app

        main.load_settings = lambda: settings
        main.GhostAssistApp = create
        return main.main(arm)
    from ui.dashboard import DashboardApp
    from ui.dashboard_service import read_service_status

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        env = path / ".env"
        env.write_text(
            "SCHEMA_VERSION=2\nCLOSE_TO_TRAY="
            + ("true" if args.tray_close else "false")
            + "\nACTIVE_PROFILE=ghostassist_shutdown_qa.md\n",
            encoding="utf-8",
        )
        app = DashboardApp(
            settings_path=env, profiles_directory=path, shutdown_notify=arm
        )
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "service",
                "--managed",
                "--scenario",
                args.scenario,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        app.assistant_process = child
        app.starting_settings = settings
        app.service_state = "starting"
        app.start_deadline = time.monotonic() + 12
        Thread(
            target=read_service_status, args=(child, app.service_updates), daemon=True
        ).start()
        if args.auto_exit:
            app.after(int(args.auto_exit * 1000), app.exit_app)
        app.mainloop()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("dashboard", "service", "controller"))
    parser.add_argument(
        "--scenario",
        choices=("idle", "generation", "record", "review", "vad", "hang"),
        default="idle",
    )
    parser.add_argument("--auto-exit", type=float, default=0)
    parser.add_argument("--tray-close", action="store_true")
    parser.add_argument("--managed", action="store_true")
    args = parser.parse_args()
    from platform_adapters.windows.process_guard import guarded_entry

    raise SystemExit(guarded_entry(lambda arm: run(args, arm)))
