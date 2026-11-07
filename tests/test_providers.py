import threading
import unittest
from unittest.mock import patch

from mock_provider import MockProvider

from config.settings import Settings
from core.contracts import ProviderCapabilities
from core.llm_worker import LLMWorker
from core.provider_contracts import (
    GenerationRequest,
    ProviderError,
    ProviderErrorCode,
    TranscriptionRequest,
)
from providers.gemini import GeminiProvider


class ProviderTests(unittest.TestCase):
    def test_screenshot_orchestrator_uses_injected_provider_and_stores_history(self):
        from main import GhostAssistApp

        with patch("main.StealthHUD"):
            app = GhostAssistApp(Settings(_env_file=None))
        app.llm.provider = MockProvider(chunks=("Вопрос: Пример?\n", "Ответ: Ответ"))
        try:
            with patch.object(app.screen_capture, "capture", return_value=b"jpeg"):
                app.capture()
                app.future.result(2)
                app.poll()
            self.assertEqual(app.sessions.snapshot("TEST")[1][0].answer, "Ответ")
            self.assertIsNone(app.controller.active)
        finally:
            app.controller.orchestrator.close(timeout=1)
            app.controller.close()
            app.controller._source.shutdown(wait=True)

    def test_offline_generation_stream_and_transcription(self):
        provider = MockProvider(chunks=("one", " two"))
        events = []
        request = GenerationRequest(b"image", "image/jpeg", "prompt")
        self.assertEqual(
            provider.generate_response(request, events.append, threading.Event()).text,
            "one two",
        )
        self.assertEqual(events, ["one", "one two"])
        self.assertEqual(
            provider.transcribe_audio(
                TranscriptionRequest(b"audio"), threading.Event()
            ).text,
            "transcript",
        )

    def test_fake_timeout_cancel_empty_and_unsupported(self):
        cancel = threading.Event()
        request = GenerationRequest(b"x", "text/plain", "prompt", timeout=0.01)
        for provider, code in (
            (MockProvider(delay=0.1), ProviderErrorCode.TIMEOUT),
            (MockProvider(chunks=()), ProviderErrorCode.PROVIDER_ERROR),
            (
                MockProvider(error=ProviderErrorCode.NETWORK_ERROR),
                ProviderErrorCode.NETWORK_ERROR,
            ),
        ):
            with self.subTest(code=code), self.assertRaises(ProviderError) as error:
                provider.generate_response(request, lambda _: None, cancel)
            self.assertEqual(error.exception.code, code)
        cancel.set()
        with self.assertRaises(ProviderError) as error:
            MockProvider().generate_response(request, lambda _: None, cancel)
        self.assertEqual(error.exception.code, ProviderErrorCode.CANCELLED)
        with self.assertRaises(ProviderError) as error:
            MockProvider(chunks=()).generate_response(request, lambda _: None, cancel)
        self.assertEqual(error.exception.code, ProviderErrorCode.CANCELLED)
        with self.assertRaises(ProviderError) as error:
            GeminiProvider(Settings(_env_file=None)).generate_response(
                GenerationRequest(b"x", "video/mp4", "prompt"),
                lambda _: None,
                threading.Event(),
            )
        self.assertEqual(error.exception.code, ProviderErrorCode.UNSUPPORTED_CAPABILITY)

        provider = GeminiProvider(Settings(_env_file=None))
        provider.capabilities = ProviderCapabilities(text=True)
        with self.assertRaises(ProviderError) as error:
            provider.generate_response(
                GenerationRequest(b"image", "image/jpeg", "prompt"),
                lambda _: None,
                threading.Event(),
            )
        self.assertEqual(error.exception.code, ProviderErrorCode.UNSUPPORTED_CAPABILITY)

    def test_facade_uses_injected_provider_for_generation_and_audio(self):
        worker = LLMWorker(Settings(_env_file=None), provider=MockProvider())
        self.assertEqual(
            worker.generate(b"q", "text/plain", "p", lambda _: None, threading.Event()),
            "answer",
        )
        self.assertEqual(worker.transcribe(b"wav", threading.Event()), "transcript")

    def test_authentication_error_has_code_and_no_secret(self):
        from google.genai import errors
        from test_stability import FakeGemini

        settings = Settings(_env_file=None, gemini_api_key="test")
        worker = LLMWorker(settings, provider=GeminiProvider(settings))
        fake = FakeGemini([[errors.ClientError(401, {"message": "secret"})]])
        with patch("providers.gemini_client.genai.Client", return_value=fake):
            with self.assertRaises(ProviderError) as error:
                worker.generate(
                    b"q", "text/plain", "p", lambda _: None, threading.Event()
                )
        self.assertEqual(error.exception.code, ProviderErrorCode.AUTH_FAILED)
        self.assertNotIn("secret", str(error.exception))

    def test_gemini_http_errors_have_typed_codes(self):
        from google.genai import errors
        from test_stability import FakeGemini

        for status, expected in (
            (404, ProviderErrorCode.MODEL_UNAVAILABLE),
            (408, ProviderErrorCode.TIMEOUT),
            (429, ProviderErrorCode.RATE_LIMIT),
            (503, ProviderErrorCode.PROVIDER_ERROR),
        ):
            with self.subTest(status=status):
                provider = GeminiProvider(
                    Settings(_env_file=None, gemini_api_key="test")
                )
                provider.client.models = (provider.client.model,)
                error_cls = errors.ServerError if status >= 500 else errors.ClientError
                fake = FakeGemini([[error_cls(status, {"message": "sensitive"})]])
                with patch("providers.gemini_client.genai.Client", return_value=fake):
                    with self.assertRaises(ProviderError) as error:
                        provider.generate_response(
                            GenerationRequest(b"q", "text/plain", "prompt"),
                            lambda _: None,
                            threading.Event(),
                        )
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn("sensitive", str(error.exception))
