"""Headless execution: run the pipeline without a Flet GUI.

Builds the same event queue + EventLog + RunController that flet_app.main
constructs, then pumps the event queue: prints output to stderr, forwards
``next`` events to RunController.on_next so Run-All chains, and exits on the
terminal ``next == -1`` (failure) or the final ``finished`` (single step).
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
from queue import Empty, Queue

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _TAG_RE.sub("", str(s)).strip()


def _log_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _pump(event_q, controller, run_all: bool, initial_step: int = 1, base_path: str = "") -> int:
    """Consume events until terminal. Returns process exit code.

    Terminal detection (``next == -1`` is overloaded -- it is BOTH a failed
    step's error terminal AND combined mode's normal terminal, so we
    disambiguate by whether a ``finished`` immediately preceded it):
      - single step: exit 0 on ``finished``; exit 1 on ``next == -1``.
      - run_all: forward ``next n`` to controller.on_next to chain. A normal
        pipeline ends at step 4 with a bare ``finished`` (no following
        ``next``) -> exit 0. A combined run ends at step 3 with ``finished``
        then ``next == -1`` -> exit 0 (finished preceded it). A step that
        fails emits ``next == -1`` with NO preceding finished -> exit 1.
    """
    current_step = int(initial_step)
    last_was_finished = False
    while True:
        try:
            ev = event_q.get(timeout=600)
        except Empty:
            _log_err("[headless] timed out waiting for events")
            return 2
        kind = getattr(ev, "type", None)
        data = getattr(ev, "data", None)
        if kind == "output":
            line = _strip_html(data)
            if line:
                _log_err(line)
        elif kind == "timechanged":
            # Persist the advancing download_time cursor (the GUI does this via
            # handle_timechanged) so repeated headless runs don't restart from
            # the same timestamp and mass-duplicate filename prefixes.
            _persist_download_time(base_path, data)
        elif kind == "next":
            if data == -1:
                return 0 if last_was_finished else 1
            last_was_finished = False
            current_step = int(data)
            if run_all:
                controller.on_next(current_step)
        elif kind == "finished":
            line = _strip_html(data)
            if line:
                _log_err(line)
            last_was_finished = True
            if not run_all:
                return 0
            # run_all: the last pipeline step (4, or combined at step 3 which
            # then emits next=-1) is terminal. step<4 normal finishes are
            # followed by a 'next' that chains the next step, so keep pumping.
            if current_step >= 4:
                return 0


def _persist_download_time(base_path, value) -> None:
    """Write the advancing per-file download_time cursor back to settings.

    The GUI dispatcher's handle_timechanged does this; without it a headless
    Step 4 / combined run never persists the cursor, so every subsequent run
    restarts from the same stored value and mass-duplicates filename prefixes.
    """
    if not value:
        return
    with contextlib.suppress(Exception):
        from app.core.settings_store import SettingsStore
        SettingsStore(base_path).update_fields("download", {"download_time": str(value)})


def _build_event_log(base_path):
    """Best-effort EventLog with crash recovery, mirroring flet_app.main.

    Reads the event-log knobs via the Flet-free
    ``event_log_kwargs_from_settings`` so a Flet-less host still gets crash
    recovery (the old ``from app.gui.flet_app import _event_log_kwargs`` pulled
    in ``flet`` and silently fell through to no event log)."""
    try:
        from app.core.event_log import (
            EventLog,
            event_log_kwargs_from_settings,
            recover_tail,
        )
        from app.core.metadata_db import MetadataDB
        el = EventLog(base_path, **event_log_kwargs_from_settings(base_path))
        if el.last_session_was_unclean:
            db = MetadataDB(base_path)
            try:
                recover_tail(db, el.log_dir)
            finally:
                db.close()
        el.emit("checkpoint", pid=os.getpid())
        return el
    except Exception as exc:
        _log_err(f"[headless] event log unavailable: {exc}")
        return None


def run_headless(step: str, *, force_rescan: bool = False) -> int:
    """Run one pipeline action headless. step in {1,2,3,4,combined,all}.

    ``force_rescan`` (Step 2 only) makes the scan ignore the 30-day
    "already scanned" skip and re-scan every artist to backfill user_id.
    """
    from app.cli.headless_view import HeadlessView
    from app.gui.run_actions import RunController

    base = os.path.join(os.getenv("APPDATA", ""), "pixiv_download")
    os.makedirs(base, exist_ok=True)
    event_q: Queue = Queue()
    event_log = _build_event_log(base)
    controller = RunController(HeadlessView(), event_q, event_log=event_log)
    if force_rescan:
        controller.force_rescan = True

    step = str(step).strip().lower()
    run_all = step == "all"
    try:
        if run_all:
            controller.run_all()
            initial_step = 1
        elif step == "combined":
            controller.force_combined = True
            controller.run_step(3)
            initial_step = 3
        elif step in {"1", "2", "3", "4"}:
            controller.run_step(int(step))
            initial_step = int(step)
        else:
            _log_err(f"[headless] unknown step: {step}")
            return 2
        return _pump(event_q, controller, run_all=run_all, initial_step=initial_step, base_path=base)
    finally:
        if event_log is not None:
            with contextlib.suppress(Exception):
                event_log.close()
