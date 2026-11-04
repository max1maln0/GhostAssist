import io
import wave
import threading
from pyaudiowpatch import PyAudio, paInt16
from google import genai
from google.genai import types
from config.settings import settings


class AudioWorker:
    def __init__(self, context_manager):
        self.ctx = context_manager
        self.client = genai.Client(
            api_key=settings.gemini_api_key.get_secret_value()
        )
        self.p = PyAudio()
        self.is_recording = False
        self.audio_frames = []
        self.record_thread = None
        self.stream = None
        self.device_info = None

    def _get_loopback_device(self):
        """Находит стандартное устройство воспроизведения Windows с флагом loopback."""
        try:
            # Получаем дефолтный WASAPI спикер/наушники
            wasapi_info = self.p.get_host_api_info_by_type(self.p.get_host_api_info_by_index(0)['type'])
            default_speakers = self.p.get_default_output_device_info()
            
            # Ищем соответствующий loopback-девайс для него
            for loopback in self.p.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    return loopback
                    
            # Резервный поиск первого попавшегося loopback
            for loopback in self.p.get_loopback_device_info_generator():
                return loopback
        except Exception as e:
            print(f"[AUDIO] Ошибка поиска WASAPI Loopback: {e}")
        return None

    def start_recording(self):
        """Запуск захвата аудиопотока в память."""
        if self.is_recording:
            return
        
        self.device_info = self._get_loopback_device()
        if not self.device_info:
            print("[AUDIO] Loopback устройство не найдено!")
            return

        self.audio_frames = []
        self.is_recording = True

        def _record():
            try:
                self.stream = self.p.open(
                    format=paInt16,
                    channels=self.device_info["maxInputChannels"],
                    rate=int(self.device_info["defaultSampleRate"]),
                    input=True,
                    input_device_index=self.device_info["index"],
                    frames_per_buffer=1024
                )
                while self.is_recording:
                    data = self.stream.read(1024, exception_on_overflow=False)
                    self.audio_frames.append(data)
            except Exception as e:
                print(f"[AUDIO] Сбой записи: {e}")
            finally:
                if self.stream:
                    self.stream.stop_stream()
                    self.stream.close()

        self.record_thread = threading.Thread(target=_record, daemon=True)
        self.record_thread.start()

    def stop_and_process(self, on_chunk_callback):
        """Останавливает запись, упаковывает WAV в RAM и стримит ответ из Gemini."""
        if not self.is_recording:
            return

        self.is_recording = False
        if self.record_thread:
            self.record_thread.join(timeout=1.0)

        if not self.audio_frames:
            on_chunk_callback("[Аудио буфер пуст]")
            return

        # Сборка WAV в RAM
        wav_buffer = io.BytesIO()
        try:
            wf = wave.open(wav_buffer, 'wb')
            wf.setnchannels(self.device_info["maxInputChannels"])
            wf.setsampwidth(self.p.get_sample_size(paInt16))
            wf.setframerate(int(self.device_info["defaultSampleRate"]))
            wf.writeframes(b''.join(self.audio_frames))
            wf.close()
            
            audio_bytes = wav_buffer.getvalue()
        finally:
            wav_buffer.close()
            self.audio_frames.clear()

        # Отправка аудио напрямую в Gemini 2.5 Flash
        try:
            audio_part = types.Part.from_bytes(
                data=audio_bytes,
                mime_type="audio/wav"
            )
            
            # Для аудио автоматически используем режим INTERVIEW с легендой
            sys_prompt = self.ctx.get_system_prompt()
            
            response_stream = self.client.models.generate_content_stream(
                model=settings.gemini_model,
                contents=[
                    audio_part,
                    "Интервьюер задал мне этот вопрос голосом. Дай краткий, точный ответ от первого лица опираясь на мою легенду."
                ],
                config=types.GenerateContentConfig(
                    system_instruction=sys_prompt,
                    temperature=0.2,
                    max_output_tokens=300
                )
            )

            accumulated = ""
            for chunk in response_stream:
                if chunk.text:
                    accumulated += chunk.text
                    on_chunk_callback(accumulated)

        except Exception as e:
            err = f"[Ошибка Audio API: {type(e).__name__}] {e}"
            print(err)
            on_chunk_callback(err)