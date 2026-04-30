from __future__ import annotations
import queue
import flet as ft
from app.gui.dispatcher import EventDispatcher


def main(page: ft.Page) -> None:
    page.title = "Pixiv 下載器"
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.theme = ft.Theme(color_scheme_seed="#0096FA")

    event_q: queue.Queue = queue.Queue()

    status_text = ft.Text("Flet 骨架已啟動 — 待接入 UI 模組")

    def handle_output(data: str) -> None:
        status_text.value = data

    disp = EventDispatcher(page, event_q, {
        "output": handle_output,
    })

    page.add(
        ft.AppBar(title=ft.Text("Pixiv 下載器")),
        ft.Column([status_text]),
    )
    page.run_thread(disp.run)


if __name__ == "__main__":
    ft.app(target=main)
