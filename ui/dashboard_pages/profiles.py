import customtkinter as ctk

from ui.dashboard_components import (
    DeferredOptionMenu,
    bridge_textbox,
    button,
    card,
    choice,
    label,
    switch,
)
from ui.dashboard_theme import BORDER_COLOR, SURFACE_INNER


def build(app, parent):
    body = card(
        parent,
        "Markdown-профили",
        "Выбор профиля подготавливает смену активного профиля. Для службы изменения применяются после сохранения и перезапуска",
    )
    app.profile_selector = DeferredOptionMenu(
        body,
        values=[app.settings.active_profile],
        command=app.load_selected_profile,
        height=36,
        width=260,
        fg_color=SURFACE_INNER,
        button_color=BORDER_COLOR,
        dynamic_resizing=False,
    )
    app.profile_selector.pack(anchor="w", pady=(0, 10))
    controls = ctk.CTkFrame(body, fg_color="transparent")
    controls.pack(fill="x", pady=(0, 10))
    button(
        controls, "Сохранить профиль", app.save_current_legend, primary=True, width=180
    ).pack(side="left", padx=(0, 8))
    button(controls, "+ Новый", app.create_new_profile, width=100).pack(side="left")
    app.profile_dirty_label = label(body, "", muted=True, size=12)
    app.profile_dirty_label.pack(fill="x", pady=(0, 8))
    app.legend_textbox = ctk.CTkTextbox(
        body,
        height=280,
        font=("Consolas", 13),
        fg_color=SURFACE_INNER,
        border_color=BORDER_COLOR,
        border_width=1,
        wrap="word",
        undo=True,
    )
    bridge_textbox(app.legend_textbox)
    app.legend_textbox.pack(fill="both", expand=True)
    app.legend_textbox.bind("<<Modified>>", app.profile_modified, add="+")
    memory = card(
        parent,
        "Память диалога",
        "История хранится только в RAM. TEST и INTERVIEW разделены; очистка начинает новую сессию",
    )
    switch(app, memory, "MEMORY_ENABLED", "Использовать память")
    choice(
        app,
        memory,
        "MEMORY_DEPTH",
        "Глубина истории",
        tuple(str(i) for i in range(1, 11)),
        "Количество завершённых взаимодействий, от 1 до 10",
    )
    app.memory_status = label(memory, "Сессия не запущена", muted=True)
    app.memory_status.pack(fill="x", pady=10)
    button(memory, "Очистить текущую сессию", app._reset_session, width=260).pack(
        anchor="w"
    )
