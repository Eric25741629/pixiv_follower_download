"""Queue adapters that translate / drop Step 4 progress events in combined mode.

Two self-contained module-level helpers moved out of ``thread_combined`` verbatim.
They wrap the real event :class:`queue.Queue` while ``combined_thread`` owns PID
progress: ``_CombinedPageProgressQueue`` rewrites the downloader's per-file
``"progress"`` events into ``"page_progress"`` events, and
``_DropOverallProgressQueue`` swallows the fetcher's stray overall ``"progress"``
events during a query. They depend only on ``WorkerEvent`` and ``normalize_pid``.
"""
import time

from app.core.worker_event import WorkerEvent
from app.core.pixiv_thread_utils import normalize_pid

# Min seconds between INTERMEDIATE lane emits per worker (the final page always
# emits). Bounds the per-page UI-update rate so a burst of fast pages
# (already-on-disk skips, retries) across K workers can't flood the dispatcher —
# the historical Phase-1 freeze class. Tests set this to 0 to disable throttling.
_LANE_MIN_INTERVAL = 0.15


def _coerce_progress_delta(data):
    """Pull the page delta out of a downloader ``"progress"`` event payload.

    The payload is ``(delta, total)`` (per-file emits send ``(1, pid_max)``); a
    bare int is also accepted. Falls back to 1 on any unexpected shape."""
    try:
        if isinstance(data, (tuple, list)) and data:
            return int(data[0])
        return int(data)
    except Exception:
        return 1


class _CombinedPageProgressQueue:
    """Translate Step 4 page progress while combined mode owns PID progress."""

    def __init__(self, target_q, pid, total):
        self._target_q = target_q
        self._pid = str(normalize_pid(pid) or pid)
        self._total = int(total)

    def put(self, event, *args, **kwargs):
        if isinstance(event, WorkerEvent) and event.type == "progress":
            self._target_q.put(
                WorkerEvent(
                    "page_progress",
                    {
                        "delta": _coerce_progress_delta(event.data),
                        "total": self._total,
                        "pid": self._pid,
                    },
                ),
                *args,
                **kwargs,
            )
            return
        self._target_q.put(event, *args, **kwargs)

    def reset(self):
        self._target_q.put(
            WorkerEvent(
                "page_progress",
                {"delta": 0, "total": self._total, "pid": self._pid},
            )
        )


class _LaneProgressQueue:
    """Translate the downloader's per-file ``"progress"`` into slot-keyed
    ``"lane"`` events for parallel combined mode, using the CALLING THREAD's lane
    context.

    Parallel combined shows one lane (row) per concurrent worker. The K workers
    SHARE one ``download_thread`` (and therefore one ``downloader._q``), so this
    queue is installed **once** for the whole phase and must not hold per-PID
    state — it reads the current thread's context via ``ctx_getter`` instead. A
    worker sets its thread-local ctx (``{slot, pid, alias, total, page}``) around
    its own download; the downloader emits ``("progress", (1, pid_max))`` per
    attempted page on that same worker thread, so ``put`` reads that thread's ctx,
    bumps its page count (capped at ``total``) and emits a ``lane`` event for the
    right slot. Distinct threads have distinct ctx objects, so concurrent workers
    never clobber each other — unlike swapping the shared ``downloader._q`` per
    PID, which races. A ``"progress"`` with no active ctx (a stray emit) is
    dropped so it can't reach the overall bar. Other events pass through.

    Volume is one event per page (~one per several seconds per worker), so K
    lanes can't flood the dispatcher.
    """

    def __init__(self, target_q, ctx_getter):
        self._target_q = target_q
        self._ctx_getter = ctx_getter

    def put(self, event, *args, **kwargs):
        if isinstance(event, WorkerEvent) and event.type in ("progress", "page_progress"):
            ctx = None
            try:
                ctx = self._ctx_getter()
            except Exception:
                ctx = None
            if not ctx:
                return  # no active lane on this thread -> drop (never hits overall bar)
            ctx["page"] = min(ctx["total"], ctx["page"] + _coerce_progress_delta(event.data))
            # Throttle intermediate updates per worker (the page count still
            # accumulates exactly; only the UI emit rate is bounded). The FIRST
            # emit always shows (immediate feedback) and the FINAL page always
            # emits (bar reaches its attempted total); only mid-stream updates
            # within the interval are coalesced.
            now = time.monotonic()
            first = "last_emit" not in ctx
            if (not first and ctx["page"] < ctx["total"]
                    and (now - ctx["last_emit"]) < _LANE_MIN_INTERVAL):
                return
            ctx["last_emit"] = now
            self._target_q.put(
                WorkerEvent("lane", {
                    "slot": ctx["slot"], "pid": ctx["pid"], "alias": ctx["alias"],
                    "state": "下載", "page": ctx["page"], "total": ctx["total"],
                }),
                *args, **kwargs,
            )
            return
        self._target_q.put(event, *args, **kwargs)


class _DropProgressQueue:
    """Forward everything EXCEPT ``progress`` / ``page_progress`` events.

    Installed on the FETCHER's ``_q`` for the whole concurrent 邊查邊下 phase so
    its per-PID query ``progress`` (which would reach the overall bar as the
    ``(1, 0)`` blank-the-bar bug, the fetcher's ``pid_max`` being 0 here) is
    dropped — the combined coordinator owns overall progress (one tick per
    finished PID). The DOWNLOADER's ``_q`` instead gets a :class:`_LaneProgressQueue`
    (per-worker lane events). Output / countdown / phase / lane / finished / next
    pass straight through. (Before the per-worker lane panel, this was installed
    on BOTH engines and dropped all per-page progress; the downloader half is now
    the lane queue.)
    """

    _DROP = {"progress", "page_progress"}

    def __init__(self, target_q):
        self._target_q = target_q

    def put(self, event, *args, **kwargs):
        if isinstance(event, WorkerEvent) and getattr(event, "type", None) in self._DROP:
            return
        self._target_q.put(event, *args, **kwargs)


class _DropOverallProgressQueue:
    """Forward everything to the real queue EXCEPT overall ``"progress"`` events.

    In combined mode the fetcher's :meth:`get_download_url` calls
    ``_step3_advance_progress`` once per PID, which emits
    ``WorkerEvent("progress", (1, fetcher.pid_max))``. The fetcher's ``pid_max``
    is **0** here — its ``run()`` / ``_load_and_filter_pid_list`` (the only place
    that sets it) never runs in combined mode — so that event reaches
    ``MainView.update_progress`` as ``(1, 0)`` and, because ``total <= 0``, blanks
    (or, after the visible-gating fix, hides) the 整體進度 bar **every time a PID
    is queried**. That is the "整體進度 shows when a PID finishes but disappears
    the moment the next PID starts" bug. combined owns overall progress (exactly
    one tick per PID, emitted from :meth:`combined_thread.run`), so the fetcher's
    per-PID ``progress`` events are dropped while querying. All other event kinds
    (output / countdown / page_progress / next / ...) pass straight through.
    """

    def __init__(self, target_q):
        self._target_q = target_q

    def put(self, event, *args, **kwargs):
        if isinstance(event, WorkerEvent) and getattr(event, "type", None) == "progress":
            return
        self._target_q.put(event, *args, **kwargs)
