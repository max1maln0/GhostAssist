import threading
from pynput import mouse, keyboard
from core.context_manager import ContextManager
from core.llm_worker import LLMWorker
from core.audio_worker import AudioWorker
from ui.stealth_hud import StealthHUD


class AssistantController:
    def __init__(self):
        self.ctx = ContextManager()
        self.vision_worker = LLMWorker(self.ctx)
        self.audio_worker = AudioWorker(self.ctx)
        self.hud = StealthHUD()
        self.is_busy = False

    def trigger_screen_solve(self):
        if self.is_busy or self.audio_worker.is_recording:
            return
        self.is_busy = True
        self.hud.update_text("Считываю экран...", status=f"◐ [{self.ctx.mode}] СКРИН...")

        def task():
            try:
                self.vision_worker.analyze_screen_stream(
                    on_chunk_callback=lambda txt: self.hud.update_text(
                        txt, status=f"● [{self.ctx.mode}] ГОТОВО"
                    )
                )
            finally:
                self.is_busy = False

        threading.Thread(target=task, daemon=True).start()

    def toggle_audio_listen(self):
        """Переключатель записи аудио: клик — запись, повторный клик — ответ."""
        if self.is_busy:
            return

        if not self.audio_worker.is_recording:
            self.audio_worker.start_recording()
            self.hud.update_text("Слушаю собеседника...", status="🔴 [СЛУШАЮ СИСТЕМУ]")
        else:
            self.is_busy = True
            self.hud.update_text("Обрабатываю аудио вопрос...", status="◐ [ДУМАЮ...]")

            def task():
                try:
                    self.audio_worker.stop_and_process(
                        on_chunk_callback=lambda txt: self.hud.update_text(
                            txt, status=f"● [{self.ctx.mode}] ГОТОВО"
                        )
                    )
                finally:
                    self.is_busy = False

            threading.Thread(target=task, daemon=True).start()

    def switch_mode(self):
        new_mode = self.ctx.toggle_mode()
        self.hud.update_text(f"Профиль: {new_mode}", status=f"● [{new_mode}] READY")

    def start_listeners(self):
        # Боковые кнопки мыши
        def on_click(x, y, button, pressed):
            if not pressed:
                return
            if button == mouse.Button.x1:      # Mouse4: Скриншот
                self.trigger_screen_solve()
            elif button == mouse.Button.x2:    # Mouse5: Вкл/Выкл микрофон (Loopback)
                self.toggle_audio_listen()

        mouse_listener = mouse.Listener(on_click=on_click)
        mouse_listener.daemon = True
        mouse_listener.start()

        # Клавиатурные хоткеи
        hotkeys = keyboard.GlobalHotKeys({
            '<alt>+q': self.trigger_screen_solve,
            '<alt>+r': self.toggle_audio_listen,       # Alt + R: Аудио перехват
            '<alt>+m': self.switch_mode,                # Alt + M: Смена TEST <-> INTERVIEW
            '<alt>+x': self.hud.toggle_visibility       # Alt + X: Скрыть/Показать HUD
        })
        hotkeys.daemon = True
        hotkeys.start()

    def run(self):
        self.start_listeners()
        self.hud.run()


if __name__ == "__main__":
    app = AssistantController()
    app.run()