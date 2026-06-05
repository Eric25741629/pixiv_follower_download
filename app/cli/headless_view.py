"""Minimal stand-in for MainView so RunController runs without a GUI.

RunController only calls three things on its view: set_running(bool),
set_step_state(i, state), and reads/writes _active_thread. Everything else
the GUI MainView does is irrelevant headless.
"""
from __future__ import annotations


class HeadlessView:
    def __init__(self):
        self.running = False
        self.step_states = ["idle", "idle", "idle", "idle"]
        self._active_thread = None

    def set_running(self, value: bool) -> None:
        self.running = bool(value)

    def set_step_state(self, index: int, state: str) -> None:
        try:
            self.step_states[int(index)] = str(state)
        except (TypeError, ValueError, IndexError):
            pass
