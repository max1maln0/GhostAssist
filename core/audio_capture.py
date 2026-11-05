"""Bounded WASAPI capture. The calling worker owns all native resources."""
import io
import threading
import time
import wave


class AudioError(RuntimeError):
    pass


def record_wav(stop: threading.Event, cancel: threading.Event,
               seconds: float, max_bytes: int) -> bytes:
    import pyaudiowpatch as audio

    pcm = bytearray()
    lock = threading.Lock()
    complete = threading.Event()
    try:
        with audio.PyAudio() as backend:
            device = backend.get_default_wasapi_loopback()
            channels = int(device["maxInputChannels"])
            rate = int(device["defaultSampleRate"])
            if channels < 1 or rate < 1:
                raise AudioError("Неверный формат устройства WASAPI.")
            frame_size = channels * 2
            limit = min(max_bytes, int(seconds * rate) * frame_size)
            limit -= limit % frame_size

            def on_audio(data, frames, timing, flags):
                with lock:
                    if stop.is_set() or cancel.is_set():
                        return None, audio.paComplete
                    remaining = limit - len(pcm)
                    pcm.extend(data[:remaining])
                    if len(pcm) >= limit:
                        complete.set()
                        return None, audio.paComplete
                return None, audio.paContinue

            if cancel.is_set():
                return b""
            stream = backend.open(
                format=audio.paInt16, channels=channels, rate=rate, input=True,
                input_device_index=device["index"], frames_per_buffer=1024,
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
                        break
                    stop.wait(min(remaining, 0.05))
            finally:
                stream.close()
    except AudioError:
        raise
    except Exception:
        raise AudioError("Не удалось записать звук с устройства WASAPI.") from None

    if cancel.is_set():
        return b""
    if not pcm:
        raise AudioError("Запись пуста. Включите воспроизведение и повторите.")
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav:
            wav.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
        return buffer.getvalue()
