# Stage 4.2.1 Hotfix — Performance & Shutdown

Локальная проверка: 2026-11-07, Windows 11, встроенный PowerShell VS Code, Python 3.12.10. Ветка feat/stage4-multi-provider-ai, main не изменяется.

## Причины и исправления

| Приоритет | Подтверждённый дефект | Исправление |
|---|---|---|
| P0 | shutdown(wait=False) возвращается, но interpreter ждёт зависший executor; main продолжает опрос после deadline | Ранний ProcessGuard, собственный Job Object, ограниченное ожидание UI |
| P1 | Семь страниц строятся до первого кадра | Overview сразу, остальные лениво и однократно |
| P1 | CTkOptionMenu вызывает update_idletasks при рисовании, повторяя layout | Локальный DeferredOptionMenu, кэш геометрии footer/summary |
| P2 | Таймер ScrollbarBridge переживает виджет | close отменяет принадлежащий таймер |
| P2 | Выход во время регистрации hook теряется | Отложенный повтор exit_requested |

## Владение процессами

Отмена задачи инвалидирует результат и останавливает источник, сохраняя приложение работающим. Зависший нативный поток нельзя безопасно прервать: ресурс может оставаться занят до перезапуска службы.

Полный выход Dashboard отменяет фоновые операции, посылает Q собственной службе и ждёт до четырёх секунд. ProcessGuard включён до тяжёлых импортов обеих консольных точек входа. Наблюдатель создаётся до назначения владельца в анонимный Windows Job Object; получает ограниченный список наследуемых дескрипторов job/owner и приватный control pipe. Он не ищет процессы по имени или произвольному PID. На начало выхода/Ctrl+C даётся шесть секунд; зависшая группа завершается с кодом 124. Нормальный выход не ждёт бюджет. Закрытие job при исчезновении владельца убирает забытых потомков. Тест подтверждает сохранность постороннего Python-процесса. os._exit и вмешательство в executor internals не используются.

X при CLOSE_TO_TRAY=true скрывает окно с пояснением при первом скрытии; приложение продолжает работать. При false выполняется полный выход. Библиотечный ApplicationController не создаёт наблюдателей и оставляет владение процессом вызывающей стороне.

## Реальный VS Code terminal

Baseline f16f576 отдельно воспроизведён: после shutdown deadline prompt не возвращается; Ctrl+C выдаёт KeyboardInterrupt, но executor остаётся жив. Остановлен только проверенный QA-процесс baseline.

Исправленная версия запускалась в одном интегрированном PowerShell (PID 25820). Использованы real UI/hooks/tray/main lifecycle, MockProvider и fake WAV/VAD, без ключей, реального захвата и платных запросов. После каждого случая выполнено выражение `42` в том же терминале: результат появился, shell принял следующую команду. Проверены конкретные PID owner/guardian/managed service — оставшихся процессов нет.

| Сценарий | Фактическое время | Результат |
|---|---:|---|
| Полный выход из Dashboard при зависшем provider | 5,208 с от нажатия | Prompt вернулся, остаток 0 |
| X, CLOSE_TO_TRAY=false, fake запись | 1,278 с от нажатия | Prompt вернулся, остаток 0 |
| Ctrl+C standalone с зависшим provider | 6,314 с до исчезновения owner/guardian | Prompt вернулся, команда выполнена |
| Автовыход Dashboard с VAD и managed service | 7,127 с, включая запуск и auto-exit 4 с | Код 0, остаток 0 |

Process-тесты дополнительно проверяют отмену генерации, записи, активного VAD и WAITING review, зависший executor, реальный service UI и Dashboard с некооперативной службой. Fake VAD публикует сегмент с ID в FIFO; controller probe проверяет активный источник и WAITING перед отменой review.

## Измерения производительности

`python tools/benchmark_dashboard.py --integrations`: настоящий Tk, hooks/tray, изолированные настройки. Один локальный проход, без гарантии универсальной скорости. Метрики не включают секреты или тексты.

| Метрика | До | После, мс |
|---|---:|---:|
| Импорты | 997,059 | 895,424 |
| Tk | 275,259 | 212,955 |
| Настройки | 34,663 | 11,228 |
| Построение окна до первого кадра, после импортов | 7566,811 | 1084,786 |
| Импорты + построение до первого кадра | 8563,870 | 1980,210 |
| Страниц при запуске | 7 | 1 |
| Hooks | отдельно не измерены | 99,952 |
| Готовность трея | отдельно не измерена | 14,224 |
| 100 переходов median | 24,617 | 24,104 |
| 100 переходов p95 | 132,050 | 28,071 |
| 100 переходов max | 293,198 | 33,116 |

Первое создание отложенных страниц: Providers 136,055; Audio 125,433; Screen 35,555; Hotkeys 116,160; Profiles 285,696; System 40,752 мс. Первое открытие учтено отдельно от 100 прогретых переходов. Guardian bootstrap не включён во время импортов. Черновики, предупреждения и прокрутка сохраняются в экземплярах страниц; тест проверяет 100 переходов без пересоздания.

## Команды

```powershell
python -m unittest discover -s tests -v
python -m ruff check config core platform_adapters providers ui tests tools main.py
python -m ruff format --check config core platform_adapters providers ui tests tools main.py
python -m compileall -q config core platform_adapters providers ui tests tools main.py
python tools/benchmark_dashboard.py --integrations
python tools/offline_shutdown_probe.py dashboard --scenario hang
python tools/offline_shutdown_probe.py dashboard --scenario record
python tools/offline_shutdown_probe.py service --scenario hang
python tools/offline_shutdown_probe.py dashboard --scenario vad --auto-exit 4
```

В первых двух probe выбрать полный выход или X; в service нажать Ctrl+C. После каждого выполнить команду в том же терминале. `--tray-close` явно включает сворачивание. Probe использует временные настройки и fake-зависимости, не предназначен для обычного использования.

## Оставшиеся ручные проверки

- CTk 100/125/150% и узкое окно проверяются GUI-тестами. Системный DPI и перенос между физическими мониторами проверить отдельно.
- На реальном WASAPI включить запись/VAD, отключить USB/HDMI, проверить сообщение, выйти и проверить prompt и отсутствие собственных процессов. Конкретный драйверный deadlock не воспроизводился.
- Live Gemini/OpenAI не вызывались; OpenAI проверяется HTTPX MockTransport. Аппаратная приёмка Windows 10 не заявляется.
- Принудительное завершение не гарантирует finally зависшего процесса. Настройки используют существующее атомарное хранилище; история остаётся RAM-only.
