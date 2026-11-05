from config.settings import profile_path


class ContextManager:
    def __init__(self, settings):
        self.settings = settings
        # Допустимые режимы: TEST или INTERVIEW
        self.mode = "TEST"
        self.legend_text = self._load_legend()

    def _load_legend(self) -> str:
        """Загрузка файла профиля/легенды из config/profiles/."""
        path = profile_path(self.settings.active_profile)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return "Опыт: Python, SQL, системный анализ, проектирование архитектуры и REST API."

    def toggle_mode(self) -> str:
        """Переключение между TEST и INTERVIEW."""
        self.mode = "INTERVIEW" if self.mode == "TEST" else "TEST"
        return self.mode

    def get_system_prompt(self, mode=None) -> str:
        """Формирует системную инструкцию со строгой разметкой Вопрос/Ответ."""
        if (mode or self.mode) == "TEST":
            return (
                "Ты — ассистент для прохождения онлайн-тестов и экзаменов.\n"
                "СТРОГИЙ ФОРМАТ ВЫВОДА:\n"
                "Вопрос: <сформулируй суть вопроса в одно предложение>\n"
                "Ответ: <буква/номер правильного варианта> — <краткое пояснение без воды>\n\n"
                "Никаких приветствий и лишнего текста. Только две строки: 'Вопрос:' и 'Ответ:'."
            )
        else:
            return (
                "Ты — ассистент на техническом собеседовании.\n"
                "СТРОГИЙ ФОРМАТ ВЫВОДА:\n"
                "Вопрос: <суть вопроса собеседника>\n"
                "Ответ:\n"
                "• <тезис 1 от первого лица>\n"
                "• <тезис 2 с технологиями/фактами>\n"
                "• <тезис 3>\n\n"
                f"Твоя легенда кандидата:\n{self.legend_text}"
            )
