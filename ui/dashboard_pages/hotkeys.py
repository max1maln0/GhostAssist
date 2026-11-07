import customtkinter as ctk

from config.settings import HOTKEY_LABELS, format_hotkey
from ui.dashboard_components import button, card, label
from ui.dashboard_theme import BORDER_COLOR, FONT, SURFACE_INNER
from ui.hotkey_capture import Tooltip


def build(app, parent):
    body = card(
        parent,
        "Глобальные сочетания",
        "Нажмите сочетание, затем удерживайте клавиши. Escape отменяет ввод; повторное нажатие кнопки тоже отменяет захват",
    )
    for name, title in HOTKEY_LABELS.items():
        key = name.upper()
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=7)
        row.grid_columnconfigure(0, weight=1)
        label(row, title, size=13).grid(row=0, column=0, sticky="ew", padx=(0, 10))
        capture = ctk.CTkButton(
            row,
            text=format_hotkey(getattr(app.settings, name)),
            command=lambda key=key: app._start_hotkey_capture(key),
            width=230,
            height=36,
            border_width=1,
            border_color=BORDER_COLOR,
            fg_color=SURFACE_INNER,
            hover_color=BORDER_COLOR,
            font=FONT,
        )
        capture.grid(row=0, column=1)
        reset = button(row, "↺", lambda key=key: app._reset_hotkey(key), width=36)
        reset.grid(row=0, column=2, padx=(6, 0))
        Tooltip(reset, "Вернуть комбинацию по умолчанию")
        Tooltip(
            capture,
            "Изменить сочетание. После сохранения параметры службы применяются явным перезапуском",
        )
        app.hotkey_buttons[key] = capture
    label(
        body,
        "Mouse 4 — полный снимок; Mouse 5 — запись звука. Системные сочетания Windows и дублирующиеся назначения запрещены.",
        muted=True,
        size=12,
    ).pack(fill="x", pady=(14, 0))
    app.hotkey_runtime_status = label(body, "", muted=True, size=12)
    app.hotkey_runtime_status.pack(fill="x", pady=(10, 0))
