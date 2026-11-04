import threading
from pynput import mouse, keyboard
from core.context_manager import ContextManager
from core.llm_worker import LLMWorker
from ui.stealth_hud import StealthHUD

class AssistantController:
    def __init__(self):
        self.ctx = ContextManager()
        self.worker = LLMWorker(self.ctx)
        self.hud = StealthHUD()
        self.is_busy = False

    def trigger_screen_solve(self):
        if self.is_busy:
            return
        self.is_busy = True
        self.hud.update_text("Считываю экран...", status=f"◐ [{self.ctx.mode}] ЗАПРОС...")

        def task():
            try:
                def handle_chunk(txt):
                    self.hud.update_text(txt, status=f"● [{self.ctx.mode}] ГОТОВО")

                self.worker.analyze_screen_stream(on_chunk_callback=handle_chunk)
            finally:
                self.is_busy = False

        threading.Thread(target=task, daemon=True).start()

    def switch_mode(self):
        new_mode = self.ctx.toggle_mode()
        self.hud.update_text(f"Переключен профиль: {new_mode}", status=f"● [{new_mode}] READY")

    def start_listeners(self):
        # 1. Слушаем боковые кнопки мыши (Mouse4 = x1, Mouse5 = x2)
        def on_click(x, y, button, pressed):
            if not pressed:
                return
            if button == mouse.Button.x1:      # Боковая кнопка Назад (Mouse4) -> Скриншот и ответ
                self.trigger_screen_solve()
            elif button == mouse.Button.x2:    # Боковая кнопка Вперед (Mouse5) -> Скрыть/Показать HUD
                self.hud.toggle_visibility()

        mouse_listener = mouse.Listener(on_click=on_click)
        mouse_listener.daemon = True
        mouse_listener.start()

        # 2. Дублирующие хоткеи на клавиатуре
        hotkeys = keyboard.GlobalHotKeys({
            '<alt>+q': self.trigger_screen_solve,        # Захват экрана (если нет боковых кнопок)
            '<alt>+m': self.switch_mode,                 # Переключение: ТЕСТ <-> СОБЕС (Легенда)
            '<alt>+x': self.hud.toggle_visibility        # Panic key: мгновенно сделать прозрачным
        })
        hotkeys.daemon = True
        hotkeys.start()

    def run(self):
        self.start_listeners()
        self.hud.run()

if __name__ == "__main__":
    app = AssistantController()
    app.run()