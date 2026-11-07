import customtkinter as ctk

from ui.dashboard_components import button, card, choice, entry, label, row, switch
from ui.dashboard_theme import BORDER_COLOR, SURFACE_INNER


def build(app, parent):
    body = card(
        parent,
        "Захват системного звука",
        "Мониторинг начинается только после вашего действия. Учитывайте согласие участников разговора",
    )
    app.variable("AUDIO_DEVICE")
    device_row = row(body, "WASAPI Loopback")
    app.device_combo = ctk.CTkOptionMenu(
        device_row,
        values=["По умолчанию"],
        width=220,
        height=36,
        fg_color=SURFACE_INNER,
        button_color=BORDER_COLOR,
        dynamic_resizing=False,
        command=app.device_selected,
    )
    app.device_combo.grid(row=0, column=1, sticky="e")
    app.form_widgets.append(app.device_combo)
    app.refresh_devices_button = button(
        body, "Обновить устройства", app._refresh_devices, width=220
    )
    app.refresh_devices_button.pack(anchor="w", pady=(8, 4))
    app.device_status = label(body, "Список ещё не обновлён", muted=True, size=12)
    app.device_status.pack(fill="x")
    entry(
        app,
        body,
        "AUDIO_MAX_SECONDS",
        "Максимальная ручная запись",
        "с",
        "От 1 до 60 секунд",
    )
    entry(
        app,
        body,
        "AUDIO_MAX_MIB",
        "Лимит аудиобуфера",
        "МиБ",
        "От 1 до 12 МиБ; очередь VAD остаётся ограниченной",
    )
    vad = card(
        parent,
        "Детектор речи VAD",
        "Захват продолжает работать во время обработки сегментов; реплики обрабатываются по FIFO",
    )
    switch(app, vad, "VAD_ENABLED", "Использовать VAD при запуске записи")
    choice(
        app,
        vad,
        "VAD_SENSITIVITY",
        "Чувствительность детектора",
        ("0", "1", "2", "3"),
        "WebRTC: 0 — мягкая фильтрация, 3 — наиболее строгая",
    )
    entry(
        app,
        vad,
        "VAD_MIN_SPEECH_MS",
        "Минимальная речь",
        "мс",
        "30–3000 мс: короткие импульсы не становятся репликами",
    )
    entry(app, vad, "VAD_SILENCE_MS", "Пауза для завершения фразы", "мс", "100–5000 мс")
    entry(
        app,
        vad,
        "VAD_MAX_SECONDS",
        "Максимальный сегмент",
        "с",
        "1–60 с; также ограничен размером PCM-буфера",
    )
    switch(
        app,
        vad,
        "AUTO_SEND_TRANSCRIPT",
        "Автоматически отправлять распознанный текст",
        "При выключении текст можно проверить и отредактировать до отправки",
    )
