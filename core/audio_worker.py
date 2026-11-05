"""Bounded WASAPI capture. The calling worker owns all native resources."""

import audioop
import io
import sys
import threading
import time
import wave


class AudioError(RuntimeError):
    pass


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
            if vad_options:
                import webrtcvad

                from core.vad import VoiceSegmenter

                if channels not in (1, 2):
                    raise AudioError("VAD поддерживает моно и стерео устройства.")
                segmenter = VoiceSegmenter(
                    webrtcvad.Vad(vad_options["sensitivity"]),
                    min_speech_ms=vad_options["min_speech_ms"],
                    silence_ms=vad_options["silence_ms"],
                    max_seconds=min(vad_options["max_seconds"], max_bytes / 32000),
                )
            frame_size = channels * 2
            limit = min(max_bytes, int(seconds * rate) * frame_size)
            limit -= limit % frame_size

            def on_audio(data, frames, timing, flags):
                nonlocal resample_state
                with lock:
                    if stop.is_set() or cancel.is_set() or complete.is_set():
                        return None, audio.paComplete
                    if segmenter is not None:
                        mono = (
                            audioop.tomono(data, 2, 0.5, 0.5) if channels == 2 else data
                        )
                        mono, resample_state = audioop.ratecv(
                            mono, 2, 1, rate, 16000, resample_state
                        )
                        segmenter.feed(mono)
                        if segmenter.done:
                            print(
                                f"[metrics] vad_silence_ms={segmenter.silent * 30}",
                                file=sys.stderr,
                            )
                            complete.set()
                            return None, audio.paComplete
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
                        if segmenter is not None and not segmenter.active:
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

    if cancel.is_set():
        return b""
    if segmenter is not None:
        pcm = segmenter.result()
        channels = 1
        rate = 16000
    if not pcm:
        raise AudioError("Запись пуста. Включите воспроизведение и повторите.")
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav:
            wav.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
        return buffer.getvalue()
