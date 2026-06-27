"""Queue adapters that translate / drop Step 4 progress events in combined mode.

Two self-contained module-level helpers moved out of ``thread_combined`` verbatim.
They wrap the real event :class:`queue.Queue` while ``combined_thread`` owns PID
progress: ``_CombinedPageProgressQueue`` rewrites the downloader's per-file
``"progress"`` events into ``"page_progress"`` events, and
``_DropOverallProgressQueue`` swallows the fetcher's stray overall ``"progress"``
events during a query. They depend only on ``WorkerEvent`` and ``normalize_pid``.
"""
from app.core.worker_event import WorkerEvent
from app.core.pixiv_thread_utils import normalize_pid


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
                        "delta": self._coerce_delta(event.data),
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

    @staticmethod
    def _coerce_delta(data):
        try:
            if isinstance(data, (tuple, list)) and data:
                return int(data[0])
            return int(data)
        except Exception:
            return 1


class _DropProgressQueue:
    """Forward everything EXCEPT ``progress`` / ``page_progress`` events.

    Installed on both engines' ``_q`` for the whole concurrent 邊查邊下 phase so
    the K workers' per-PID page/overall progress can never flood the single
    :class:`~app.gui.dispatcher.EventDispatcher` (the freeze cause in the
    reverted Phase 1). The combined coordinator owns overall progress (one tick
    per finished PID) and the lightweight aggregate phase line; per-page bars
    are meaningless across K concurrent PIDs so they are dropped. Output /
    countdown / phase / finished / next pass straight through.
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
