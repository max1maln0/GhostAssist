"""Dashboard 2.0 regression coverage, with local files and fake network only."""

import io
import unittest
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

from ui.dashboard_service import ServiceActions, parse_status, read_service_status


class ServiceDashboardTests(unittest.TestCase):
    def test_status_parser_rejects_unknown_or_secret_text(self):
        self.assertIsNone(parse_status(b"private-key error"))
        self.assertIsNone(parse_status(b'GHOST_STATE ["TEST", 1, false, false]'))
        self.assertIsNone(parse_status(b'GHOST_READY {"signature":"private"}'))
        self.assertEqual(
            parse_status(b'GHOST_STATE ["INTERVIEW", true, false, true]'),
            ("state", ["INTERVIEW", True, False, True]),
        )
        self.assertEqual(parse_status(b"GHOST_SESSION 3 4"), ("session", (3, 4)))

    def test_bounded_status_reader_drops_large_lines(self):
        process = SimpleNamespace(
            stdout=io.BytesIO(b"x" * 100000 + b"\nGHOST_SESSION 2 3\n")
        )
        queue = Queue(1)
        read_service_status(process, queue)
        self.assertEqual(queue.get_nowait(), (process, ("session", (2, 3))))
        self.assertTrue(process.stdout.closed)

    def test_apply_restarts_only_owned_service_after_save(self):
        app = SimpleNamespace(
            external_service=False,
            guard_profile=Mock(return_value=True),
            settings=SimpleNamespace(has_api_key=True),
            _service_running=Mock(return_value=True),
            _stop_process=Mock(),
            request_save=Mock(),
            restart_pending=False,
        )
        ServiceActions.apply_and_restart(app)
        app._stop_process.assert_not_called()
        app.request_save.call_args.kwargs["done"](True)
        app._stop_process.assert_called_once()
        self.assertTrue(app.restart_pending)

    def test_external_service_is_not_restarted(self):
        app = SimpleNamespace(
            external_service=True,
            set_error=Mock(),
            request_save=Mock(),
            _stop_process=Mock(),
        )
        ServiceActions.apply_and_restart(app)
        app.request_save.assert_not_called()
        app._stop_process.assert_not_called()

    def test_real_control_commands_are_allowlisted(self):
        process = Mock()
        app = SimpleNamespace(
            service_state="running",
            _service_running=Mock(return_value=True),
            assistant_process=process,
            set_error=Mock(),
        )
        for command in (b"H", b"D", b"M", b"C"):
            self.assertTrue(ServiceActions.send_service(app, command))
        self.assertFalse(ServiceActions.send_service(app, b"unknown"))
        self.assertEqual(process.stdin.write.call_count, 4)


class ServiceStatusLifecycleTests(unittest.TestCase):
    def app(self):
        import threading

        from config.settings import Settings

        process = Mock()
        process.poll.return_value = None
        app = SimpleNamespace(
            save_jobs=Mock(),
            probe_jobs=Mock(),
            device_jobs=Mock(),
            closing=False,
            assistant_process=process,
            dashboard_hotkey=None,
            exit_requested=threading.Event(),
            instance=None,
            tray_commands=Queue(64),
            service_updates=Queue(32),
            stopping_until=None,
            start_deadline=10**20,
            starting_settings=Settings(_env_file=None),
            applied=None,
            running_settings=None,
            service_state="starting",
            live_state=None,
            session_counts=(0, 0),
            integrations=False,
            external_service=False,
            tray=SimpleNamespace(
                running=False, externally_running=False, ready=threading.Event()
            ),
            set_error=Mock(),
            refresh_summary=Mock(),
            after=Mock(),
            _poll_process=Mock(),
            restart_pending=False,
            toggle_assistant_process=Mock(),
            _finish_exit=Mock(),
        )
        app._service_running = lambda: ServiceActions._service_running(app)
        app._stop_process = lambda: ServiceActions._stop_process(app)
        return app

    def test_ready_acknowledges_actual_config_and_stale_process_is_ignored(self):
        from config.settings import service_signature

        app = self.app()
        value = {
            "signature": service_signature(app.starting_settings),
            "generation": ["gemini", "gemini-3.5-flash-lite"],
            "transcription": ["gemini", "gemini-3.5-flash-lite"],
            "vad": False,
        }
        app.service_updates.put((Mock(), ("session", (99, 99))))
        app.service_updates.put((app.assistant_process, ("ready", value)))
        app.service_updates.put(
            (app.assistant_process, ("state", ["TEST", True, False, False]))
        )
        ServiceActions._poll_process(app)
        self.assertEqual(app.service_state, "running")
        self.assertIs(app.running_settings, app.starting_settings)
        self.assertEqual(app.session_counts, (0, 0))
        self.assertEqual(app.live_state[0], "TEST")

    def test_different_child_snapshot_does_not_claim_saved_settings_active(self):
        app = self.app()
        value = {
            "signature": "0" * 64,
            "generation": ["openai", "gpt-5.6-terra"],
            "transcription": ["openai", "gpt-transcribe"],
            "vad": False,
        }
        app.service_updates.put((app.assistant_process, ("ready", value)))
        ServiceActions._poll_process(app)
        self.assertIsNone(app.running_settings)
        self.assertEqual(app.applied["generation"][0], "openai")

    def test_stopping_rejects_late_results_and_watchdog_kills_only_owned_child(self):
        app = self.app()
        app.start_deadline = None
        app.stopping_until = 1
        app.service_state = "stopping"
        app.service_updates.put((app.assistant_process, ("session", (99, 99))))
        ServiceActions._poll_process(app)
        self.assertEqual(app.session_counts, (0, 0))
        app.assistant_process.kill.assert_called_once()

    def test_crash_resets_status_and_sessions(self):
        app = self.app()
        app.assistant_process.poll.return_value = 7
        app.assistant_process.stdin.close.side_effect = BrokenPipeError
        app.session_counts = (4, 2)
        ServiceActions._poll_process(app)
        self.assertEqual(app.service_state, "error")
        self.assertIsNone(app.assistant_process)
        self.assertEqual(app.session_counts, (0, 0))
        app.set_error.assert_called_once()

    def test_restart_before_poll_reaps_dead_child_even_with_broken_pipe(self):
        from pathlib import Path

        app = self.app()
        process = app.assistant_process
        process.poll.return_value = 7
        process.stdin.close.side_effect = BrokenPipeError
        app.settings_path = Path("isolated-dashboard.env")
        app.save_jobs.busy = False
        ServiceActions.toggle_assistant_process(app)
        process.stdin.close.assert_called_once()
        self.assertIsNone(app.assistant_process)
        self.assertEqual(app.service_state, "stopped")

    def test_exit_during_job_callback_never_touches_destroyed_widgets(self):
        app = self.app()

        def exit_now():
            app.closing = True
            app.assistant_process = None

        app.save_jobs.poll.side_effect = exit_now
        ServiceActions._poll_process(app)
        app.refresh_summary.assert_not_called()
        app.after.assert_not_called()


class ScrollbarBridgeTests(unittest.TestCase):
    def test_recursive_scroll_updates_are_coalesced_and_shutdown_ignores_them(self):
        from ui.dashboard_components import ScrollbarBridge

        root = SimpleNamespace(destroying=False)
        callbacks = []
        owner = SimpleNamespace(
            winfo_toplevel=lambda: root,
            after=lambda delay, callback: callbacks.append(callback) or len(callbacks),
        )
        scrollbar = Mock()
        bridge = ScrollbarBridge(owner, scrollbar)
        bridge.set(0, 0.4)
        bridge.set(0, 0.6)
        self.assertEqual(len(callbacks), 1)
        scrollbar.set.side_effect = lambda *values: bridge.set(0.1, 0.8)
        callbacks.pop(0)()
        scrollbar.set.assert_called_once_with(0.0, 0.6)
        self.assertEqual(len(callbacks), 1)
        scrollbar.set.side_effect = None
        callbacks.pop(0)()
        scrollbar.set.assert_called_with(0.1, 0.8)
        self.assertEqual(callbacks, [])
        root.destroying = True
        bridge.set(0, 1)
        self.assertEqual(callbacks, [])
