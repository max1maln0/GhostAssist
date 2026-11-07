"""Reusable cards, labelled fields and disclosure sections."""

import customtkinter as ctk

from ui.dashboard_theme import (
    ACCENT_GREEN,
    ACCENT_HOVER,
    BORDER_COLOR,
    FONT,
    SURFACE_CARD,
    SURFACE_INNER,
    TEXT,
    TEXT_MUTED,
)


def label(parent, text, *, muted=False, size=14, bold=False):
    widget = ctk.CTkLabel(
        parent,
        text=text,
        text_color=TEXT_MUTED if muted else TEXT,
        font=("Segoe UI", size, "bold" if bold else "normal"),
        anchor="w",
        justify="left",
        height=0,
    )
    widget.bind(
        "<Configure>",
        lambda event: widget.configure(wraplength=max(120, event.width - 4)),
        add="+",
    )
    return widget


def card(parent, title, note=None):
    frame = ctk.CTkFrame(
        parent,
        fg_color=SURFACE_CARD,
        border_color=BORDER_COLOR,
        border_width=1,
        corner_radius=12,
    )
    frame.pack(fill="x", pady=(0, 16))
    label(frame, title, size=16, bold=True).pack(fill="x", padx=20, pady=(18, 8))
    if note:
        label(frame, note, muted=True, size=12).pack(fill="x", padx=20, pady=(0, 12))
    body = ctk.CTkFrame(frame, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=20, pady=(0, 18))
    return body


def button(parent, text, command, *, primary=False, width=160):
    return ctk.CTkButton(
        parent,
        text=text,
        command=command,
        height=36,
        width=width,
        corner_radius=8,
        font=FONT,
        fg_color=ACCENT_GREEN if primary else BORDER_COLOR,
        hover_color=ACCENT_HOVER if primary else "#334155",
        text_color="#021c14" if primary else TEXT,
    )


def row(parent, text, hint=None):
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    frame.pack(fill="x", pady=7)
    frame.grid_columnconfigure(0, weight=1)
    label(frame, text).grid(row=0, column=0, sticky="ew", padx=(0, 12))
    if hint:
        label(frame, hint, muted=True, size=12).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0)
        )
    return frame


def entry(app, parent, key, text, unit, hint=None):
    frame = row(parent, text + (f", {unit}" if unit else ""), hint)
    widget = ctk.CTkEntry(
        frame,
        textvariable=app.variable(key),
        width=150,
        height=36,
        fg_color=SURFACE_INNER,
        border_color=BORDER_COLOR,
        font=FONT,
    )
    widget.grid(row=0, column=1, sticky="e")
    app.form_widgets.append(widget)
    return widget


def choice(app, parent, key, text, values, hint=None, command=None):
    frame = row(parent, text, hint)
    widget = ctk.CTkOptionMenu(
        frame,
        variable=app.variable(key),
        values=list(values),
        width=220,
        height=36,
        font=FONT,
        fg_color=SURFACE_INNER,
        button_color=BORDER_COLOR,
        button_hover_color="#334155",
        dropdown_fg_color=SURFACE_CARD,
        dynamic_resizing=False,
        command=command,
    )
    widget.grid(row=0, column=1, sticky="e")
    app.form_widgets.append(widget)
    return widget


def switch(app, parent, key, text, hint=None):
    frame = row(parent, text, hint)
    widget = ctk.CTkSwitch(
        frame,
        text="",
        variable=app.variable(key),
        onvalue="true",
        offvalue="false",
        width=48,
        progress_color=ACCENT_GREEN,
    )
    widget.grid(row=0, column=1, sticky="e")
    app.form_widgets.append(widget)
    return widget


def disclosure(parent, text="Дополнительно"):
    host = ctk.CTkFrame(parent, fg_color="transparent")
    host.pack(fill="x", pady=(0, 16))
    body = ctk.CTkFrame(host, fg_color=SURFACE_INNER, corner_radius=10)
    opened = False

    def toggle():
        nonlocal opened
        opened = not opened
        if opened:
            body.pack(fill="x", pady=(8, 0))
        else:
            body.pack_forget()
        control.configure(text=("▾ " if opened else "▸ ") + text)

    control = button(host, "▸ " + text, toggle)
    control.pack(anchor="w")
    inner = ctk.CTkFrame(body, fg_color="transparent")
    inner.pack(fill="x", padx=16, pady=12)
    return inner
