from ui.dashboard_components import button, card, label


def build(app, parent):
    body = card(
        parent, "◈  GhostAssist", "Экран и системная речь — в одной рабочей сессии"
    )
    app.overview_status = label(body, "Служба остановлена", size=20, bold=True)
    app.overview_status.pack(fill="x", pady=(0, 14))
    app.overview_run = button(
        body, "Запустить службу", app.toggle_assistant_process, primary=True, width=240
    )
    app.overview_run.pack(anchor="w")
    summary = card(
        parent,
        "Рабочая конфигурация",
        "Показаны параметры службы; при её остановке — сохранённые настройки",
    )
    app.overview_summary = label(summary, "")
    app.overview_summary.pack(fill="x")
    app.session_status = label(summary, "Сессия  TEST 0 · INTERVIEW 0", muted=True)
    app.session_status.pack(fill="x", pady=(12, 0))
    errors = card(parent, "Диагностика")
    app.overview_error = label(errors, "Ошибок не зарегистрировано", muted=True)
    app.overview_error.pack(fill="x")
    links = card(parent, "Быстрые действия")
    for text, page in (
        ("Настроить AI-провайдеров", "providers"),
        ("Настроить аудио и VAD", "audio"),
        ("Открыть профили и память", "profiles"),
    ):
        button(links, text, lambda page=page: app.navigate(page), width=260).pack(
            anchor="w", pady=5
        )

    button(
        links, "Переключить TEST / INTERVIEW", lambda: app.send_service(b"M"), width=280
    ).pack(anchor="w", pady=5)
