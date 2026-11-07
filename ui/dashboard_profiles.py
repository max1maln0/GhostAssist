"""Profile editing with explicit persistence and loss/conflict protection."""

from tkinter import messagebox, simpledialog

from config.settings import profile_path
from core.profile_store import write_profile


class ProfileActions:
    def _load_profiles_list(self):
        names = sorted(
            path.name
            for path in self.profiles_directory.glob("*.md")
            if path.is_file() and not path.is_symlink()
        )
        self.profile_selector.configure(values=names or [self.settings.active_profile])

    def profile_modified(self, event=None):
        if self.legend_textbox._textbox.edit_modified():
            self.profile_dirty = (
                self.legend_textbox.get("1.0", "end-1c") != self.profile_original
            )
            self.legend_textbox._textbox.edit_modified(False)
            self.profile_dirty_label.configure(
                text="Есть несохранённые изменения профиля"
                if self.profile_dirty
                else "Профиль сохранён"
            )

    def guard_profile(self):
        if not self.profile_dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Профиль", "Сохранить изменения профиля перед продолжением?", parent=self
        )
        if answer is False:
            self.legend_textbox.delete("1.0", "end")
            self.legend_textbox.insert("1.0", self.profile_original)
            self.legend_textbox._textbox.edit_modified(False)
            self.profile_dirty = False
            self.profile_dirty_label.configure(text="Изменения профиля отменены")
            return True
        return answer is True and self.save_current_legend()

    def load_selected_profile(self, name, save=True):
        if save and not self.guard_profile():
            self.profile_selector.set(self.loaded_profile)
            return False
        try:
            path = profile_path(name, self.profiles_directory)
            text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        except (ValueError, OSError, UnicodeError):
            self.set_error(
                "Не удалось прочитать Markdown-профиль. Проверьте имя и доступ к файлу."
            )
            self.profile_selector.set(self.loaded_profile)
            return False
        self.loaded_profile, self.profile_original, self.profile_dirty = (
            name,
            text,
            False,
        )
        self.legend_textbox.delete("1.0", "end")
        self.legend_textbox.insert("1.0", text)
        self.legend_textbox._textbox.edit_modified(False)
        self.profile_dirty_label.configure(text="Профиль загружен")
        self.profile_selector.set(name)
        if save:
            self.variable("ACTIVE_PROFILE").set(name)
        return True

    def save_current_legend(self):
        text = self.legend_textbox.get("1.0", "end-1c")
        try:
            path = profile_path(self.loaded_profile, self.profiles_directory)
            existing = path.read_text(encoding="utf-8-sig") if path.exists() else ""
            if existing != self.profile_original:
                self.set_error(
                    "Профиль изменён вне Dashboard. Сохранение отменено; скопируйте правки и загрузите файл заново."
                )
                return False
            write_profile(self.loaded_profile, text, directory=self.profiles_directory)
        except (ValueError, OSError, UnicodeError):
            self.set_error(
                "Не удалось сохранить профиль. Проверьте права доступа и имя файла."
            )
            return False
        self.profile_original, self.profile_dirty = text, False
        self.profile_dirty_label.configure(
            text="Профиль сохранён · для службы требуется перезапуск"
        )
        return True

    def create_new_profile(self):
        if not self.guard_profile():
            return
        name = simpledialog.askstring(
            "Новый профиль", "Имя Markdown-файла, например developer.md", parent=self
        )
        if not name:
            return
        try:
            path = profile_path(name, self.profiles_directory)
            # Exclusive creation prevents accidental replacement of an existing profile.
            with path.open("x", encoding="utf-8") as output:
                output.write("# Новый профиль\n")
        except (ValueError, OSError):
            self.set_error(
                "Не удалось создать профиль: проверьте имя, права доступа и отсутствие существующего файла."
            )
            return
        self._load_profiles_list()
        self.load_selected_profile(name, save=False)
        self.variable("ACTIVE_PROFILE").set(name)
