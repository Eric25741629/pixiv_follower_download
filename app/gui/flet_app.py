from __future__ import annotations
import queue
import flet as ft

from app.gui.dispatcher import EventDispatcher
from app.gui.run_actions import RunController
from app.gui.views.main_view import MainView
from app.gui.views.settings_view import SettingsView
from app.gui.views.cookies_view import CookiesView


def main(page: ft.Page) -> None:
    page.title = "Pixiv 下載器"
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.theme = ft.Theme(color_scheme_seed="#0096FA")
    page.window.width = 1100
    page.window.height = 750
    page.padding = 0

    event_q: queue.Queue = queue.Queue()

    main_view = MainView(page, event_q)
    settings_view = SettingsView(page)
    cookies_view = CookiesView(page)

    run_controller = RunController(main_view, event_q)
    main_view._run_controller = run_controller

    views_built = [main_view.build(), settings_view.build(), cookies_view.build()]

    content_area = ft.Column(
        controls=[views_built[0]],
        expand=True,
    )

    def on_nav_change(e: ft.ControlEvent) -> None:
        idx = e.control.selected_index
        content_area.controls = [views_built[idx]]
        page.update()

    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        on_change=on_nav_change,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.HOME_OUTLINED,
                selected_icon=ft.Icons.HOME,
                label="主頁",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SETTINGS_OUTLINED,
                selected_icon=ft.Icons.SETTINGS,
                label="設定",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.COOKIE_OUTLINED,
                selected_icon=ft.Icons.COOKIE,
                label="Cookie",
            ),
        ],
    )

    def handle_output(data: str) -> None:
        main_view.append_log(data)

    def handle_progress(data: tuple) -> None:
        current, total = data
        main_view.update_progress(current, total)

    def handle_countdown(data: int) -> None:
        main_view.update_countdown(data)

    def handle_finished(data: str) -> None:
        main_view.append_log(f"<p><font color='green'>{data}</font></p>")
        for i, st in enumerate(main_view._step_states):
            if st == "running":
                main_view.set_step_state(i, "done")
        main_view.set_running(False)

    def handle_next(data: int) -> None:
        if data == -1:
            for i, st in enumerate(main_view._step_states):
                if st == "running":
                    main_view.set_step_state(i, "error")
            main_view.set_running(False)
            return
        for i, st in enumerate(main_view._step_states):
            if st == "running":
                main_view.set_step_state(i, "done")
        run_controller.on_next(data)

    disp = EventDispatcher(page, event_q, {
        "output":    handle_output,
        "progress":  handle_progress,
        "countdown": handle_countdown,
        "finished":  handle_finished,
        "next":      handle_next,
    })

    def toggle_theme(e: ft.ControlEvent) -> None:
        page.theme_mode = (
            ft.ThemeMode.DARK
            if page.theme_mode == ft.ThemeMode.LIGHT
            else ft.ThemeMode.LIGHT
        )
        page.update()

    page.appbar = ft.AppBar(
        title=ft.Text("Pixiv 下載器"),
        center_title=False,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        actions=[
            ft.IconButton(
                icon=ft.Icons.LIGHT_MODE,
                tooltip="切換深淺色",
                on_click=toggle_theme,
            ),
        ],
    )

    page.add(
        ft.Row(
            controls=[nav_rail, ft.VerticalDivider(width=1), content_area],
            expand=True,
        )
    )
    page.run_thread(disp.run)


if __name__ == "__main__":
    ft.app(target=main)
