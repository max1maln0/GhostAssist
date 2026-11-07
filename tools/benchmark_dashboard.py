"""Offline GUI benchmark: temporary settings, no API calls."""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
started = perf_counter()
import customtkinter as ctk  # noqa: E402 - measure cold imports

import ui.dashboard as dashboard  # noqa: E402


def run(integrations=False, shutdown_notify=None):
    times = {"imports_ms": (perf_counter() - started) * 1000}

    def timed(name, function):
        def wrapped(*args, **kwargs):
            begin = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                times[name] = (perf_counter() - begin) * 1000

        return wrapped

    environment = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in dashboard.Settings.model_fields
    }
    with (
        tempfile.TemporaryDirectory() as folder,
        patch.dict(os.environ, environment, clear=True),
        ExitStack() as stack,
    ):
        for name, (_, page) in dashboard.PAGES.items():
            stack.enter_context(
                patch.object(page, "build", timed("build_" + name, page.build))
            )
        stack.enter_context(
            patch.object(ctk.CTk, "__init__", timed("tk_create", ctk.CTk.__init__))
        )
        stack.enter_context(
            patch.object(
                dashboard,
                "load_settings",
                timed("settings_load", dashboard.load_settings),
            )
        )
        path = Path(folder)
        (path / ".env").write_text(
            "SCHEMA_VERSION=2\nCLOSE_TO_TRAY=false\n", encoding="utf-8"
        )
        begin = perf_counter()
        app = dashboard.DashboardApp(
            settings_path=path / ".env",
            profiles_directory=path,
            start_integrations=integrations,
            shutdown_notify=shutdown_notify,
        )
        app.update()
        times["first_frame"] = (perf_counter() - begin) * 1000
        times["initial_pages"] = len(app.pages)
        if integrations:
            deadline = perf_counter() + 5
            while (
                app.save_jobs.busy or not app.tray.ready.is_set()
            ) and perf_counter() < deadline:
                app.update()
                time.sleep(0.01)
            times["hooks_ms"] = app.timings.get("hooks_ms")
            if app.tray.ready_at is not None:
                times["tray_ready_ms"] = (
                    app.tray.ready_at - app.tray.started_at
                ) * 1000
        for name in dashboard.PAGES:
            app.navigate(name)
            app.update_idletasks()
        samples = []
        for index in range(100):
            begin = perf_counter()
            app.navigate(tuple(dashboard.PAGES)[index % 7])
            app.update_idletasks()
            samples.append((perf_counter() - begin) * 1000)
        times.update(
            navigation_median=statistics.median(samples),
            navigation_p95=sorted(samples)[94],
            navigation_max=max(samples),
        )
        app._begin_exit()
    return {
        key: round(value, 3) if isinstance(value, float) else value
        for key, value in times.items()
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--integrations", action="store_true")
    args = parser.parse_args()
    from platform_adapters.windows.process_guard import guarded_entry

    def measured(arm):
        result = run(args.integrations, arm)
        if args.output:
            args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 0

    raise SystemExit(guarded_entry(measured))
