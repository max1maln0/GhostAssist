# Configuration schema 1

Формат остаётся dotenv UTF-8. `SCHEMA_VERSION=1` — текущая схема; отсутствие поля означает legacy. `load_settings` используется службой и Dashboard: проверяет синтаксис и версию файла, валидирует значения и атомарно добавляет версию без изменения других строк. Файл без настроек не создаётся при чтении. Повторная миграция ничего не записывает. Неизвестная или дублирующаяся версия блокирует загрузку и запись, включая попытку скрыть её переменной окружения. Исправление неизвестной версии требует подходящей версии приложения или ручного восстановления файла; автоматического понижения нет.

Сначала проверяются эффективные значения. Перед миграцией дополнительно проверяются поля файла с их существующей dotenv-интерполяцией, независимо от прямых env-переопределений; они не должны скрывать некорректные данные на диске. Миграция заменяет файл через временный файл в том же каталоге с flush/fsync. На Windows DACL исходного файла применяется к пустому staging-файлу до записи секрета, а ReplaceFileW сохраняет ACL и метаданные оригинала; флаги игнорирования ошибок ACL не используются. Такой же механизм защищает сохранение Markdown-профилей. Для нового файла действуют разрешения каталога; ограничьте их заранее. На POSIX при замене копируются mode-биты, поддержка приложения по-прежнему ограничена Windows.

ReplaceFileW получает уникальное резервное имя `.ghostassist-env-<имя>-<uuid>.backup`. После успешного сохранения резервная копия удаляется. При частичном отказе переименования приложение восстанавливает оригинал из backup; если восстановление запрещено файловой системой, backup сохраняется с исходным ACL. Пока основной .env отсутствует и найден соответствующий backup, загрузка и запись блокируются. Закройте оба процесса, сохраните имеющиеся файлы и восстановите нужную резервную копию под исходным именем; не публикуйте её. После аварии staging или backup могут остаться: они исключены из Git, но не зашифрованы. Аппаратный сбой диска и отключение питания не покрываются гарантиями тестов.

Загрузка/миграция службы и Dashboard, а также read–validate–write Dashboard выполняются в одной межпроцессной транзакции. Стабильный sidecar `.env.lock` содержит один служебный байт, Windows блокирует его через msvcrt.locking; POSIX использует flock. Дополнительный RLock обеспечивает поточную и вложенную синхронизацию. Блокировка снимается ОС при завершении процесса; sidecar нельзя удалять во время работы. Ожидание загрузки ограничено 10 секундами, сохранение Dashboard прекращает ожидание через 50 мс. Обновление применяет только переданные поля к последнему файлу: независимые правки не теряются, последовательные изменения одного поля используют последнее успешное сохранение. Ручной редактор и синхронизация OneDrive не участвуют в этом протоколе — перед внешним редактированием остановите процессы.

Приоритет: явные аргументы Settings > переменные окружения > .env > defaults. Dashboard запрещает сохранять изменённое значение, если оно переопределено переменной окружения, и показывает её имя без значения. Комментарии, неизвестные ключи и значения сохраняются. Markdown-профили не мигрируются. Settings остаётся совместимым плоским API, а `.groups` возвращает неизменяемые типизированные представления: application, tasks, audio, screen, interface, memory, provider. Второй изменяемой копии настроек нет.

AI_PROVIDER пока принимает только gemini. ProviderConfig содержит идентификатор, модель, SecretStr-ключ и сетевой тайм-аут; подключение использует стандартный endpoint Gemini SDK. Собственные endpoints и другие провайдеры не подключены; дальнейшее расширение этого контракта относится к Stage 4. GeminiClient уже использует provider-группу для модели и ключа. Прикладные бюджеты, аудио, экран и UI сохраняют прежние обращения к полям Settings.

API-ключ исключён из repr Settings и ProviderConfig. Ошибки валидации не отображают входные значения в строковом представлении. Не выводите model_dump с явным извлечением SecretStr или содержимое .env в логи. История и пользовательские профили не включаются в конфигурационные отчёты.

## Инвентаризация полей

Значения ниже взяты из определений схемы, а не пользовательского файла.

| Поле .env | Тип | Default | Группа / потребители |
| --- | --- | --- | --- |
| `SCHEMA_VERSION` | `typing.Literal[1]` | `1` | Application / loader |
| `AI_PROVIDER` | `typing.Literal['gemini']` | `gemini` | AI / GeminiProvider, Dashboard |
| `GEMINI_API_KEY` | `<class 'pydantic.types.SecretStr'>` | `(пустой секрет)` | AI / GeminiProvider, Dashboard |
| `GEMINI_MODEL` | `<class 'str'>` | `gemini-3.5-flash-lite` | AI / GeminiProvider, Dashboard |
| `ACTIVE_PROFILE` | `<class 'str'>` | `example_legend.md` | Memory / SessionManager, ContextManager, Dashboard |
| `CROP_WIDTH` | `<class 'int'>` | `1280` | Screen / composition, MSS |
| `CROP_HEIGHT` | `<class 'int'>` | `720` | Screen / composition, MSS |
| `REQUEST_TIMEOUT` | `<class 'float'>` | `12` | Tasks / controller, Gemini |
| `TASK_TIMEOUT` | `<class 'float'>` | `30` | Tasks / controller, Gemini |
| `AUDIO_MAX_SECONDS` | `<class 'float'>` | `60` | Audio / controller, WASAPI |
| `AUDIO_MAX_MIB` | `<class 'int'>` | `12` | Audio / controller, WASAPI |
| `AUDIO_DEVICE` | `<class 'str'>` | `` | Audio / controller, WASAPI |
| `VAD_ENABLED` | `<class 'bool'>` | `False` | Audio / controller, WASAPI |
| `AUTO_SEND_TRANSCRIPT` | `<class 'bool'>` | `False` | Audio / controller, WASAPI |
| `VAD_MIN_SPEECH_MS` | `<class 'int'>` | `300` | Audio / controller, WASAPI |
| `VAD_SILENCE_MS` | `<class 'int'>` | `900` | Audio / controller, WASAPI |
| `VAD_MAX_SECONDS` | `<class 'int'>` | `30` | Audio / controller, WASAPI |
| `VAD_SENSITIVITY` | `<class 'int'>` | `2` | Audio / controller, WASAPI |
| `MEMORY_ENABLED` | `<class 'bool'>` | `True` | Memory / SessionManager, ContextManager, Dashboard |
| `MEMORY_DEPTH` | `<class 'int'>` | `6` | Memory / SessionManager, ContextManager, Dashboard |
| `CLOSE_TO_TRAY` | `<class 'bool'>` | `True` | UI / Dashboard, main |
| `HOTKEY_DASHBOARD` | `<class 'str'>` | `<ctrl>+<shift>+<f9>` | UI / Dashboard, main |
| `HOTKEY_SCREEN` | `<class 'str'>` | `<alt>+q` | UI / Dashboard, main |
| `HOTKEY_SNIP` | `<class 'str'>` | `<alt>+s` | UI / Dashboard, main |
| `HOTKEY_AUDIO` | `<class 'str'>` | `<alt>+r` | UI / Dashboard, main |
| `HOTKEY_MODE` | `<class 'str'>` | `<alt>+m` | UI / Dashboard, main |
| `HOTKEY_HUD` | `<class 'str'>` | `<alt>+x` | UI / Dashboard, main |
| `HOTKEY_DOCK` | `<class 'str'>` | `<alt>+<down>` | UI / Dashboard, main |
| `HOTKEY_CANCEL` | `<class 'str'>` | `<ctrl>+<shift>+<f12>` | UI / Dashboard, main |
| `HOTKEY_PAGE_UP` | `<class 'str'>` | `<alt>+<page_up>` | UI / Dashboard, main |
| `HOTKEY_PAGE_DOWN` | `<class 'str'>` | `<alt>+<page_down>` | UI / Dashboard, main |

## Привязки Stage 4.1

Provider Registry уже используется приложением. Legacy AI_PROVIDER/GEMINI_MODEL разрешаются в две отдельные runtime-привязки генерации и транскрибации. Номер схемы остаётся 1, новые dotenv-поля и UI-селекторы не добавлены. Разные привязки задаются в доверенной точке сборки; проект будущих имён и правила совместимости описаны в [PROVIDER_REGISTRY.md](PROVIDER_REGISTRY.md). Неизвестная GEMINI_MODEL сохраняется и загружается, но запрос блокируется до появления подтверждённых возможностей.
