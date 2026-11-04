import sys
import threading
from pynput import mouse, keyboard

from config.settings import settings
from core.context_manager import ContextManager
from core.llm_worker import LLMWorker
from core.audio_worker import AudioWorker
from ui.stealth_hud import StealthHUD
from ui.snipping_tool import SnippingOverlay


class GhostAssistApp:
    def __init__(self):
        print("[INIT] Запуск инициализации GhostAssist...")
        
        self.context_manager = ContextManager()
        self.hud = StealthHUD()
        self.llm_worker = LLMWorker(self.context_manager)
        self.audio_worker = AudioWorker(self.context_manager)
        
        self.is_busy = False
        print("[INIT] Все модули успешно загружены.")

    def trigger_fullscreen_solve(self):
        """Мгновенный снимок всего основного монитора и решение."""
        if self.is_busy:
            return
        
        self.is_busy = True
        mode_label = self.context_manager.mode
        self.hud.update_text(status=f"◐ [{mode_label}] СКАН ЭКРАНА...")

        def _worker():
            try:
                self.llm_worker.analyze_screen_stream(
                    on_chunk_callback=lambda chunk: self.hud.update_text(
                        text=chunk, 
                        status=f"● [{mode_label}] ОТВЕТ"
                    ),
                    bbox=None
                )
            except Exception as e:
                self.hud.update_text(text=f"[Ошибка]: {e}", status="● [ОШИБКА]")
            finally:
                self.is_busy = False

        threading.Thread(target=_worker, daemon=True).start()

    def trigger_snip_solve(self):
        """Вызов полупрозрачного оверлея для выделения области мышью."""
        if self.is_busy:
            return

        self.hud.root.after(0, lambda: SnippingOverlay(self._on_snip_selected))

    def _on_snip_selected(self, bbox: dict):
        """Коллбэк после выделения области мышью."""
        self.is_busy = True
        mode_label = self.context_manager.mode
        self.hud.update_text(status=f"◐ [{mode_label}] АНАЛИЗ ОБЛАСТИ...")

        def _worker():
            try:
                self.llm_worker.analyze_screen_stream(
                    on_chunk_callback=lambda chunk: self.hud.update_text(
                        text=chunk, 
                        status=f"● [{mode_label}] ОТВЕТ"
                    ),
                    bbox=bbox
                )
            except Exception as e:
                self.hud.update_text(text=f"[Ошибка]: {e}", status="● [ОШИБКА]")
            finally:
                self.is_busy = False

        threading.Thread(target=_worker, daemon=True).start()

    def toggle_audio_listen(self):
        """Переключение циклической записи системного звука (Loopback)."""
        if not self.audio_worker.is_recording:
            self.hud.update_text(status="🔴 [СЛУШАЮ СИСТЕМУ]")
            self.audio_worker.start_recording()
        else:
            self.hud.update_text(status="◐ [ДУМАЮ...]")
            
            def _worker():
                try:
                    self.audio_worker.stop_and_analyze(
                        on_chunk_callback=lambda chunk: self.hud.update_text(
                            text=chunk, 
                            status="● [ОТВЕТ НА ВОПРОС]"
                        )
                    )
                except Exception as e:
                    self.hud.update_text(text=f"[Ошибка звука]: {e}", status="● [ОШИБКА]")

            threading.Thread(target=_worker, daemon=True).start()

    def switch_mode(self):
        """Переключение рабочего режима TEST ↔ INTERVIEW."""
        new_mode = self.context_manager.toggle_mode()
        self.hud.update_text(
            text=f"Режим переключен на: {new_mode}", 
            status=f"● [{new_mode}] READY"
        )
        print(f"[MODE] Активирован режим: {new_mode}")

    def start_listeners(self):
        """Запуск глобальных перехватчиков кнопок мыши и хоткеев клавиатуры."""
        
        # 1. Мышь: аппаратные боковые кнопки
        def on_click(x, y, button, pressed):
            if not pressed:
                return
            if button == mouse.Button.x1:
                self.trigger_fullscreen_solve()
            elif button == mouse.Button.x2:
                self.toggle_audio_listen()

        mouse_listener = mouse.Listener(on_click=on_click)
        mouse_listener.daemon = True
        mouse_listener.start()

        # 2. Клавиатурные хоткеи
        hotkey_bindings = {
            settings.hotkey_screen: self.trigger_fullscreen_solve,
            settings.hotkey_snip: self.trigger_snip_solve,
            settings.hotkey_audio: self.toggle_audio_listen,
            settings.hotkey_mode: self.switch_mode,
            settings.hotkey_hud: self.hud.toggle_visibility,
            settings.hotkey_dock: self.hud.toggle_dock_position,  # Alt + Down: смена позиции
        }

        try:
            hotkeys = keyboard.GlobalHotKeys(hotkey_bindings)
            hotkeys.daemon = True
            hotkeys.start()
            print("[HOTKEYS] Зарегистрированы активные бинды:")
            for key_combo, func in hotkey_bindings.items():
                print(f"  • {key_combo:<17} -> {func.__name__}")
        except Exception as e:
            print(f"[HOTKEYS ERROR] Ошибка инициализации глобальных хоткеев: {e}")

    def run(self):
        self.start_listeners()
        print("[READY] GhostAssist полностью готов к работе.")
        self.hud.run()


if __name__ == "__main__":
    app = GhostAssistApp()
    app.run()