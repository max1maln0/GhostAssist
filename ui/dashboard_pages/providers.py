import customtkinter as ctk

from ui.dashboard_components import button, card, choice, disclosure, entry, label
from ui.dashboard_settings import compatible_models
from ui.dashboard_theme import BORDER_COLOR, FONT, SURFACE_INNER


def build(app, parent):
    app.provider_controls = {}
    for purpose, title in (
        ("generation", "Генерация ответа"),
        ("transcription", "Распознавание речи"),
    ):
        body = card(
            parent, title, "Провайдер и модель выбираются независимо для каждой задачи"
        )
        key = purpose.upper()
        provider = app.variable(key + "_PROVIDER").get()
        choice(
            app,
            body,
            key + "_PROVIDER",
            "Провайдер",
            ("gemini", "openai"),
            command=lambda value, purpose=purpose: app.provider_changed(purpose, value),
        )
        model = choice(
            app,
            body,
            key + "_MODEL",
            "Совместимая модель",
            compatible_models(provider, purpose),
        )
        info = label(body, "", muted=True, size=12)
        info.pack(fill="x", pady=(8, 0))
        app.provider_controls[purpose] = (model, info)
    for provider, title in (("gemini", "Gemini API"), ("openai", "OpenAI API")):
        body = card(
            parent,
            title,
            "Сохранённый ключ не подставляется в поле. Введите новый для замены",
        )
        status = label(body, "Ключ отсутствует", muted=True)
        status.pack(fill="x", pady=(0, 8))
        key_entry = ctk.CTkEntry(
            body,
            placeholder_text="Новый API-ключ",
            show="•",
            height=36,
            fg_color=SURFACE_INNER,
            border_color=BORDER_COLOR,
            font=FONT,
        )
        key_entry.pack(fill="x", pady=(0, 10))
        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x")
        button(
            actions,
            "Сохранить ключ",
            lambda provider=provider: app.save_key(provider),
            primary=True,
            width=150,
        ).pack(side="left", padx=(0, 8))
        button(
            actions,
            "Удалить",
            lambda provider=provider: app.delete_key(provider),
            width=90,
        ).pack(side="left")
        probe = button(
            body,
            "Проверить доступ к моделям",
            lambda provider=provider: app.probe_access(provider),
            width=250,
        )
        probe.pack(anchor="w", pady=(12, 0))
        app.key_controls[provider] = (key_entry, status, probe)
    more = disclosure(parent)
    entry(
        app,
        more,
        "REQUEST_TIMEOUT",
        "Ожидание одной попытки",
        "с",
        "Ограничивает сетевую попытку вместе с чтением потока: 0,1–60 с",
    )
    entry(
        app,
        more,
        "TASK_TIMEOUT",
        "Общий бюджет обработки",
        "с",
        "Включает повторные попытки и паузы: 0,1–120 с",
    )
    choice(
        app,
        more,
        "GEMINI_MODEL",
        "Наследуемая модель Gemini",
        compatible_models("gemini", "generation"),
        "Используется для старых конфигураций без явной привязки модели",
    )
    label(
        more,
        "Параметры temperature и reasoning управляются текущими адаптерами и не настраиваются здесь. Проверка доступа читает только метаданные моделей: она не отправляет профиль, разговор или снимок и не подтверждает качество генерации либо квоту.",
        muted=True,
        size=12,
    ).pack(fill="x", pady=12)
