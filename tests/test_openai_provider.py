"""Real official SDK against offline HTTP transports, never the live API."""

import asyncio
import io
import json
import logging
import threading
import unittest
import wave
from collections import deque
from dataclasses import replace
from unittest.mock import patch

import httpx

from config.settings import Settings
from core.provider_contracts import (
    GenerationRequest,
    ProviderError,
    ProviderErrorCode,
    TranscriptionRequest,
)
from core.session_manager import Interaction
from providers.openai_provider import OpenAIProvider


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(1920))
    return buffer.getvalue()


def delta(text):
    return {
        "type": "response.output_text.delta",
        "delta": text,
        "item_id": "msg_test",
        "output_index": 0,
        "content_index": 0,
        "sequence_number": 1,
    }


def completed(text):
    return {
        "type": "response.completed",
        "sequence_number": 2,
        "response": {
            "id": "resp_test",
            "object": "response",
            "created_at": 0,
            "model": "gpt-5.6-terra",
            "status": "completed",
            "output": [
                {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": text, "annotations": []}
                    ],
                }
            ],
        },
    }


class OfflineStream(httpx.AsyncByteStream):
    def __init__(self, events):
        self.events = events
        self.closed = 0

    async def __aiter__(self):
        for event in self.events:
            if event is None:
                await asyncio.Event().wait()
            else:
                yield (
                    "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                ).encode()

    async def aclose(self):
        self.closed += 1


class OfflineHTTP:
    def __init__(self, scripts=()):
        self.scripts = deque(scripts)
        self.requests = []
        self.clients = []
        self.streams = []
        self.transport_closes = 0
        self.started = threading.Event()

    async def handle(self, request):
        self.requests.append(request)
        self.started.set()
        if not self.scripts:
            raise AssertionError("Unexpected API call")
        script = self.scripts.popleft()
        if script is None:
            await asyncio.Event().wait()
        if isinstance(script, Exception):
            raise script
        if isinstance(script, list):
            stream = OfflineStream(script)
            self.streams.append(stream)
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        status, body = script
        return httpx.Response(status, json=body)

    def client(self, timeout):
        harness = self

        class Transport(httpx.MockTransport):
            async def aclose(self):
                harness.transport_closes += 1

        client = httpx.AsyncClient(
            transport=Transport(self.handle), timeout=timeout, follow_redirects=False
        )
        self.clients.append(client)
        return client

    def install(self):
        harness = self
        return patch.object(
            OpenAIProvider,
            "_http_client",
            lambda self, timeout: harness.client(timeout),
        )


class OpenAIProviderTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            _env_file=None,
            openai_api_key="private-test-key",
            generation_provider="openai",
            transcription_provider="openai",
            task_timeout=5,
            request_timeout=20,
        )
        self.request = GenerationRequest(
            b"jpeg",
            "image/jpeg",
            "private profile",
            history=(Interaction("old question", "old answer"),),
            timeout=20,
        )
        self.cancel = threading.Event()

    def generate(self, harness, *, request=None, publish=None):
        provider = OpenAIProvider(self.settings, "gpt-5.6-terra")
        with harness.install():
            return provider.generate_response(
                request or self.request, publish or (lambda _: None), self.cancel
            )

    def transcribe(self, harness, data=None, *, timeout=20):
        with harness.install():
            return OpenAIProvider(self.settings, "gpt-transcribe").transcribe_audio(
                TranscriptionRequest(wav_bytes() if data is None else data, timeout),
                self.cancel,
            )

    def assert_released(self, harness):
        self.assertTrue(all(c.is_closed for c in harness.clients))
        self.assertEqual(harness.transport_closes, len(harness.clients))
        self.assertTrue(all(s.closed >= 1 for s in harness.streams))

    def test_responses_image_history_profile_stream_and_no_server_state(self):
        harness = OfflineHTTP([[delta("one"), delta(" two"), completed("one two")]])
        chunks = []
        self.assertEqual(self.generate(harness, publish=chunks.append).text, "one two")
        self.assertEqual(chunks, ["one", "one two"])
        payload = json.loads(harness.requests[0].content)
        self.assertEqual(payload["instructions"], "private profile")
        self.assertEqual(
            [m["role"] for m in payload["input"]], ["user", "assistant", "user"]
        )
        self.assertEqual(
            payload["input"][-1]["content"][0]["image_url"],
            "data:image/jpeg;base64,anBlZw==",
        )
        self.assertFalse(payload["store"])
        self.assertNotIn("previous_response_id", payload)
        self.assertEqual(
            harness.requests[0].headers["authorization"], "Bearer private-test-key"
        )
        self.assert_released(harness)

    def test_text_input_and_final_completed_text(self):
        harness = OfflineHTTP([[delta("partial"), completed("corrected")]])
        chunks = []
        result = self.generate(
            harness,
            request=replace(
                self.request, data="question?".encode(), mime_type="text/plain"
            ),
            publish=chunks.append,
        )
        self.assertEqual(result.text, "corrected")
        self.assertEqual(chunks, ["partial", "corrected"])
        self.assertEqual(
            json.loads(harness.requests[0].content)["input"][-1]["content"][0],
            {"type": "input_text", "text": "question?"},
        )

    def test_cancel_on_chunk_prevents_later_chunks(self):
        harness = OfflineHTTP([[delta("first"), delta("late"), completed("firstlate")]])
        chunks = []

        def publish(text):
            chunks.append(text)
            self.cancel.set()

        with self.assertRaises(ProviderError) as error:
            self.generate(harness, publish=publish)
        self.assertEqual(error.exception.code, ProviderErrorCode.CANCELLED)
        self.assertEqual(chunks, ["first"])
        self.assert_released(harness)

    def test_transcription_sends_wav_multipart_and_returns_typed_text(self):
        harness = OfflineHTTP([(200, {"text": "Русский English"})])
        self.assertEqual(self.transcribe(harness).text, "Русский English")
        request = harness.requests[0]
        self.assertEqual(request.url.path, "/v1/audio/transcriptions")
        self.assertIn(b'filename="recording.wav"', request.content)
        self.assertIn(b"audio/wav", request.content)
        self.assertIn(wav_bytes(), request.content)
        self.assertIn(b"gpt-transcribe", request.content)
        self.assertNotIn(b'language"', request.content)
        self.assert_released(harness)

    def test_invalid_wav_unknown_model_wrong_modality_and_size_never_connect(self):
        harness = OfflineHTTP()
        with harness.install():
            for data in (b"", b"bad wav", wav_bytes()[:-10]):
                with self.assertRaises(ProviderError):
                    self.transcribe(harness, data)
            for model, request, code in (
                ("unknown", self.request, ProviderErrorCode.UNKNOWN_MODEL),
                (
                    "gpt-transcribe",
                    self.request,
                    ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                ),
                (
                    "gpt-5.6-terra",
                    replace(self.request, mime_type="audio/wav"),
                    ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                ),
                (
                    "gpt-5.6-terra",
                    replace(self.request, data=bytes(20_000_001)),
                    ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                ),
            ):
                with self.assertRaises(ProviderError) as error:
                    OpenAIProvider(self.settings, model).generate_response(
                        request, lambda _: None, self.cancel
                    )
                self.assertEqual(error.exception.code, code)
        self.assertEqual(harness.clients, [])

    def test_terminal_http_errors_do_not_retry_or_expose_details(self):
        for status, api_code, expected in (
            (401, "invalid_api_key", ProviderErrorCode.AUTH_FAILED),
            (403, "permission_denied", ProviderErrorCode.AUTH_FAILED),
            (404, "model_not_found", ProviderErrorCode.MODEL_UNAVAILABLE),
            (429, "insufficient_quota", ProviderErrorCode.RATE_LIMIT),
            (400, "invalid_request_error", ProviderErrorCode.PROVIDER_ERROR),
        ):
            for transcription in (False, True):
                harness = OfflineHTTP(
                    [
                        (
                            status,
                            {
                                "error": {
                                    "message": "private-test-key private content",
                                    "code": api_code,
                                }
                            },
                        )
                    ]
                )
                with (
                    self.subTest(status=status, transcription=transcription),
                    self.assertRaises(ProviderError) as error,
                ):
                    self.transcribe(harness) if transcription else self.generate(
                        harness
                    )
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn("private", str(error.exception))
                self.assertEqual(len(harness.requests), 1)
                self.assert_released(harness)

    def test_retryable_errors_retry_same_model_and_clear_partial(self):
        for status in (429, 500, 503):
            harness = OfflineHTTP(
                [
                    (
                        status,
                        {
                            "error": {
                                "code": "rate_limit_exceeded"
                                if status == 429
                                else "server_error",
                                "message": "private",
                            }
                        },
                    ),
                    [delta("ok"), completed("ok")],
                ]
            )
            chunks = []
            self.assertEqual(self.generate(harness, publish=chunks.append).text, "ok")
            self.assertEqual(chunks, ["", "ok"])
            self.assertEqual(len(harness.requests), 2)
            self.assertTrue(
                all(
                    json.loads(r.content)["model"] == "gpt-5.6-terra"
                    for r in harness.requests
                )
            )
            self.assert_released(harness)
        harness = OfflineHTTP(
            [
                [
                    delta("old"),
                    {
                        "type": "error",
                        "code": "server_error",
                        "message": "private",
                        "param": None,
                    },
                ],
                [delta("new"), completed("new")],
            ]
        )
        chunks = []
        self.assertEqual(self.generate(harness, publish=chunks.append).text, "new")
        self.assertEqual(chunks, ["old", "", "new"])

    def test_three_attempt_limit_network_failure_and_interrupted_stream(self):
        for script, code in (
            (httpx.ConnectError("private"), ProviderErrorCode.NETWORK_ERROR),
            ([delta("partial")], ProviderErrorCode.NETWORK_ERROR),
        ):
            harness = OfflineHTTP([script, script, script])
            with self.assertRaises(ProviderError) as error:
                self.generate(harness)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(len(harness.requests), 3)
            self.assert_released(harness)

    def test_empty_incomplete_and_failed_responses_are_errors(self):
        for events in (
            [completed("")],
            [
                {
                    "type": "response.incomplete",
                    "sequence_number": 1,
                    "response": {**completed("")["response"], "status": "incomplete"},
                }
            ],
            [
                {
                    "type": "response.failed",
                    "sequence_number": 1,
                    "response": {
                        **completed("")["response"],
                        "status": "failed",
                        "error": {"code": "invalid_api_key", "message": "private"},
                    },
                }
            ],
        ):
            harness = OfflineHTTP([events])
            with self.assertRaises(ProviderError):
                self.generate(harness)
            self.assertEqual(len(harness.requests), 1)
            self.assert_released(harness)
        harness = OfflineHTTP([(200, {"text": "  "})])
        with self.assertRaises(ProviderError):
            self.transcribe(harness)
        self.assert_released(harness)

    def test_deadline_cancels_waiting_stream_and_upload(self):
        for transcription in (False, True):
            harness = OfflineHTTP([None if transcription else [None]])
            with self.assertRaises(ProviderError) as error:
                self.transcribe(
                    harness, timeout=0.07
                ) if transcription else self.generate(
                    harness, request=replace(self.request, timeout=0.07)
                )
            self.assertEqual(error.exception.code, ProviderErrorCode.TIMEOUT)
            self.assert_released(harness)

    def test_cancellation_during_generation_and_transcription_then_restart(self):
        for transcription in (False, True):
            self.cancel.clear()
            harness = OfflineHTTP([None if transcription else [None]])
            timer = threading.Timer(0.06, self.cancel.set)
            timer.start()
            try:
                with self.assertRaises(ProviderError) as error:
                    self.transcribe(harness) if transcription else self.generate(
                        harness
                    )
                self.assertEqual(error.exception.code, ProviderErrorCode.CANCELLED)
                self.assert_released(harness)
            finally:
                timer.join(1)
        self.cancel.clear()
        self.assertEqual(
            self.generate(OfflineHTTP([[completed("restart")]])).text, "restart"
        )

    def test_missing_key_cancelled_and_closed_provider_create_no_client(self):
        harness = OfflineHTTP()
        with harness.install():
            provider = OpenAIProvider(Settings(_env_file=None), "gpt-5.6-terra")
            with self.assertRaises(ProviderError) as error:
                provider.generate_response(self.request, lambda _: None, self.cancel)
            self.assertEqual(error.exception.code, ProviderErrorCode.AUTH_FAILED)
            self.cancel.set()
            with self.assertRaises(ProviderError):
                self.generate(harness)
            provider.close()
            with self.assertRaises(ProviderError) as error:
                provider.generate_response(self.request, lambda _: None, self.cancel)
            self.assertEqual(error.exception.code, ProviderErrorCode.CLOSED)
        self.assertEqual(harness.clients, [])

    def test_sdk_logging_and_environment_cannot_redirect_credentials(self):
        harness = OfflineHTTP([[completed("private response")]])
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture()
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            with patch.dict(
                "os.environ",
                {
                    "OPENAI_BASE_URL": "https://untrusted.invalid",
                    "OPENAI_ORG_ID": "other-org",
                    "OPENAI_PROJECT_ID": "other-project",
                    "OPENAI_LOG": "debug",
                },
            ):
                self.generate(harness)
        finally:
            root.removeHandler(handler)
        self.assertTrue(
            str(harness.requests[0].url).startswith("https://api.openai.com/v1/")
        )
        self.assertNotIn("OpenAI-Organization", harness.requests[0].headers)
        self.assertNotIn("OpenAI-Project", harness.requests[0].headers)
        self.assertNotIn("private", "\n".join(records))
