"""Dashboard 2.0 regression coverage, with local files and fake network only."""

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from config.env_file import update_env
from config.settings import Settings, load_settings, service_signature
from providers.access_check import check_access
from ui.dashboard_jobs import JobRunner
from ui.dashboard_settings import (
    SettingsDraft,
    SettingsEditError,
    compatible_models,
    persist_changes,
)


def finish(runner):
    deadline = time.monotonic() + 3
    while runner.busy and time.monotonic() < deadline:
        runner.poll()
        time.sleep(0.005)
    if runner.busy:
        raise AssertionError("Background job did not complete")


class SettingsEditorTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / ".env"
        self.settings = load_settings(self.path)

    def tearDown(self):
        self.folder.cleanup()
        self.environment.stop()

    def test_draft_cancel_never_changes_saved_values(self):
        draft = SettingsDraft(self.settings)
        draft.baseline["AUDIO_DEVICE"] = draft.display("AUDIO_DEVICE")
        draft.stage("AUDIO_DEVICE", "USB")
        self.assertEqual(draft.changes, {"AUDIO_DEVICE": "USB"})
        self.assertEqual(self.settings.audio_device, "")
        draft.reset()
        self.assertEqual(draft.changes, {})

    def test_independent_bindings_roundtrip_and_comments(self):
        self.path.write_text("# preserve\nSCHEMA_VERSION=2\n", encoding="utf-8")
        result = persist_changes(
            self.settings,
            {
                "GENERATION_PROVIDER": "openai",
                "GENERATION_MODEL": "gpt-5.6-terra",
                "TRANSCRIPTION_PROVIDER": "gemini",
                "TRANSCRIPTION_MODEL": "gemini-3.5-flash-lite",
            },
            path=self.path,
        )
        reloaded = load_settings(self.path)
        self.assertEqual(
            reloaded.resolve_binding("generation"), ("openai", "gpt-5.6-terra")
        )
        self.assertEqual(reloaded.resolve_binding("transcription")[0], "gemini")
        self.assertTrue(self.path.read_text().startswith("# preserve"))
        self.assertEqual(result.settings, reloaded)

    def test_stale_same_field_rejected_but_unrelated_patch_merged(self):
        update_env({"AUDIO_DEVICE": "USB"}, self.path)
        with self.assertRaises(SettingsEditError):
            persist_changes(self.settings, {"AUDIO_DEVICE": "HDMI"}, path=self.path)
        result = persist_changes(self.settings, {"MEMORY_DEPTH": "3"}, path=self.path)
        self.assertEqual(result.settings.audio_device, "USB")
        self.assertEqual(result.settings.memory_depth, 3)

    def test_unknown_model_and_modality_rejected_before_write(self):
        self.path.write_text("SCHEMA_VERSION=2\n", encoding="utf-8")
        before = self.path.read_bytes()
        for model in ("unknown", "gpt-transcribe"):
            with self.subTest(model=model), self.assertRaises(SettingsEditError):
                persist_changes(
                    self.settings,
                    {"GENERATION_PROVIDER": "openai", "GENERATION_MODEL": model},
                    path=self.path,
                )
            self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn("gpt-transcribe", compatible_models("openai", "generation"))

    def test_key_save_replace_delete_and_safe_repr(self):
        current = self.settings
        for key in ("offline-key-1", "offline-key-2", ""):
            current = persist_changes(
                current, {"OPENAI_API_KEY": key}, path=self.path
            ).settings
            self.assertEqual(
                load_settings(self.path).openai_api_key.get_secret_value(), key
            )
            self.assertNotIn("offline-key", repr(current))

    def test_signature_detects_service_changes_but_not_dashboard_preferences(self):
        self.assertEqual(
            service_signature(self.settings),
            service_signature(
                self.settings.model_copy(update={"close_to_tray": False})
            ),
        )
        self.assertNotEqual(
            service_signature(self.settings),
            service_signature(self.settings.model_copy(update={"audio_device": "USB"})),
        )
        self.assertNotIn("offline", service_signature(self.settings))

    def test_safe_errors_never_include_validation_input(self):
        from ui.dashboard_actions import safe_save

        result = safe_save(
            self.settings, {"AUDIO_MAX_SECONDS": "private-value"}, self.path, None
        )
        self.assertIn("audio_max_seconds", result.error)
        self.assertNotIn("private-value", result.error)


class BackgroundJobTests(unittest.TestCase):
    def test_bounded_worker_and_main_thread_delivery(self):
        runner = JobRunner()
        main = threading.get_ident()
        ready, release = threading.Event(), threading.Event()
        seen = []

        def work():
            ready.set()
            release.wait(2)
            return threading.get_ident()

        self.assertTrue(
            runner.submit(
                work, lambda result, error: seen.append((result, threading.get_ident()))
            )
        )
        self.assertTrue(ready.wait(1))
        self.assertFalse(runner.submit(work, Mock()))
        release.set()
        finish(runner)
        self.assertNotEqual(seen[0][0], main)
        self.assertEqual(seen[0][1], main)
        previous = runner.thread
        runner.submit(lambda: 1, Mock())
        finish(runner)
        self.assertIs(runner.thread, previous)
        runner.close()
        previous.join(1)
        self.assertFalse(previous.is_alive())

    def test_late_result_cleanup_without_callback(self):
        runner = JobRunner()
        started, release, cleaned = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        callback = Mock()

        def work():
            started.set()
            release.wait(2)
            return object()

        runner.submit(work, callback, lambda result: cleaned.set())
        self.assertTrue(started.wait(1))
        runner.close()
        release.set()
        self.assertTrue(cleaned.wait(1))
        callback.assert_not_called()
        self.assertFalse(runner.submit(Mock(), Mock()))

    def test_secret_exception_is_sanitized(self):
        runner = JobRunner()
        callback = Mock()
        runner.submit(Mock(side_effect=RuntimeError("private-key")), callback)
        finish(runner)
        self.assertNotIn("private-key", str(callback.call_args))
        runner.close()


class AccessCheckTests(unittest.TestCase):
    def settings(self):
        return Settings(
            _env_file=None,
            openai_api_key="offline-private",
            gemini_api_key="offline-gemini",
        )

    def test_metadata_only_no_content_and_correct_authorization(self):
        requests = []

        def respond(request):
            requests.append(request)
            model = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"id": model})

        result = check_access(
            self.settings(),
            "openai",
            ["gpt-5.6-terra"],
            transport=httpx.MockTransport(respond),
        )
        self.assertEqual(result.state, "verified")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(requests[0].content, b"")
        self.assertNotIn("offline-private", str(requests[0].url))
        self.assertNotIn("x-goog-api-key", requests[0].headers)

    def test_gemini_header_and_duplicate_models(self):
        requests = []

        def respond(request):
            requests.append(request)
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, json={"name": "models/gemini-3.5-flash-lite"})

        result = check_access(
            self.settings(),
            "gemini",
            ["gemini-3.5-flash-lite"] * 2,
            transport=httpx.MockTransport(respond),
        )
        self.assertEqual(result.state, "verified")
        self.assertEqual(len(requests), 1)

    def test_auth_model_and_service_errors_hide_response_body(self):
        for status, expected in (
            (401, "auth"),
            (403, "auth"),
            (404, "model"),
            (429, "error"),
            (503, "error"),
        ):
            with self.subTest(status=status):
                result = check_access(
                    self.settings(),
                    "openai",
                    ["gpt-5.6-terra"],
                    transport=httpx.MockTransport(
                        lambda request, status=status: httpx.Response(
                            status, text="private-key"
                        )
                    ),
                )
                self.assertEqual(result.state, expected)
                self.assertNotIn("private-key", repr(result))

    def test_total_timeout(self):
        async def slow(request):
            await asyncio.sleep(0.2)
            return httpx.Response(200, json={"id": "model"})

        started = time.monotonic()
        result = check_access(
            self.settings(),
            "openai",
            ["model"],
            transport=httpx.MockTransport(slow),
            timeout=0.02,
        )
        self.assertEqual(result.state, "timeout")
        self.assertLess(time.monotonic() - started, 0.5)

    def test_missing_key_never_connects(self):
        transport = Mock()
        result = check_access(
            Settings(_env_file=None), "openai", ["model"], transport=transport
        )
        self.assertEqual(result.state, "missing")
        transport.handle_async_request.assert_not_called()

    def test_invalid_metadata_and_oversized_response(self):
        for payload in (
            b"not-json",
            json.dumps({"id": "wrong"}).encode(),
            b"x" * 65537,
        ):
            with self.subTest(size=len(payload)):
                result = check_access(
                    self.settings(),
                    "openai",
                    ["model"],
                    transport=httpx.MockTransport(
                        lambda request, payload=payload: httpx.Response(
                            200, content=payload
                        )
                    ),
                )
                self.assertNotEqual(result.state, "verified")
