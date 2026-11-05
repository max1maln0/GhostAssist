"""Bounded WASAPI capture. The calling worker owns all native resources."""

import audioop
import io
import threading
import time
import wave
from array import array
from collections import deque
from uuid import uuid4


class AudioError(RuntimeError):
    pass


class SegmentQueue:
    """Bounded FIFO with monotonically numbered capture events."""

    def __init__(self, max_bytes, max_segments=4):
        self.max_bytes = max_bytes
        self.max_segments = max_segments
        self._items = deque()
        self._bytes = 0
        self._session_id = uuid4()
        self._last_id = 0
        self._dropped = 0
        self._closed = False
        self._lock = threading.Lock()

    def put(self, segment_id, wav, silence_ms=0):
        with self._lock:
            if self._closed or segment_id <= self._last_id:
                return False
            self._last_id = segment_id
            if (
                len(self._items) >= self.max_segments
                or self._bytes + len(wav) > self.max_bytes
            ):
                self._dropped += 1
                return False
            self._items.append(((self._session_id, segment_id), wav, silence_ms))
            self._bytes += len(wav)
            return True

    def get(self):
        with self._lock:
            if not self._items:
                return None
            segment = self._items.popleft()
            self._bytes -= len(segment[1])
            return segment

    def take_overflow(self):
        with self._lock:
            count, self._dropped = self._dropped, 0
            return count

    def close(self):
        with self._lock:
            self._closed = True
            self._items.clear()
            self._bytes = 0


def _wav_bytes(pcm, channels, rate):
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav:
            wav.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
        return buffer.getvalue()


def list_loopback_devices():
    import pyaudiowpatch as audio

    try:
        with audio.PyAudio() as backend:
            return [
                item["name"] for item in backend.get_loopback_device_info_generator()
            ]
    except (OSError, RuntimeError, ValueError, KeyError):
        raise AudioError("Не удалось получить список устройств WASAPI.") from None


def record_wav(
    stop: threading.Event,
    cancel: threading.Event,
    seconds: float,
    max_bytes: int,
    device_name: str = "",
    vad_options=None,
    on_segment=None,
) -> bytes:
    import pyaudiowpatch as audio

    pcm = bytearray()
    lock = threading.Lock()
    complete = threading.Event()
    try:
        with audio.PyAudio() as backend:
            if device_name:
                devices = [
                    item
                    for item in backend.get_loopback_device_info_generator()
                    if item["name"] == device_name
                ]
                if len(devices) != 1:
                    raise AudioError(
                        "Выбранное устройство недоступно или неоднозначно."
                    )
                device = devices[0]
            else:
                device = backend.get_default_wasapi_loopback()
            channels = int(device["maxInputChannels"])
            rate = int(device["defaultSampleRate"])
            if channels < 1 or rate < 1:
                raise AudioError("Неверный формат устройства WASAPI.")
            segmenter = None
            resample_state = None
            segment_id = 0
            if vad_options:
                import webrtcvad

                from core.vad import VoiceSegmenter

                detector = webrtcvad.Vad(vad_options["sensitivity"])

                def new_segmenter():
                    return VoiceSegmenter(
                        detector,
                        min_speech_ms=vad_options["min_speech_ms"],
                        silence_ms=vad_options["silence_ms"],
                        max_seconds=min(vad_options["max_seconds"], max_bytes / 32000),
                    )

                segmenter = new_segmenter()
            frame_size = channels * 2
            limit = min(max_bytes, int(seconds * rate) * frame_size)
            limit -= limit % frame_size

            def on_audio(data, frames, timing, flags):
                nonlocal resample_state, segmenter, segment_id
                with lock:
                    if stop.is_set() or cancel.is_set() or complete.is_set():
                        return None, audio.paComplete
                    if segmenter is not None:
                        if channels == 1:
                            mono = data
                        elif channels == 2:
                            mono = audioop.tomono(data, 2, 0.5, 0.5)
                        else:
                            samples = array("h", data)
                            mono = array(
                                "h",
                                (
                                    sum(samples[i : i + channels]) // channels
                                    for i in range(0, len(samples), channels)
                                ),
                            ).tobytes()
                        mono, resample_state = audioop.ratecv(
                            mono, 2, 1, rate, 16000, resample_state
                        )
                        segmenter.feed(mono)
                        while segmenter.done:
                            if on_segment is None:
                                complete.set()
                                return None, audio.paComplete
                            segment_id += 1
                            on_segment(
                                segment_id,
                                _wav_bytes(segmenter.result(), 1, 16000),
                                segmenter.silent * 30,
                            )
                            remaining_pcm = bytes(segmenter.pending)
                            segmenter = new_segmenter()
                            segmenter.feed(remaining_pcm)
                        return None, audio.paContinue
                    remaining = limit - len(pcm)
                    pcm.extend(data[:remaining])
                    if len(pcm) >= limit:
                        complete.set()
                        return None, audio.paComplete
                return None, audio.paContinue

            if cancel.is_set():
                return b""
            stream = backend.open(
                format=audio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device["index"],
                frames_per_buffer=1024,
                stream_callback=on_audio,
            )
            try:
                deadline = time.monotonic() + seconds
                while not (stop.is_set() or cancel.is_set() or complete.is_set()):
                    if not stream.is_active():
                        if complete.is_set() or stop.is_set() or cancel.is_set():
                            break
                        raise AudioError("Устройство воспроизведения отключилось.")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        if on_segment is not None or (
                            segmenter is not None and not segmenter.active
                        ):
                            deadline = time.monotonic() + seconds
                            continue
                        break
                    stop.wait(min(remaining, 0.05))
            finally:
                stream.close()
    except AudioError:
        raise
    except (OSError, RuntimeError, ValueError, KeyError, OverflowError):
        raise AudioError("Не удалось записать звук с устройства WASAPI.") from None

    if cancel.is_set() or on_segment is not None:
        return b""
    if segmenter is not None:
        pcm = segmenter.result()
        channels = 1
        rate = 16000
    if not pcm:
        raise AudioError("Запись пуста. Включите воспроизведение и повторите.")
    return _wav_bytes(pcm, channels, rate)
