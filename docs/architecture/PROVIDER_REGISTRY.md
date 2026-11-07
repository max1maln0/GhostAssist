# Stage 4.1–4.2 — фактическая архитектура Provider Registry

Реализован реестр и независимая маршрутизация Gemini/OpenAI. Claude, Ollama, custom endpoints и полный графический селектор провайдеров не реализованы. Текущий формат .env — SCHEMA_VERSION=2, с миграцией схемы 1. [Настройка OpenAI](OPENAI_PROVIDER.md).

## Сборка и поток вызовов

`platform_adapters/windows/composition.py:build_dependencies` создаёт реестр,
регистрирует доверенные фабрики Gemini и OpenAI и собирает две отдельные привязки
`ProviderBinding(provider_id, model_id)`. Legacy-настройки `AI_PROVIDER` и
`GEMINI_MODEL` используются при отсутствии новых GENERATION_PROVIDER/MODEL и TRANSCRIPTION_PROVIDER/MODEL. Сама сборка, перечисление
идентификаторов и чтение каталога не создают SDK-клиент и не выполняют запросы.

GenerationRequest → BoundGeneration → проверка adapter/model capabilities →
registry.acquire → generate_response → close. TranscriptionRequest отдельно
проходит через BoundTranscription и transcribe_audio. LLMWorker получает эти две
зависимости независимо. Обратная совместимость прямой инъекции одного провайдера
в LLMWorker сохранена для тестов и существующего программного API; production
точка сборки всегда использует обе привязки реестра.

ApplicationController не выбирает модели или SDK. TaskOrchestrator остаётся
единственным механизмом пользовательских задач; UUID записи, подтверждения и
генерации сохраняется. У контроллера добавлен только общий вызов владельца
зависимостей при shutdown; статусы генерации больше не содержат имя Gemini.
Память, VAD-источник, bounded FIFO и Tk event queues не заменялись.

Пример разных моделей в доверенной точке сборки, без изменения контроллера:

```python
from core.provider_registry import ProviderBinding
from platform_adapters.windows.composition import build_dependencies

dependencies = build_dependencies(
    settings,
    generation=ProviderBinding("gemini", "gemini-3.5-flash-lite"),
    transcription=ProviderBinding("gemini", "gemini-3.1-flash-lite"),
)
```

Разные рабочие провайдеры теперь выбираются и в .env: OpenAI → Gemini и Gemini → OpenAI проверены через SDK с mock HTTP. Для программной подмены по-прежнему доступны registry=... и отдельные ProviderBinding. Пользовательские строки конфигурации не становятся
Python import paths, именами классов для eval или фабриками.

## Реестр и владение ресурсами

- ProviderRegistration содержит уникальный проверенный id, фабрику, возможности
  адаптера и неизменяемый снимок каталога моделей. Фабрика исключена из repr.
- `register`, `identifiers`, `describe` и учёт активных выдач защищены Lock.
  Повторная регистрация отклоняется; неизвестный id даёт типизированную ошибку.
- `acquire(binding)` — context manager, создающий новый экземпляр только на запрос.
  Проверяется наличие нужного метода и close. Генерация и транскрибация не обязаны
  реализовываться одним объектом; audio-only ASR не обязан поддерживать streaming.
- Созданный экземпляр принадлежит одной выдаче. В finally вызывается close,
  включая ошибку, отмену, timeout, отсутствие интерфейса и гонку shutdown/factory.
  Если фабрика падает до возврата объекта, она сама отвечает за частично созданные
  ресурсы. Фабрики доверены коду приложения и должны возвращать отдельные экземпляры.
- Реестр не хранит пул сетевых клиентов. `close()` идемпотентно запрещает новые
  выдачи, но не закрывает активный SDK из Tk-потока и не блокирует UI. Уже отменённый
  worker освобождает свою выдачу при выходе. Счётчик active показывает этот процесс.
- GeminiProvider создаёт лёгкий GeminiClient без сети. Сам google-genai Client
  создаётся внутри запроса и закрывается вместе с async stream/event loop, как до
  Stage 4.1. Затем lease закрывает оболочку GeminiProvider. SDK-клиенты не переносятся
  между разными asyncio.run и моделями/привязками.
- Ошибка close не скрывает исходную ошибку запроса. Неизвестные ошибки factory,
  вызова адаптера и close преобразуются в сообщения без текста исключения SDK.

Типизированные новые коды: UNKNOWN_PROVIDER, INVALID_PROVIDER, UNKNOWN_MODEL,
UNKNOWN_CAPABILITY, CLOSED. Существующие AUTH_FAILED, RATE_LIMIT, TIMEOUT,
NETWORK_ERROR, MODEL_UNAVAILABLE, UNSUPPORTED_CAPABILITY и CANCELLED сохранены.

## Возможности моделей и ограничения

ProviderCapabilities описывает адаптер. Отдельный ModelCapabilities описывает
конкретный model_id: text_input, image_input, audio_input, transcription, streaming,
mime_types, max_input_bytes, max_request_bytes и источник сведений.
Возможности по умолчанию неизвестны (`None`), а не подтверждены. False означает
явную несовместимость. Неизвестная модель или необходимая возможность блокирует
запрос до создания провайдера и отправки содержимого.

Для генерации требуются текстовые инструкции/история и streaming плюс выбранная
модальность. Для транскрибации требуются audio_input, transcription и формат;
text_input/streaming для самостоятельного ASR не обязательны. Проверяются одновременно
возможности зарегистрированного адаптера и выбранной модели.

В `providers/gemini_catalog.py` перечислены точные IDs текущей очереди:
gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-3.6-flash. Возможности не
выводятся из префикса `gemini`. Поддерживаемый приложением subset форматов:
text/plain UTF-8, image/jpeg и audio/wav. Это не полный перечень возможностей API.
Основания: [3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite),
[3.1 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite),
[3.6 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash).

Для Gemini действует консервативная локальная политика inline request ≤20 000 000
байт: проверяются исходный payload, оценка JSON/base64, промпт, инструкции, история
и запас на упаковку. Это защита до SDK, а не измерение токенов или гарантия точного
серверного лимита. Лимит основан на [inline image requests](https://ai.google.dev/gemini-api/docs/image-understanding).
None у размера означает отсутствие подтверждённого модельного лимита в метаданных,
а не утверждение о безграничном размере; текущий Gemini-каталог задаёт оба лимита явно.
WAV дополнительно проверяется существующим GeminiClient до сети.

Неизвестные пользовательские GEMINI_MODEL продолжают загружаться и сохраняться,
но их вызов блокируется до появления проверенных метаданных. Это намеренная безопасная
диагностика, а не автоматическое объявление поддержки всех моделей Gemini.
Встроенные три модели и старые настройки не требуют миграции или правки файла.

## Отмена, бюджеты, fallback и безопасность

Бюджет по умолчанию берётся из TASK_TIMEOUT. Подготовка и создание фабрики входят
в него, SDK получает оставшееся время. Просроченный запрос не создаёт клиент;
публикация и итог после дедлайна/отмены/закрытия реестра отсекаются даже при
некорректном адаптере, который возвращает поздний результат.
Принудительное прерывание зависшего нативного вызова не добавлено: действует
кооперативная отмена и прежний watchdog управляемой службы.

Сохранена прежняя очередь fallback внутри Gemini, backoff и очистка partial text.
Каждый кандидат отдельно проверяется на формат, размер и необходимые возможности;
неизвестные/несовместимые резервные модели пропускаются до SDK. Начальная выбранная
модель должна пройти проверку. OpenAI имеет bounded retry той же модели, без автоматического перехода на Gemini. Межпровайдерного fallback нет: другой провайдер не
получает содержимое автоматически. Авторизация принадлежит фабрике конкретной
регистрации; реестр не переносит заголовки или ключи между ними.

## Конфигурационные привязки Stage 4.2

| Поле | Назначение / legacy fallback |
| --- | --- |
| GENERATION_PROVIDER | gemini или openai; AI_PROVIDER при отсутствии |
| GENERATION_MODEL | Модель ответа; GEMINI_MODEL для Gemini, gpt-5.6-terra для OpenAI |
| TRANSCRIPTION_PROVIDER | gemini или openai; AI_PROVIDER при отсутствии |
| TRANSCRIPTION_MODEL | Модель речи; GEMINI_MODEL для Gemini, gpt-transcribe для OpenAI |
| OPENAI_API_KEY | Отдельный SecretStr-ключ OpenAI; GEMINI_API_KEY сохраняется |

Схема 2 предотвращает игнорирование этих полей старым кодом. Миграция 0/1 → 2
сохраняет значения и комментарии, а при отсутствии новых полей остаётся Gemini.
Группы Settings.groups.generation/transcription отражают разрешённые привязки;
.provider сохраняет Gemini-совместимость, .openai содержит отдельную авторизацию.
Служба требует ключи выбранных провайдеров. Полный UI выбора остаётся Stage 4.6;
смена .env применяется после контролируемой остановки и нового запуска процессов.

OpenAIProvider использует request-scoped AsyncOpenAI и HTTP-клиент с закрытием в
worker event loop. Генерация поддерживает Responses JPEG/text и streaming, ASR —
готовый WAV. Точные IDs, политика размеров и особенности финальных событий описаны
в [OPENAI_PROVIDER.md](OPENAI_PROVIDER.md). Ключи не переносятся между фабриками.

## Проверки Stage 4.1

Полная регрессия: 173 теста на Python 3.10.22, 3.11.18, 3.12.10 на Windows 11;
Ruff check, format --check и compileall успешны. Покрыты реестр, две привязки,
capabilities, lifecycle/cancel/deadline, SDK streaming и WAV, обе смешанные связки,
VAD FIFO, история, shutdown и миграция. OpenAI stress: 100 последовательных и 12
конкурентных запросов с проверкой закрытия всех клиентов. CI — Windows tests рабочей ветки.

```powershell
python -m unittest discover -s tests -v
python -m compileall -q config core platform_adapters providers ui tests main.py
python -m ruff check config core platform_adapters providers ui tests main.py
python -m ruff format --check config core platform_adapters providers ui tests main.py
```

Автотесты не обращаются к сети, платному API или реальным устройствам. Качество
транскрибации, доступность API для ключа пользователя, физический ввод, Windows 10,
отключение устройств и смешанный DPI остаются live/manual-проверками по
[чек-листу Stage 3](../audits/STAGE_3_FINAL_AUDIT.md). Эти проверки не объявлены пройденными.
