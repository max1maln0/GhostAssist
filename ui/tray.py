"""System tray callbacks enqueue commands for the Tk thread."""

from threading import Event, Thread

import pystray
from PIL import Image, ImageDraw


class Tray:
    def __init__(self, enqueue):
        self.ready = Event()
        self.running = False
        self.externally_running = False
        self.visible = True
        self.closing = False
        image = Image.new("RGBA", (64, 64), (8, 14, 26, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((12, 8, 52, 54), 16, fill=(16, 185, 129, 255))
        draw.ellipse((22, 23, 28, 29), fill=(8, 14, 26, 255))
        draw.ellipse((36, 23, 42, 29), fill=(8, 14, 26, 255))
        item = pystray.MenuItem

        def command(name):
            return lambda icon, menu_item: enqueue(name)

        self.icon = pystray.Icon(
            "GhostAssist",
            image,
            "GhostAssist",
            menu=pystray.Menu(
                item(
                    "Открыть Dashboard",
                    command("show"),
                    default=True,
                    enabled=lambda _: not self.visible and not self.closing,
                ),
                item(
                    "Скрыть Dashboard",
                    command("hide"),
                    enabled=lambda _: self.visible and not self.closing,
                ),
                item(
                    "Запустить службу",
                    command("start"),
                    enabled=lambda _: (
                        not self.running
                        and not self.externally_running
                        and not self.closing
                    ),
                ),
                item(
                    "Остановить службу",
                    command("stop"),
                    enabled=lambda _: self.running and not self.closing,
                ),
                item(
                    "Отменить текущую задачу",
                    command("cancel"),
                    enabled=lambda _: self.running and not self.closing,
                ),
                item(
                    "Завершить GhostAssist",
                    command("exit"),
                    enabled=lambda _: not self.closing,
                ),
            ),
        )

    def start(self):
        def setup(icon):
            icon.visible = True
            self.ready.set()

        self.icon.run_detached(setup=setup)

    def stop(self):
        self.closing = True
        if self.ready.is_set():
            self.icon.stop()
        else:

            def stop_after_start():
                if self.ready.wait(5):
                    self.icon.stop()

            Thread(target=stop_after_start, daemon=True).start()
