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
