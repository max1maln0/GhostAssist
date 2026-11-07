import io
import threading
import time
import unittest
import wave
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from mock_provider import MockProvider
from test_provider_registry import ADAPTER, MODEL, REQUEST

from config.settings import GEMINI_MODELS, Settings
from core.controller import ApplicationController, ViewKind
from core.provider_contracts import (
    ProviderError,
    ProviderErrorCode,
)
from core.provider_registry import (
    ProviderBinding,
    ProviderRegistration,
    ProviderRegistry,
)
from platform_adapters.windows.composition import build_dependencies
from providers.gemini import GeminiProvider
from providers.gemini_catalog import GEMINI_MODEL_CAPABILITIES


class RegistryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None)
        self.registry = ProviderRegistry()
        self.created = {"answer": [], "speech": []}
        for identifier in self.created:

            def factory(model, identifier=identifier):
                provider = MockProvider(chunks=("Вопрос: Пример?\nОтвет: Ответ",))
                self.created[identifier].append((model, provider))
                return provider

            self.registry.register(
                ProviderRegistration(
                    identifier, factory, ADAPTER, {identifier + "-model": MODEL}
                )
            )
        self.deps = build_dependencies(
            self.settings,
            registry=self.registry,
            generation=ProviderBinding("answer", "answer-model"),
            transcription=ProviderBinding("speech", "speech-model"),
        )
        self.deps.screen = SimpleNamespace(capture=lambda: b"image")
        self.deps.audio = SimpleNamespace(record=lambda *args, **kwargs: b"wav")
        self.app = ApplicationController(self.settings, self.deps)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app.close()
        self.app.orchestrator.close(timeout=2)
        self.app._source.shutdown(wait=True)

    def finish(self):
        self.app.orchestrator.future.result(2)
        return self.app.poll()

    def test_composition_routes_different_models_preserves_uuid_stream_and_history(
        self,
    ):
        self.assertEqual(self.created, {"answer": [], "speech": []})
        self.app.sessions.add(
            "INTERVIEW", "previous", "previous answer", 0, threading.Event()
        )
        self.app.audio()
        identifier = self.app.active.request.task_id
        views = self.finish()
        self.assertTrue(any(v.kind is ViewKind.REVIEW for v in views))
        self.assertEqual(len(self.created["speech"]), 1)
        self.assertEqual(self.created["answer"], [])
        self.assertTrue(self.app.confirm(identifier, "edited question"))
        views = self.finish()
        self.assertTrue(any(v.status == "ГЕНЕРАЦИЯ ОТВЕТА" for v in views))
        self.assertEqual(self.created["answer"][0][0], "answer-model")
        self.assertEqual(self.created["speech"][0][0], "speech-model")
        generation = self.created["answer"][0][1]
        speech = self.created["speech"][0][1]
        self.assertEqual(generation.generation_requests[0].data, b"edited question")
        self.assertEqual(
            generation.generation_requests[0].history[0].question, "previous"
        )
        self.assertEqual(speech.transcription_requests[0].audio, b"wav")
        self.assertEqual(generation.transcription_requests, [])
        self.assertEqual(speech.generation_requests, [])
        self.assertEqual((generation.close_count, speech.close_count), (1, 1))
        self.assertTrue(all(v.task_id == identifier for v in views))
        self.assertEqual(
            self.app.sessions.snapshot("INTERVIEW")[1][-1].question, "edited question"
        )

    def test_cancellation_waiting_creates_no_generation_client_or_history(self):
        self.app.audio()
        self.finish()
        self.app.cancel()
        self.app.poll()
        self.assertEqual(self.created["answer"], [])
        self.assertEqual(self.app.sessions.snapshot("INTERVIEW")[1], ())
        self.assertEqual(self.created["speech"][0][1].close_count, 1)

    def test_vad_fifo_uses_both_bindings_and_releases_all_clients(self):
        self.settings.vad_enabled = True
        ready = threading.Event()

        def record(stop, cancel, *args, **kwargs):
            for number in range(1, 4):
                kwargs["on_segment"](number, b"wav")
            ready.set()
            stop.wait(2)

        self.deps.audio.record = record
        self.app.audio()
        self.assertTrue(ready.wait(1))
        deadline = time.monotonic() + 2
        while len(self.created["answer"]) < 3 and time.monotonic() < deadline:
            self.app.poll()
            if self.app.active and self.app.orchestrator.state.value == "waiting":
                self.app.confirm(self.app.active.request.task_id, "confirmed")
            time.sleep(0.005)
        self.app.orchestrator.future.result(1)
        self.app.poll()
        self.assertEqual(len(self.created["answer"]), 3)
        self.assertEqual(len(self.created["speech"]), 3)
        self.assertTrue(self.app.monitoring)
        self.app.cancel()
        self.app.source_future.result(1)
        self.app.poll()
        self.assertFalse(self.app.busy)
        self.assertTrue(
            all(
                provider.close_count == 1
                for created in self.created.values()
                for _, provider in created
            )
        )

    def test_controller_close_closes_registry(self):
        self.app.close()
        with self.assertRaises(ProviderError) as error:
            self.registry.describe("answer")
        self.assertEqual(error.exception.code, ProviderErrorCode.CLOSED)

    def test_default_gemini_composition_never_creates_sdk_client_when_listing(self):
        with patch("providers.gemini_client.genai.Client") as sdk:
            deps = build_dependencies(self.settings)
            try:
                self.assertEqual(deps.registry.identifiers(), ("gemini", "openai"))
                self.assertEqual(deps.provider.binding, deps.transcription.binding)
                deps.registry.describe("gemini")
                sdk.assert_not_called()
            finally:
                deps.orchestrator.close()
                deps.close()

    def test_gemini_registry_path_keeps_fallback_and_sdk_cleanup(self):
        from google.genai import errors
        from test_stability import FakeGemini, chunk

        deps = build_dependencies(Settings(_env_file=None, gemini_api_key="offline"))
        fake = FakeGemini(
            [[chunk("old"), errors.ServerError(503, {})], [chunk("answer", "STOP")]]
        )
        chunks = []
        try:
            with patch("providers.gemini_client.genai.Client", return_value=fake):
                self.assertEqual(
                    deps.llm.generate(
                        b"jpeg",
                        "image/jpeg",
                        "prompt",
                        chunks.append,
                        threading.Event(),
                    ),
                    "answer",
                )
            self.assertEqual(chunks, ["old", "", "answer"])
            self.assertTrue(fake.async_closed and fake.sync_closed)
            self.assertEqual(deps.registry.active, 0)
        finally:
            deps.orchestrator.close()
            deps.close()

    def test_gemini_fallback_skips_incompatible_and_unknown_models(self):
        from google.genai import errors
        from test_stability import FakeGemini, chunk

        provider = GeminiProvider(Settings(_env_file=None, gemini_api_key="offline"))
        provider.client.model_catalog = {
            **GEMINI_MODEL_CAPABILITIES,
            GEMINI_MODELS[1]: replace(MODEL, image_input=False),
        }
        provider.client.models = (
            GEMINI_MODELS[0],
            "unknown",
            GEMINI_MODELS[1],
            GEMINI_MODELS[2],
        )
        fake = FakeGemini([[errors.ServerError(503, {})], [chunk("answer", "STOP")]])
        with patch("providers.gemini_client.genai.Client", return_value=fake):
            self.assertEqual(
                provider.generate_response(
                    REQUEST, lambda _: None, threading.Event()
                ).text,
                "answer",
            )
        self.assertEqual(
            [call["model"] for call in fake.calls], [GEMINI_MODELS[0], GEMINI_MODELS[2]]
        )

    def test_gemini_transcription_uses_independently_selected_model(self):
        from test_stability import FakeGemini, chunk

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(bytes(640))
        deps = build_dependencies(
            Settings(_env_file=None, gemini_api_key="offline"),
            generation=ProviderBinding("gemini", GEMINI_MODELS[0]),
            transcription=ProviderBinding("gemini", GEMINI_MODELS[1]),
        )
        fake = FakeGemini([[chunk("transcript", "STOP")]])
        try:
            with patch("providers.gemini_client.genai.Client", return_value=fake):
                self.assertEqual(
                    deps.llm.transcribe(buffer.getvalue(), threading.Event()),
                    "transcript",
                )
            self.assertEqual(fake.calls[0]["model"], GEMINI_MODELS[1])
            self.assertTrue(fake.async_closed and fake.sync_closed)
            self.assertEqual(deps.registry.active, 0)
        finally:
            deps.orchestrator.close()
            deps.close()

    def test_unknown_gemini_model_is_retained_in_settings_but_never_sent(self):
        settings = Settings(_env_file=None, gemini_model="private-unknown-model")
        deps = build_dependencies(settings)
        try:
            with patch("providers.gemini_client.genai.Client") as sdk:
                with self.assertRaises(ProviderError) as error:
                    deps.llm.generate(
                        b"jpg",
                        "image/jpeg",
                        "prompt",
                        lambda _: None,
                        threading.Event(),
                    )
                self.assertEqual(error.exception.code, ProviderErrorCode.UNKNOWN_MODEL)
                self.assertEqual(settings.gemini_model, "private-unknown-model")
                sdk.assert_not_called()
        finally:
            deps.orchestrator.close()
            deps.close()

    def test_registry_gemini_timeout_and_cancel_close_pending_sdk_stream(self):
        from test_stability import FakeGemini

        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                deps = build_dependencies(
                    Settings(
                        _env_file=None, gemini_api_key="offline", task_timeout=0.12
                    )
                )
                token = threading.Event()
                timer = threading.Timer(0.04, token.set)
                fake = FakeGemini([[None]])
                try:
                    if cancelled:
                        timer.start()
                    with patch(
                        "providers.gemini_client.genai.Client", return_value=fake
                    ):
                        if cancelled:
                            self.assertIsNone(
                                deps.llm.generate(
                                    b"jpg",
                                    "image/jpeg",
                                    "prompt",
                                    lambda _: None,
                                    token,
                                )
                            )
                        else:
                            with self.assertRaises(ProviderError) as error:
                                deps.llm.generate(
                                    b"jpg",
                                    "image/jpeg",
                                    "prompt",
                                    lambda _: None,
                                    token,
                                )
                            self.assertEqual(
                                error.exception.code, ProviderErrorCode.TIMEOUT
                            )
                    self.assertTrue(fake.async_closed and fake.sync_closed)
                    self.assertEqual(fake.stream_closed, 1)
                    self.assertEqual(deps.registry.active, 0)
                finally:
                    if cancelled:
                        timer.join(1)
                    deps.orchestrator.close()
                    deps.close()
