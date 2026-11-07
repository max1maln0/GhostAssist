import asyncio
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from config.env_file import update_env
from config.settings import Settings, profile_path, validate_hotkeys
from core.audio_queue import AudioError, SegmentQueue
from core.llm_worker import GenerationError, LLMWorker
from core.session_manager import Interaction
from core.task_state import State, TaskState
from platform_adapters.windows.audio import record_wav


class TaskTests(unittest.TestCase):
    def test_conflict_cancel_and_stale_results(self):
        controller = TaskState()
        self.assertIs(controller.state, State.IDLE)
        task = controller.begin(State.RECORDING)
        self.assertIsNone(controller.begin(State.CAPTURING))
        task.publish("partial")
        self.assertEqual(task.preview(), "partial")
        task.state = State.STREAMING
        controller.cancel()
        self.assertIs(controller.state, State.CANCELLING)
        task.publish("late")
        self.assertIsNone(task.preview())
        self.assertIsNone(controller.begin(State.PROCESSING))
        controller.finish(task)
        next_task = controller.begin(State.CAPTURING)
        controller.finish(task)
        self.assertIs(controller.active, next_task)
        self.assertIsNone(next_task.preview())


class FakeAudio:
    def __init__(self, data, stop, *, fail=False, disconnect=False):
        self.data = data
        self.stop = stop
        self.fail = fail
        self.disconnect = disconnect
        self.closed = False
        self.terminated = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.terminated = True

    def get_default_wasapi_loopback(self):
        return {"index": 1, "maxInputChannels": 2, "defaultSampleRate": 48000}

    def open(self, **kwargs):
        if self.fail:
            raise OSError("backend")
        self.callback_result = kwargs["stream_callback"](self.data, 1, None, 0)
        if not self.disconnect:
            self.stop.set()
        return self

    def is_active(self):
        return not self.disconnect

    def close(self):
        self.closed = True


class AudioTests(unittest.TestCase):
    def test_queue_overflow_and_close_stay_bounded_under_load(self):
        queue = SegmentQueue(max_bytes=100, max_segments=4)
        for segment_id in range(1, 10001):
            queue.put(segment_id, b"a" * 20)
        self.assertEqual(queue.take_overflow(), 9996)
        self.assertEqual([queue.get()[0][1] for _ in range(4)], [1, 2, 3, 4])
        self.assertIsNone(queue.get())
        queue.put(10001, b"a" * 20)
        queue.close()
        self.assertIsNone(queue.get())
        self.assertFalse(queue.put(10002, b"a" * 20))

    def capture(self, pcm=b"", limit=100, **kwargs):
        stop = threading.Event()
        backend = FakeAudio(pcm, stop, **kwargs)
        module = SimpleNamespace(
            PyAudio=lambda: backend, paInt16=8, paComplete=1, paContinue=0
        )
        self.backend = backend
        with patch.dict(sys.modules, pyaudiowpatch=module):
            return record_wav(stop, threading.Event(), 1, limit)

    def test_start_stop_and_wav(self):
        wav = self.capture(b"abcd")
        with wave.open(io.BytesIO(wav)) as audio:
            self.assertEqual(
                (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()),
                (2, 2, 48000),
            )
            self.assertEqual(audio.readframes(10), b"abcd")
        self.assertTrue(self.backend.closed and self.backend.terminated)

    def test_buffer_limit_aligns_frames(self):
        wav = self.capture(b"a" * 200, limit=11)
        self.assertEqual(self.backend.callback_result, (None, 1))
        with wave.open(io.BytesIO(wav)) as audio:
            self.assertEqual(audio.readframes(10), b"a" * 8)

    def test_empty(self):
        with self.assertRaisesRegex(AudioError, "пуста"):
            self.capture()
        self.assertTrue(self.backend.closed and self.backend.terminated)

    def test_open_failure(self):
        with self.assertRaisesRegex(AudioError, "WASAPI"):
            self.capture(fail=True)
        self.assertTrue(self.backend.terminated)

    def test_vad_capture_formats_wav(self):
        stop = threading.Event()
        backend = FakeAudio(bytes([1, 0, 1, 0]) * 48000, stop)
        module = SimpleNamespace(
            PyAudio=lambda: backend, paInt16=8, paComplete=1, paContinue=0
        )
        detector = SimpleNamespace(is_speech=lambda frame, rate: True)
        vad = SimpleNamespace(Vad=lambda sensitivity: detector)
        options = {
            "sensitivity": 2,
            "min_speech_ms": 30,
            "silence_ms": 60,
            "max_seconds": 1,
        }
        with patch.dict(sys.modules, pyaudiowpatch=module, webrtcvad=vad):
            result = record_wav(stop, threading.Event(), 2, 32000, vad_options=options)
        with wave.open(io.BytesIO(result)) as audio:
            self.assertEqual((audio.getnchannels(), audio.getframerate()), (1, 16000))
            self.assertGreater(audio.getnframes(), 0)
            self.assertLessEqual(audio.getnframes() * 2, 32000)

    def test_vad_accepts_multichannel_loopback(self):
        stop = threading.Event()
        backend = FakeAudio(bytes([1, 0]) * 6 * 48000, stop)
        backend.get_default_wasapi_loopback = lambda: {
            "index": 1,
            "maxInputChannels": 6,
            "defaultSampleRate": 48000,
        }
        module = SimpleNamespace(
            PyAudio=lambda: backend, paInt16=8, paComplete=1, paContinue=0
        )
        vad = SimpleNamespace(
            Vad=lambda _: SimpleNamespace(is_speech=lambda frame, rate: True)
        )
        options = {
            "sensitivity": 2,
            "min_speech_ms": 30,
            "silence_ms": 60,
            "max_seconds": 1,
        }
        with patch.dict(sys.modules, pyaudiowpatch=module, webrtcvad=vad):
            result = record_wav(stop, threading.Event(), 2, 32000, vad_options=options)
        with wave.open(io.BytesIO(result)) as audio:
            self.assertEqual(audio.getnchannels(), 1)
            self.assertGreater(audio.getnframes(), 0)

    def test_vad_callback_error_is_reported_and_resources_closed(self):
        stop = threading.Event()
        backend = FakeAudio(b"bad", stop)
        module = SimpleNamespace(
            PyAudio=lambda: backend, paInt16=8, paComplete=1, paContinue=0
        )
        vad = SimpleNamespace(
            Vad=lambda _: SimpleNamespace(is_speech=lambda frame, rate: True)
        )
        options = {
            "sensitivity": 2,
            "min_speech_ms": 30,
            "silence_ms": 60,
            "max_seconds": 1,
        }
        with patch.dict(sys.modules, pyaudiowpatch=module, webrtcvad=vad):
            with self.assertRaisesRegex(AudioError, "аудиопотока"):
                record_wav(stop, threading.Event(), 2, 32000, vad_options=options)
        self.assertEqual(backend.callback_result, (None, 1))
        self.assertTrue(backend.closed and backend.terminated)

    def test_device_selection_and_missing_device(self):
        stop = threading.Event()
        backend = FakeAudio(b"abcd", stop)
        backend.get_loopback_device_info_generator = lambda: iter(
            [
                {
                    "name": "Headset",
                    "index": 4,
                    "maxInputChannels": 2,
                    "defaultSampleRate": 48000,
                }
            ]
        )
        module = SimpleNamespace(
            PyAudio=lambda: backend, paInt16=8, paComplete=1, paContinue=0
        )
        with patch.dict(sys.modules, pyaudiowpatch=module):
            self.assertTrue(record_wav(stop, threading.Event(), 1, 100, "Headset"))
            stop.clear()
            with self.assertRaisesRegex(AudioError, "устройство"):
                record_wav(stop, threading.Event(), 1, 100, "Missing")

    def test_disconnect(self):
        with self.assertRaisesRegex(AudioError, "отключилось"):
            self.capture(b"abcd", disconnect=True)
        self.assertTrue(self.backend.closed and self.backend.terminated)


def chunk(text, finish=None):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(parts=[types.Part(text=text)]),
                finish_reason=finish,
            )
        ]
    )


class FakeGemini:
    def __init__(self, scripts):
        self.scripts = iter(scripts)
        self.calls = []
        self.aio = self
        self.models = self
        self.async_closed = self.sync_closed = False
        self.stream_closed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.async_closed = True

    def close(self):
        self.sync_closed = True

    async def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)
        script = next(self.scripts)

        async def stream():
            try:
                for item in script:
                    if isinstance(item, Exception):
                        raise item
                    if item is None:
                        await asyncio.sleep(100)
                    else:
                        yield item
            finally:
                self.stream_closed += 1

        return stream()


class GeminiTests(unittest.TestCase):
    def run_fake(self, fake, publish, cancel=None, **kwargs):
        settings = Settings(
            _env_file=None,
            gemini_api_key="test-key",
            request_timeout=kwargs.get("request", 1),
            task_timeout=kwargs.get("total", 2),
        )
        worker = LLMWorker(settings)
        with patch("providers.gemini_client.genai.Client", return_value=fake):
            return worker.generate(
                b"media", "audio/wav", "prompt", publish, cancel or threading.Event()
            )

    def test_real_sdk_decodes_sse_without_network(self):
        requests = []

        async def send(request):
            requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'data: {"candidates":[{"content":{"parts":[{"text":"answer"}]},'
                    b'"finishReason":"STOP"}]}\n\n'
                ),
            )

        original = genai.Client

        def client(**kwargs):
            kwargs["http_options"].async_client_args = {
                "transport": httpx.MockTransport(send)
            }
            return original(**kwargs)

        settings = Settings(_env_file=None, gemini_api_key="test-key")
        worker = LLMWorker(settings)
        with patch("providers.gemini_client.genai.Client", side_effect=client):
            self.assertEqual(
                worker.generate(
                    b"wav", "audio/wav", "prompt", lambda _: None, threading.Event()
                ),
                "answer",
            )
        self.assertEqual(len(requests), 1)
        payload = json.loads(requests[0].content)
        inline = payload["contents"][0]["parts"][0]["inlineData"]
        self.assertEqual(inline.get("mimeType", inline.get("mime_type")), "audio/wav")
        self.assertEqual(inline["data"], "d2F2")

    def test_history_is_sent_as_user_and_model_messages(self):
        worker = LLMWorker(Settings(_env_file=None, gemini_api_key="test"))
        fake = FakeGemini([[chunk("готово", "STOP")]])
        with patch("providers.gemini_client.genai.Client", return_value=fake):
            worker.generate(
                b"jpg",
                "image/jpeg",
                "prompt",
                lambda _: None,
                threading.Event(),
                history=(Interaction("прошлый вопрос", "прошлый ответ"),),
            )
        messages = fake.calls[0]["contents"]
        self.assertEqual([item.role for item in messages], ["user", "model", "user"])
        self.assertEqual(messages[0].parts[0].text, "прошлый вопрос")
        self.assertEqual(messages[1].parts[0].text, "прошлый ответ")

    def test_transcription_validates_wav_and_uses_stream(self):
        worker = LLMWorker(Settings(_env_file=None, gemini_api_key="test"))
        cancel = threading.Event()
        for invalid in (b"", b"not wav"):
            with self.assertRaises(GenerationError):
                worker.transcribe(invalid, cancel)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 320)
        fake = FakeGemini([[chunk("Распознанный текст", "STOP")]])
        with patch("providers.gemini_client.genai.Client", return_value=fake):
            self.assertEqual(
                worker.transcribe(buffer.getvalue(), cancel), "Распознанный текст"
            )
        self.assertEqual(
            fake.calls[0]["contents"][0].parts[0].inline_data.mime_type, "audio/wav"
        )

    def test_transcription_timeout_cancel_and_service_error(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(bytes(640))
        data = buffer.getvalue()
        worker = LLMWorker(
            Settings(
                _env_file=None,
                gemini_api_key="test",
                request_timeout=1,
                task_timeout=0.1,
            )
        )
        fake = FakeGemini([[None]])
        with patch("providers.gemini_client.genai.Client", return_value=fake):
            with self.assertRaisesRegex(GenerationError, "общее время"):
                worker.transcribe(data, threading.Event())
        self.assertTrue(fake.async_closed)
        cancelled = threading.Event()
        cancelled.set()
        with patch("providers.gemini_client.genai.Client") as client:
            self.assertIsNone(worker.transcribe(data, cancelled))
            client.assert_not_called()
        fake = FakeGemini([[errors.ClientError(401, {"message": "secret"})]])
        with patch("providers.gemini_client.genai.Client", return_value=fake):
            with self.assertRaises(GenerationError) as error:
                worker.transcribe(data, threading.Event())
        self.assertNotIn("secret", str(error.exception))

    def test_fallback_clears_partial_and_sends_wav(self):
        fake = FakeGemini(
            [[chunk("old"), errors.ServerError(503, {})], [chunk("new", "STOP")]]
        )
        previews = []
        self.assertEqual(self.run_fake(fake, previews.append), "new")
        self.assertEqual(previews, ["old", "", "new"])
        self.assertNotEqual(fake.calls[0]["model"], fake.calls[1]["model"])
        self.assertEqual(
            fake.calls[0]["contents"][0].parts[0].inline_data.mime_type, "audio/wav"
        )
        self.assertTrue(fake.async_closed and fake.sync_closed)

    def test_auth_is_terminal(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                fake = FakeGemini([[errors.ClientError(status, {"message": "secret"})]])
                with self.assertRaises(GenerationError) as ctx:
                    self.run_fake(fake, lambda _: None)
                self.assertNotIn("secret", str(ctx.exception))
                self.assertEqual(len(fake.calls), 1)

    def test_total_timeout_closes_pending_stream(self):
        fake = FakeGemini([[None]])
        with self.assertRaisesRegex(GenerationError, "общее время"):
            self.run_fake(fake, lambda _: None, total=0.12)
        self.assertEqual(fake.stream_closed, 1)
        self.assertTrue(fake.async_closed and fake.sync_closed)

    def test_per_model_timeout_falls_back(self):
        fake = FakeGemini([[None], [chunk("next", "STOP")]])
        self.assertEqual(self.run_fake(fake, lambda _: None, request=0.1), "next")
        self.assertEqual(fake.stream_closed, 2)

    def test_incomplete_and_empty_streams_fall_back(self):
        fake = FakeGemini([[chunk("partial")], [], [chunk("complete", "STOP")]])
        self.assertEqual(self.run_fake(fake, lambda _: None, total=3), "complete")

    def test_cancel_during_stream(self):
        cancel = threading.Event()
        fake = FakeGemini([[chunk("partial"), None]])
        self.assertIsNone(self.run_fake(fake, lambda _: cancel.set(), cancel))
        self.assertEqual(fake.stream_closed, 1)


class SettingsTests(unittest.TestCase):
    def test_failed_dotenv_replace_cleans_identifiable_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("EXISTING=one\n", encoding="utf-8")
            temporary_names = []

            def fail(source, destination):
                temporary_names.append(Path(source).name)
                raise OSError("replace failed")

            with patch("config.env_file.os.replace", side_effect=fail):
                with self.assertRaises(OSError):
                    update_env({"EXISTING": "two"}, env)
            self.assertTrue(temporary_names[0].startswith(".ghostassist-env-"))
            self.assertTrue(temporary_names[0].endswith(".tmp"))
            self.assertEqual(list(Path(tmp).iterdir()), [env])
            self.assertEqual(env.read_text(encoding="utf-8"), "EXISTING=one\n")

    def test_load_validate_and_preserve_dotenv(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {}, clear=True),
        ):
            env = Path(tmp) / ".env"
            env.write_text(
                "# header\n\nOTHER='value' # keep\nCROP_WIDTH=800 # width note\n",
                encoding="utf-8",
            )
            settings = Settings(_env_file=env)
            self.assertFalse(settings.has_api_key)
            self.assertEqual(settings.crop_width, 800)
            update_env({"CROP_WIDTH": "900", "HOTKEY_CANCEL": "<alt>+<esc>"}, env)
            self.assertIn(
                "# header\n\nOTHER='value' # keep\n", env.read_text(encoding="utf-8")
            )
            self.assertIn(" # width note", env.read_text(encoding="utf-8"))
            self.assertEqual(Settings(_env_file=env).crop_width, 900)
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, audio_max_seconds=61)

    def test_windows_shortcuts_and_legacy_cancel(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None, hotkey_cancel="<alt>+<esc>")
            self.assertEqual(settings.hotkey_cancel, "<ctrl>+<shift>+<f12>")
            validate_hotkeys(settings)
            for combo in (
                "<alt>+<esc>",
                "<alt>+<tab>",
                "<alt>+<f4>",
                "<ctrl>+<shift>+<esc>",
                "<cmd>+d",
                "<cmd>+l",
            ):
                with self.subTest(combo=combo), self.assertRaises(ValueError):
                    validate_hotkeys(settings, {"hotkey_screen": combo})

    def test_profiles_reject_traversal_and_reserved_names(self):
        for name in (
            "../private.md",
            "C:\\private.md",
            "sub/file.md",
            "\\\\host\\file.md",
            "test.md:secret",
            "NUL.md",
            "COM1.test.md",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                profile_path(name)
        self.assertEqual(profile_path("Легенда 1.md").name, "Легенда 1.md")

    def test_symlink_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.md"
            outside.touch()
            profiles = root / "profiles"
            profiles.mkdir()
            try:
                (profiles / "alias.md").symlink_to(outside)
            except OSError:
                with patch.object(Path, "is_symlink", return_value=True):
                    with self.assertRaises(ValueError):
                        profile_path("alias.md", profiles)
                return
            with self.assertRaises(ValueError):
                profile_path("alias.md", profiles)


if __name__ == "__main__":
    unittest.main()
