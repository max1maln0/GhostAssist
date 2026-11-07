import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from mock_provider import MockProvider

from core.contracts import ProviderCapabilities
from core.model_capabilities import ModelCapabilities
from core.provider_contracts import (
    GenerationRequest,
    ProviderError,
    ProviderErrorCode,
    TranscriptionRequest,
)
from core.provider_registry import (
    ProviderBinding,
    ProviderRegistration,
    ProviderRegistry,
)
from core.provider_routing import BoundGeneration, BoundTranscription
from core.session_manager import Interaction

ADAPTER = ProviderCapabilities(text=True, images=True, audio=True, streaming=True)
MODEL = ModelCapabilities(
    text_input=True,
    image_input=True,
    audio_input=True,
    transcription=True,
    streaming=True,
    mime_types=frozenset({"text/plain", "image/jpeg", "audio/wav"}),
    max_input_bytes=10000,
    max_request_bytes=20000,
    source="offline fixture",
)
REQUEST = GenerationRequest(b"image", "image/jpeg", "private prompt", timeout=1)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ProviderRegistry()
        self.addCleanup(self.registry.close)
        self.instances = []
        self.factory = Mock(side_effect=self.create)
        self.registry.register(
            ProviderRegistration(
                "mock",
                self.factory,
                ADAPTER,
                {
                    "full": MODEL,
                    "unknown": ModelCapabilities(),
                    "text": replace(
                        MODEL, image_input=False, audio_input=False, transcription=False
                    ),
                },
            )
        )
        self.binding = ProviderBinding("mock", "full")
        self.bound = BoundGeneration(self.registry, self.binding)
        self.cancel = threading.Event()

    def create(self, model):
        provider = MockProvider()
        self.instances.append(provider)
        return provider

    def assert_code(self, code, call):
        with self.assertRaises(ProviderError) as error:
            call()
        self.assertEqual(error.exception.code, code)
        return str(error.exception)

    def generate(self):
        return self.bound.generate_response(REQUEST, lambda _: None, self.cancel)

    def test_registration_listing_is_lazy_and_catalog_is_immutable(self):
        self.assertEqual(self.registry.identifiers(), ("mock",))
        self.assertIs(self.registry.describe("mock").models["full"], MODEL)
        self.factory.assert_not_called()
        with self.assertRaises(TypeError):
            self.registry.describe("mock").models["new"] = MODEL
        self.assert_code(
            ProviderErrorCode.INVALID_PROVIDER,
            lambda: self.registry.register(
                ProviderRegistration("mock", self.factory, ADAPTER, {})
            ),
        )
        self.assert_code(
            ProviderErrorCode.UNKNOWN_PROVIDER,
            lambda: self.registry.describe("not-registered"),
        )

    def test_acquire_and_release_per_operation(self):
        with self.registry.acquire(self.binding) as provider:
            self.assertEqual(self.registry.active, 1)
            self.assertEqual(provider.close_count, 0)
        self.assertEqual(self.registry.active, 0)
        self.assertEqual(provider.close_count, 1)
        self.generate()
        self.assertEqual(len(self.instances), 2)
        self.assertTrue(all(p.close_count == 1 for p in self.instances))
        self.factory.assert_called_with("full")

    def test_audio_only_non_streaming_transcription_backend_is_valid(self):
        self.registry.register(
            ProviderRegistration(
                "asr",
                self.factory,
                ProviderCapabilities(audio=True),
                {
                    "speech": replace(
                        MODEL, text_input=False, image_input=False, streaming=False
                    )
                },
            )
        )
        bound = BoundTranscription(self.registry, ProviderBinding("asr", "speech"))
        self.assertEqual(
            bound.transcribe_audio(TranscriptionRequest(b"wav"), self.cancel).text,
            "transcript",
        )
        self.assertEqual(self.instances[0].close_count, 1)

    def test_close_racing_factory_creation_releases_new_client(self):
        entered, release = threading.Event(), threading.Event()
        provider = MockProvider()
        errors = []

        def factory(model):
            entered.set()
            release.wait(2)
            return provider

        self.factory.side_effect = factory

        def run():
            try:
                self.generate()
            except ProviderError as error:
                errors.append(error.code)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.registry.close()
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [ProviderErrorCode.CLOSED])
        self.assertEqual(provider.close_count, 1)
        self.assertEqual(self.registry.active, 0)

    def test_repeated_requests_do_not_retain_clients(self):
        for _ in range(100):
            self.generate()
            self.assertEqual(self.registry.active, 0)
        self.assertEqual(len(self.instances), 100)
        self.assertTrue(all(p.close_count == 1 for p in self.instances))

    def test_unknown_provider_model_and_capability_never_create_clients(self):
        for binding, code in (
            (ProviderBinding("missing", "full"), ProviderErrorCode.UNKNOWN_PROVIDER),
            (ProviderBinding("mock", "missing"), ProviderErrorCode.UNKNOWN_MODEL),
            (ProviderBinding("mock", "unknown"), ProviderErrorCode.UNKNOWN_CAPABILITY),
            (ProviderBinding("mock", "text"), ProviderErrorCode.UNSUPPORTED_CAPABILITY),
        ):
            self.bound = BoundGeneration(self.registry, binding)
            self.assert_code(code, self.generate)
        self.factory.assert_not_called()

    def test_each_model_feature_and_format_is_checked(self):
        for capabilities, request, transcription, code in (
            (
                replace(MODEL, text_input=False),
                REQUEST,
                False,
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
            ),
            (
                replace(MODEL, streaming=None),
                REQUEST,
                False,
                ProviderErrorCode.UNKNOWN_CAPABILITY,
            ),
            (
                replace(MODEL, mime_types=None),
                REQUEST,
                False,
                ProviderErrorCode.UNKNOWN_CAPABILITY,
            ),
            (
                replace(MODEL, mime_types=frozenset({"text/plain"})),
                REQUEST,
                False,
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
            ),
            (
                replace(MODEL, transcription=None),
                replace(REQUEST, mime_type="audio/wav"),
                True,
                ProviderErrorCode.UNKNOWN_CAPABILITY,
            ),
            (
                replace(MODEL, transcription=False),
                replace(REQUEST, mime_type="audio/wav"),
                True,
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
            ),
        ):
            with self.subTest(capabilities=capabilities):
                self.assert_code(
                    code,
                    lambda c=capabilities, r=request, t=transcription: c.validate(
                        r, transcription=t
                    ),
                )

    def test_adapter_and_model_capabilities_are_both_required(self):
        self.registry.register(
            ProviderRegistration(
                "text_adapter",
                self.factory,
                ProviderCapabilities(text=True, streaming=True),
                {"full": MODEL},
            )
        )
        self.bound = BoundGeneration(
            self.registry, ProviderBinding("text_adapter", "full")
        )
        self.assert_code(ProviderErrorCode.UNSUPPORTED_CAPABILITY, self.generate)
        self.factory.assert_not_called()

    def test_size_policy_accounts_for_base64_and_history(self):
        for caps, request in (
            (replace(MODEL, max_input_bytes=4), REQUEST),
            (
                replace(MODEL, max_request_bytes=5000),
                replace(REQUEST, data=bytes(1000)),
            ),
            (
                replace(MODEL, max_request_bytes=5000),
                replace(REQUEST, history=(Interaction("q" * 1000, "a" * 1000),)),
            ),
            (MODEL, replace(REQUEST, data=b"\xff", mime_type="text/plain")),
        ):
            self.assert_code(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                lambda c=caps, r=request: c.validate(r),
            )

    def test_missing_interface_closes_created_provider(self):
        invalid = SimpleNamespace(close=Mock())
        self.factory.side_effect = None
        self.factory.return_value = invalid
        self.assert_code(ProviderErrorCode.INVALID_PROVIDER, self.generate)
        invalid.close.assert_called_once()
        self.assertEqual(self.registry.active, 0)

    def test_generation_only_and_transcription_only_interfaces(self):
        for transcription, method in (
            (False, "generate_response"),
            (True, "transcribe_audio"),
        ):
            provider = SimpleNamespace(close=Mock(), **{method: Mock()})
            self.factory.side_effect = None
            self.factory.return_value = provider
            with self.registry.acquire(self.binding, transcription=transcription):
                pass
            provider.close.assert_called_once()

    def test_factory_and_operation_errors_are_sanitized_and_clients_closed(self):
        self.factory.side_effect = ProviderError(
            ProviderErrorCode.AUTH_FAILED, "Authentication failed"
        )
        self.assert_code(ProviderErrorCode.AUTH_FAILED, self.generate)
        self.factory.side_effect = RuntimeError("private-api-key")
        self.assertNotIn(
            "private-api-key",
            self.assert_code(ProviderErrorCode.INVALID_PROVIDER, self.generate),
        )
        self.assertEqual(self.registry.active, 0)
        provider = MockProvider()
        provider.generate_response = Mock(side_effect=RuntimeError("private-api-key"))
        self.factory.side_effect = None
        self.factory.return_value = provider
        self.assertNotIn(
            "private-api-key",
            self.assert_code(ProviderErrorCode.PROVIDER_ERROR, self.generate),
        )
        self.assertEqual(provider.close_count, 1)

    def test_typed_error_and_cancellation_release_clients(self):
        provider = MockProvider(error=ProviderErrorCode.AUTH_FAILED)
        self.factory.side_effect = None
        self.factory.return_value = provider
        self.assert_code(ProviderErrorCode.AUTH_FAILED, self.generate)
        self.assertEqual(provider.close_count, 1)
        self.cancel.set()
        self.assert_code(ProviderErrorCode.CANCELLED, self.generate)
        self.assertEqual(provider.close_count, 1)
        self.cancel.clear()
        provider.error = None
        events = []

        def publish(text):
            events.append(text)
            self.cancel.set()

        self.assert_code(
            ProviderErrorCode.CANCELLED,
            lambda: self.bound.generate_response(REQUEST, publish, self.cancel),
        )
        self.assertEqual(provider.close_count, 2)

    def test_close_error_never_masks_original_failure(self):
        provider = MockProvider(error=ProviderErrorCode.AUTH_FAILED)
        provider.close = Mock(side_effect=RuntimeError("private-api-key"))
        self.factory.side_effect = None
        self.factory.return_value = provider
        self.assert_code(ProviderErrorCode.AUTH_FAILED, self.generate)
        provider.error = None
        self.assertNotIn(
            "private-api-key",
            self.assert_code(ProviderErrorCode.PROVIDER_ERROR, self.generate),
        )
        self.assertEqual(self.registry.active, 0)

    def test_close_during_active_lease_defers_cleanup_and_discards_result(self):
        provider = MockProvider()

        def generate(request, publish, cancel):
            self.registry.close()
            self.assertEqual(provider.close_count, 0)
            return SimpleNamespace(text="late")

        provider.generate_response = generate
        self.factory.side_effect = None
        self.factory.return_value = provider
        self.assert_code(ProviderErrorCode.CLOSED, self.generate)
        self.assertEqual(provider.close_count, 1)
        self.assert_code(ProviderErrorCode.CLOSED, self.generate)
        self.registry.close()

    def test_factory_time_counts_against_budget_and_cancel_prevents_call(self):
        provider = MockProvider()

        def create(model):
            time.sleep(0.02)
            return provider

        self.factory.side_effect = create
        request = replace(REQUEST, timeout=0.001)
        self.assert_code(
            ProviderErrorCode.TIMEOUT,
            lambda: self.bound.generate_response(request, lambda _: None, self.cancel),
        )
        self.assertEqual(provider.generation_requests, [])
        self.assertEqual(provider.close_count, 1)

        def cancel_factory(model):
            self.cancel.set()
            return provider

        self.factory.side_effect = cancel_factory
        self.assert_code(ProviderErrorCode.CANCELLED, self.generate)
        self.assertEqual(provider.generation_requests, [])

    def test_transcription_failure_and_cancellation_close_once(self):
        provider = MockProvider(error=ProviderErrorCode.TIMEOUT)
        self.factory.side_effect = None
        self.factory.return_value = provider
        bound = BoundTranscription(self.registry, self.binding)

        def call():
            return bound.transcribe_audio(TranscriptionRequest(b"wav"), self.cancel)

        self.assert_code(ProviderErrorCode.TIMEOUT, call)
        self.assertEqual(provider.close_count, 1)
        provider.error = None

        def late(request, cancel):
            cancel.set()
            return SimpleNamespace(text="late")

        provider.transcribe_audio = late
        self.assert_code(ProviderErrorCode.CANCELLED, call)
        self.assertEqual(provider.close_count, 2)
