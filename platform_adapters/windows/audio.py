"""Bounded WASAPI capture. The calling worker owns all native resources."""

import audioop
import io
import threading
import time
import wave
from array import array

from core.audio_queue import AudioError


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


class WindowsAudioCapture:
    def list_devices(self):
        return list_loopback_devices()

    def record(self, stop, cancel, seconds, max_bytes, device_name="", **options):
        return record_wav(stop, cancel, seconds, max_bytes, device_name, **options)


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
    callback_error = threading.Event()
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

            def process_audio(data, frames, timing, flags):
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

            def on_audio(data, frames, timing, flags):
                try:
                    return process_audio(data, frames, timing, flags)
                except Exception:  # noqa: BLE001 - propagate callback failures safely
                    callback_error.set()
                    complete.set()
                    return None, audio.paComplete

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
            if callback_error.is_set() and not cancel.is_set():
                raise AudioError("Ошибка обработки аудиопотока устройства.")
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
