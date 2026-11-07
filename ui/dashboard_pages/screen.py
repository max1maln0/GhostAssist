from ui.dashboard_components import button, card, entry, label


def build(app, parent):
    body = card(
        parent,
        "Снимок экрана",
        "Полный снимок основного монитора уменьшается с сохранением пропорций. Выделенная область сохраняет исходное разрешение",
    )
    entry(app, body, "CROP_WIDTH", "Максимальная ширина", "px", "160–7680 пикселей")
    entry(app, body, "CROP_HEIGHT", "Максимальная высота", "px", "120–4320 пикселей")
    label(
        body,
        "Выделение ограничено 40 млн пикселей. Координаты и DPI обрабатывает существующий Windows-адаптер.",
        muted=True,
        size=12,
    ).pack(fill="x", pady=(12, 0))
    hud = card(
        parent,
        "Плавающий HUD",
        "Команды работают только для службы, запущенной этим Dashboard. Положение и видимость меняются в текущем сеансе",
    )
    app.hud_status = label(hud, "Служба остановлена", muted=True)
    app.hud_status.pack(fill="x", pady=(0, 10))
    app.hud_buttons = [
        button(hud, "Показать / скрыть HUD", lambda: app.send_service(b"H"), width=260),
        button(
            hud, "Переместить вверх / вниз", lambda: app.send_service(b"D"), width=260
        ),
    ]
    for widget in app.hud_buttons:
        widget.pack(anchor="w", pady=5)
    app.screen_hotkeys = label(hud, "", muted=True, size=12)
    app.screen_hotkeys.pack(fill="x", pady=(12, 0))
