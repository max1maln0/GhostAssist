"""Tk adapter for transcript review; callbacks run in the Tk thread."""

import tkinter as tk


class TranscriptReview:
    def __init__(self, parent, text, on_submit, on_cancel):
        self.on_submit = on_submit
        self.root = tk.Toplevel(parent)
        self.root.title("Проверка распознанной речи")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#080e1a")
        self.editor = tk.Text(
            self.root,
            width=65,
            height=8,
            wrap="word",
            bg="#080e1a",
            fg="#cbd5e1",
            insertbackground="white",
        )
        self.editor.pack(fill="both", expand=True, padx=12, pady=12)
        self.editor.insert("1.0", text)
        controls = tk.Frame(self.root, bg="#080e1a")
        controls.pack(pady=(0, 12))
        tk.Button(controls, text="Отправить", command=self.submit).pack(
            side="left", padx=5
        )
        tk.Button(controls, text="Отменить", command=on_cancel).pack(
            side="left", padx=5
        )
        self.root.protocol("WM_DELETE_WINDOW", on_cancel)

    def submit(self):
        question = self.editor.get("1.0", "end-1c").strip()
        if question:
            self.on_submit(question)

    def after(self, delay, callback):
        return self.root.after(delay, callback)

    def destroy(self):
        self.root.destroy()
