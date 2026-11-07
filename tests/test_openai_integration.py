import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_openai_provider import OfflineHTTP, completed, wav_bytes
from test_stability import FakeGemini, chunk

from config.settings import Settings
from core.controller import ApplicationController, ViewKind
from platform_adapters.windows.composition import build_dependencies


class OpenAIIntegrationTests(unittest.TestCase):
    def settings(self, **values):
        return Settings(
            _env_file=None,
            openai_api_key="private-openai",
            gemini_api_key="private-gemini",
            **values,
        )

    def controller(self, settings):
        dependencies = build_dependencies(settings)
        dependencies.screen = SimpleNamespace(capture=Mock(return_value=b"jpeg"))
        dependencies.audio = SimpleNamespace(record=Mock(return_value=wav_bytes()))
        app = ApplicationController(settings, dependencies)

        def cleanup():
            app.close()
            app.orchestrator.close(timeout=3)
            app._source.shutdown(wait=True)

        self.addCleanup(cleanup)
        return app, dependencies

    def finish(self, app):
        app.orchestrator.future.result(3)
        return app.poll()

    def test_openai_transcription_gemini_generation_and_reverse_keep_uuid_history(self):
        for speech_openai in (True, False):
            with self.subTest(speech_openai=speech_openai):
                app, deps = self.controller(
                    self.settings(
                        generation_provider="gemini" if speech_openai else "openai",
                        transcription_provider="openai" if speech_openai else "gemini",
                    )
                )
                answer = "Вопрос: Original?\nОтвет: Answer"
                harness = OfflineHTTP(
                    [(200, {"text": "Original?"})]
                    if speech_openai
                    else [[completed(answer)]]
                )
                fake = FakeGemini(
                    [[chunk(answer if speech_openai else "Original?", "STOP")]]
                )
                with (
                    harness.install(),
                    patch(
                        "providers.gemini_client.genai.Client", return_value=fake
                    ) as gemini,
                ):
                    app.audio()
                    identifier = app.active.request.task_id
                    views = self.finish(app)
                    self.assertTrue(
                        any(
                            v.kind is ViewKind.REVIEW and v.text == "Original?"
                            for v in views
                        )
                    )
                    self.assertTrue(app.confirm(identifier, "Edited?"))
                    views = self.finish(app)
                self.assertTrue(all(v.task_id == identifier for v in views))
                self.assertEqual(
                    app.sessions.snapshot("INTERVIEW")[1][0].question, "Edited?"
                )
                self.assertEqual(len(harness.requests), 1)
                self.assertEqual(
                    harness.requests[0].headers["authorization"],
                    "Bearer private-openai",
                )
                self.assertEqual(gemini.call_args.kwargs["api_key"], "private-gemini")
                self.assertTrue(fake.async_closed and fake.sync_closed)
                self.assertTrue(all(client.is_closed for client in harness.clients))
                self.assertEqual(deps.registry.active, 0)

    def test_openai_fullscreen_selection_and_conflicts(self):
        app, deps = self.controller(self.settings(generation_provider="openai"))
        harness = OfflineHTTP(
            [
                [completed("Вопрос: Screen?\nОтвет: A")],
                [completed("Вопрос: Area?\nОтвет: B")],
            ]
        )
        with harness.install():
            app.capture()
            original_id = app.active.request.task_id
            app.snip()
            app.audio()
            self.assertEqual(app.active.request.task_id, original_id)
            self.finish(app)
            app.snip()
            identifier = app.active.request.task_id
            app.poll()
            bbox = {"left": -30, "top": 0, "width": 40, "height": 40}
            app.selected(identifier, bbox)
            self.finish(app)
        deps.screen.capture.assert_called_with(bbox)
        deps.audio.record.assert_not_called()
        self.assertEqual(len(app.sessions.snapshot("TEST")[1]), 2)
        self.assertEqual(len(harness.requests), 2)

    def test_cancel_pending_generation_then_immediately_restart_after_cleanup(self):
        app, deps = self.controller(self.settings(generation_provider="openai"))
        harness = OfflineHTTP([[None], [completed("Вопрос: New?\nОтвет: New")]])
        with harness.install():
            app.capture()
            self.assertTrue(harness.started.wait(2))
            app.cancel()
            self.finish(app)
            self.assertEqual(app.sessions.snapshot("TEST")[1], ())
            self.assertEqual(deps.registry.active, 0)
            app.capture()
            self.finish(app)
        self.assertEqual(app.sessions.snapshot("TEST")[1][0].answer.strip(), "New")
        self.assertEqual(harness.transport_closes, 2)

    def test_shutdown_during_transcription_releases_client_and_ignores_result(self):
        app, deps = self.controller(self.settings(transcription_provider="openai"))
        harness = OfflineHTTP([None])
        with harness.install():
            app.audio()
            self.assertTrue(harness.started.wait(2))
            app.close()
            self.finish(app)
        self.assertTrue(app.stopped)
        self.assertEqual(deps.registry.active, 0)
        self.assertEqual(app.sessions.snapshot("INTERVIEW")[1], ())
        self.assertTrue(all(client.is_closed for client in harness.clients))
        self.assertEqual(harness.transport_closes, 1)

    def test_vad_fifo_openai_transcription_gemini_generation(self):
        app, deps = self.controller(
            self.settings(transcription_provider="openai", vad_enabled=True)
        )
        ready = threading.Event()

        def record(stop, cancel, *args, **kwargs):
            for number in range(1, 4):
                kwargs["on_segment"](number, wav_bytes())
            ready.set()
            stop.wait(4)

        deps.audio.record = record
        harness = OfflineHTTP([(200, {"text": f"Question {i}"}) for i in range(1, 4)])
        fake = FakeGemini([[chunk("Answer", "STOP")] for _ in range(3)])
        with (
            harness.install(),
            patch("providers.gemini_client.genai.Client", return_value=fake),
        ):
            app.audio()
            self.assertTrue(ready.wait(1))
            app.poll()
            for _ in range(3):
                views = self.finish(app)
                review = next(v for v in views if v.kind is ViewKind.REVIEW)
                self.assertTrue(app.monitoring)
                self.assertTrue(app.confirm(review.task_id, review.text))
                self.finish(app)
            self.assertEqual(
                [i.question for i in app.sessions.snapshot("INTERVIEW")[1]],
                ["Question 1", "Question 2", "Question 3"],
            )
            app.cancel()
            app.source_future.result(2)
            app.poll()
        self.assertFalse(app.busy)
        self.assertEqual(harness.transport_closes, 3)
        self.assertEqual(deps.registry.active, 0)

    def test_100_registry_requests_and_concurrent_calls_release_all_clients(self):
        deps = build_dependencies(self.settings(generation_provider="openai"))
        self.addCleanup(deps.close)
        self.addCleanup(deps.orchestrator.close)
        harness = OfflineHTTP([[completed("answer")] for _ in range(112)])

        def run(_):
            return deps.llm.generate(
                b"text", "text/plain", "prompt", lambda _: None, threading.Event()
            )

        with harness.install():
            for number in range(100):
                self.assertEqual(run(number), "answer")
                self.assertEqual(deps.registry.active, 0)
            with ThreadPoolExecutor(max_workers=4) as executor:
                self.assertEqual(list(executor.map(run, range(12))), ["answer"] * 12)
        self.assertEqual(harness.transport_closes, 112)
        self.assertTrue(all(client.is_closed for client in harness.clients))
        self.assertEqual(deps.registry.active, 0)

    def test_lazy_registry_creates_neither_sdk_client_when_listing(self):
        with (
            patch("providers.openai_provider.openai.AsyncOpenAI") as openai,
            patch("providers.gemini_client.genai.Client") as gemini,
        ):
            deps = build_dependencies(self.settings())
            try:
                self.assertEqual(deps.registry.identifiers(), ("gemini", "openai"))
                deps.registry.describe("openai")
                openai.assert_not_called()
                gemini.assert_not_called()
            finally:
                deps.close()
                deps.orchestrator.close()
