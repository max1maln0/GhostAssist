import platform
import sys

from ui.dashboard_components import button, card, label, switch
from ui.dashboard_theme import VERSION


def build(app, parent):
    body = card(parent, "Поведение приложения")
    switch(
        app,
        body,
        "CLOSE_TO_TRAY",
        "Кнопка × сворачивает в трей",
        "Если выключено, закрытие окна завершает GhostAssist и управляемую службу",
    )
    for title, command in (
        ("Скрыть Dashboard в трей", app.hide_dashboard),
        ("Перезапустить службу", app.apply_and_restart),
        ("Отменить текущую задачу", lambda: app.send_service(b"C")),
        ("Завершить GhostAssist", app.exit_app),
    ):
        button(body, title, command, width=270).pack(anchor="w", pady=6)
    diagnostic = card(parent, "Система и диагностика")
    label(diagnostic, VERSION, bold=True).pack(fill="x")
    label(
        diagnostic,
        f"Python {sys.version.split()[0]} · {platform.system()} {platform.release()}",
        muted=True,
    ).pack(fill="x", pady=8)
    app.configuration_status = label(diagnostic, "Схема конфигурации 2", muted=True)
    app.configuration_status.pack(fill="x")
    app.system_error = label(diagnostic, "Ошибок не зарегистрировано", muted=True)
    app.system_error.pack(fill="x", pady=12)
    button(diagnostic, "Сбросить сообщение об ошибке", app.clear_error, width=290).pack(
        anchor="w"
    )
    label(
        diagnostic,
        "Настройки записываются атомарно с сохранением Windows ACL. Ключи и содержимое разговоров не включаются в диагностику. Автозапуск не реализован.",
        muted=True,
        size=12,
    ).pack(fill="x", pady=(14, 0))
