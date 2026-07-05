"""Background JPEG-XL conversion for ``download_thread`` (file-size refactor).

A self-contained subsystem: a pool of worker threads drains one shared FIFO
queue so ``cjxl.exe`` runs never block downloads and multiple conversions run
in parallel. Mixed into ``download_thread`` via ``_JXLMixin``; every method
uses only JXL-owned instance state (``self.jxl_*`` / ``self._jxl_*`` set by
``_init_jxl_config``) plus the shared event queue (``self._q``), stop event
(``self._stop_event``), optional ``self._stats_collector`` and
``self._format_size_human`` provided by the concrete class. Because several
workers now mutate the shared JXL counters concurrently, those increments are
guarded by ``self._jxl_stats_lock``. Absence of ``cjxl.exe`` must never break
downloads.
"""
from __future__ import annotations

import contextlib
import glob
import os
import shutil
import subprocess
import tempfile
import threading
from queue import Empty, Queue

from app.core.worker_event import WorkerEvent
from app import i18n


class _JXLMixin:
    """Background JXL-conversion helpers, mixed into ``download_thread``."""

    def _init_jxl_config(self, jxl_enable, jxl_cjxl_path, jxl_delete_original, jxl_effort,
                         jxl_skip_gif=True):
        """Resolve JXL settings + reset per-run JXL counters."""
        self.jxl_enable = bool(jxl_enable)
        self.jxl_skip_gif = bool(jxl_skip_gif)
        jxl_path_raw = (
            str(jxl_cjxl_path).strip()
            if str(jxl_cjxl_path).strip()
            else r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe"
        )
        self.jxl_cjxl_path = self._resolve_cjxl_path(jxl_path_raw)
        self.jxl_delete_original = bool(jxl_delete_original)
        try:
            self.jxl_effort = int(jxl_effort)
        except Exception:
            self.jxl_effort = 7
        self.jxl_effort = max(1, min(9, self.jxl_effort))
        self._jxl_path_warned = False
        self._jxl_gif_skip_warned = False
        self._jxl_ok_count = 0
        self._jxl_fail_count = 0
        self._jxl_src_total_bytes = 0
        self._jxl_dst_total_bytes = 0
        # Background JXL conversion: a pool of worker threads drains one shared
        # FIFO queue so cjxl runs do not block downloads and several files
        # convert in parallel.  Because multiple workers mutate the shared
        # counters concurrently, all counter increments go through
        # _jxl_stats_lock; the main thread reads them after _drain_jxl_queue()
        # in finalize.  Pool size is half the CPU count, clamped to [2, 4].
        self._jxl_queue: Queue = Queue()
        self._jxl_workers = min(4, max(2, (os.cpu_count() or 4) // 2))
        self._jxl_worker_threads: list[threading.Thread] = []
        self._jxl_stats_lock = threading.Lock()

    def _resolve_cjxl_path(self, preferred_path):
        preferred = str(preferred_path or "").strip()
        if preferred and os.path.isfile(preferred):
            return preferred
        candidates = [
            preferred,
            r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe",
            r"C:\Users\Eric\Downloads\jxl-x64-windows-static\bin\cjxl.exe",
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        try:
            user_home = os.path.expanduser("~")
            found = glob.glob(os.path.join(user_home, "Downloads", "jxl*", "bin", "cjxl.exe"))
            if found:
                found = sorted(found, key=lambda x: os.path.getmtime(x), reverse=True)
                return found[0]
        except Exception:
            pass
        return preferred or r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe"

    def _build_jxl_command(self, src_path, dst_path):
        ext = os.path.splitext(str(src_path))[1].lower()
        cmd = [self.jxl_cjxl_path, str(src_path), str(dst_path), "--effort", str(self.jxl_effort)]
        if ext in {".jpg", ".jpeg"}:
            cmd.append("--lossless_jpeg=1")
        else:
            cmd.append("--distance=0")
        return cmd

    def _run_cjxl_once(self, src_path, dst_path):
        cmd = self._build_jxl_command(src_path, dst_path)
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception):
                self._q.put(WorkerEvent("output",
                    f"<p><font color='orange'>{i18n.t('log.jxl.timeout', name=os.path.basename(str(src_path)))}</font></p>"
                ))
            return False, "cjxl timeout (120s)"
        if completed.returncode == 0 and os.path.isfile(dst_path):
            return True, ""
        reason = (completed.stderr or completed.stdout or "").strip()
        if len(reason) > 200:
            reason = reason[:197] + "..."
        if not reason:
            reason = f"cjxl exit={completed.returncode}"
        return False, reason

    def _run_cjxl_with_temp_ascii_path(self, src_path, dst_path, temp_name=None):
        ext = os.path.splitext(str(src_path))[1].lower() or ".bin"
        workdir = tempfile.mkdtemp(prefix="jxl_retry_")
        src_name = str(temp_name or ("input" + ext))
        if "." not in src_name:
            src_name = src_name + ext
        tmp_src = os.path.join(workdir, src_name)
        tmp_dst_name = "1_PID.jxl" if temp_name else "output.jxl"
        tmp_dst = os.path.join(workdir, tmp_dst_name)
        try:
            shutil.copy2(src_path, tmp_src)
            ok, reason = self._run_cjxl_once(tmp_src, tmp_dst)
            if ok and os.path.isfile(tmp_dst):
                shutil.move(tmp_dst, dst_path)
                return True, ""
            return False, reason
        except Exception as err:
            return False, str(err)
        finally:
            with contextlib.suppress(Exception):
                shutil.rmtree(workdir, ignore_errors=True)

    _JXL_SUPPORTED_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

    def _warn_cjxl_missing_once(self):
        """Emit the 'cjxl not found' warning at most once per worker run."""
        if self._jxl_path_warned:
            return
        self._jxl_path_warned = True
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("output",
                f"<p><font color='orange'>{i18n.t('log.jxl.cjxl_missing', path=self.jxl_cjxl_path)}</font></p>"
            ))

    def _warn_gif_skip_once(self):
        """Emit the 'GIF skipped' notice at most once per worker run."""
        if self._jxl_gif_skip_warned:
            return
        self._jxl_gif_skip_warned = True
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>{i18n.t('log.jxl.skip_gif')}</font></p>"
            ))

    def _jxl_stats_guard(self):
        """Return the stats lock, tolerating old test stubs that never set it."""
        lock = getattr(self, "_jxl_stats_lock", None)
        return lock if lock is not None else contextlib.nullcontext()

    def _handle_existing_jxl_destination(self, src_path):
        """Bump ok counter and optionally delete the original when .jxl already exists."""
        try:
            with self._jxl_stats_guard():
                self._jxl_ok_count += 1
            if self.jxl_delete_original and os.path.isfile(src_path):
                os.remove(src_path)
        except Exception:
            pass

    def _jxl_should_convert(self, src_path):
        """Run all gating checks. Returns (proceed, dst_path, ext).

        proceed=False means the orchestrator should return immediately. Side
        effects: emits the one-shot cjxl-missing warning and handles the
        "destination already exists" accounting + optional original deletion.
        """
        if not self.jxl_enable or not src_path:
            return False, None, None
        src_path = str(src_path)
        ext = os.path.splitext(src_path)[1].lower()
        if ext not in self._JXL_SUPPORTED_EXTS:
            return False, None, None
        if ext == ".gif" and self.jxl_skip_gif:
            # GIF→JXL re-encodes the animation and almost always hits the 120s
            # cjxl timeout; the 跳過 GIF 轉檔 switch defaults on. Keep the .gif.
            self._warn_gif_skip_once()
            return False, None, None
        if not os.path.isfile(src_path):
            return False, None, None
        if not os.path.isfile(self.jxl_cjxl_path):
            self._warn_cjxl_missing_once()
            return False, None, None
        dst_path = os.path.splitext(src_path)[0] + ".jxl"
        if os.path.isfile(dst_path):
            self._handle_existing_jxl_destination(src_path)
            return False, dst_path, ext
        return True, dst_path, ext

    def _jxl_run_conversion(self, src_path, dst_path, ext):
        """Invoke cjxl with extension-aware retry. Returns (ok, reason)."""
        try:
            if ext == ".gif":
                # GIF uses fixed short ASCII filename directly to avoid path/encoding failures.
                ok, reason = self._run_cjxl_with_temp_ascii_path(
                    src_path, dst_path, temp_name="1_PID.gif"
                )
            else:
                ok, reason = self._run_cjxl_once(src_path, dst_path)
        except Exception as err:
            ok, reason = False, str(err)
        if (not ok) and ext != ".gif":
            # Retry with short ASCII temp path to avoid unicode/long-path decode failures.
            ok, reason2 = self._run_cjxl_with_temp_ascii_path(src_path, dst_path)
            reason = "" if ok else reason2 or reason
        # GIF intentionally does not fall back to first-frame static conversion
        # so animation correctness is preserved.
        return ok, reason

    def _jxl_tally_sizes(self, src_path, dst_path):
        """Read src/dst sizes, update running totals, return ``(src_size, dst_size, saved)``.

        Any of the three return values may be ``None`` if a stat call failed.
        Total/stats updates are best-effort so test stubs that don't preset
        the running-total attributes don't crash.
        """
        try:
            src_size = os.path.getsize(src_path)
            dst_size = os.path.getsize(dst_path)
        except Exception:
            return None, None, None
        if src_size < 0 or dst_size < 0:
            return None, None, None
        try:
            with self._jxl_stats_guard():
                self._jxl_src_total_bytes += src_size
                self._jxl_dst_total_bytes += dst_size
        except Exception:
            pass
        try:
            if getattr(self, "_stats_collector", None) is not None:
                self._stats_collector.report_jxl(src_size, dst_size)
        except Exception:
            pass
        return src_size, dst_size, src_size - dst_size

    def _jxl_emit_compare_log(self, src_path, src_size, dst_size, saved):
        """Print the per-file 'src → dst (saved X, Y%)' log line if sizes are known."""
        if src_size is None or saved is None or src_size <= 0:
            return
        try:
            saved_ratio = (saved / float(src_size)) * 100.0
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>JXL 對比：{os.path.basename(src_path)} "
                f"{self._format_size_human(src_size)} → {self._format_size_human(dst_size)}"
                f"（省下 {self._format_size_human(saved)}, {saved_ratio:.2f}%）</font></p>"
            ))
        except Exception:
            pass

    def _jxl_delete_source_if_configured(self, src_path):
        """Delete the source file when jxl_delete_original is True (best-effort)."""
        if not self.jxl_delete_original:
            return
        with contextlib.suppress(Exception):
            os.remove(src_path)

    def _jxl_emit_failure_log(self, src_path, reason):
        """Print the per-file failure log line; trims very long reasons."""
        with self._jxl_stats_guard():
            self._jxl_fail_count += 1
        msg = reason if len(reason) <= 120 else reason[:117] + "..."
        if not msg:
            msg = "cjxl conversion failed"
        with contextlib.suppress(Exception):
            self._q.put(WorkerEvent("output",
                f"<p><font color='orange'>JXL 轉檔失敗：{os.path.basename(src_path)} "
                f"({msg})</font></p>"
            ))

    def _jxl_record_outcome(self, src_path, dst_path, ok, reason):
        """Update counters, emit log lines and optionally delete the source."""
        if ok and os.path.isfile(dst_path):
            # Carry the source's atime/mtime onto the .jxl: the download stamped
            # them to the timetag (set_file_mtime), and cjxl's fresh output file
            # would otherwise reset them to conversion time.
            with contextlib.suppress(Exception):
                st = os.stat(src_path)
                os.utime(dst_path, (st.st_atime, st.st_mtime))
            with self._jxl_stats_guard():
                self._jxl_ok_count += 1
            src_size, dst_size, saved = self._jxl_tally_sizes(src_path, dst_path)
            self._jxl_emit_compare_log(src_path, src_size, dst_size, saved)
            self._jxl_delete_source_if_configured(src_path)
            return
        self._jxl_emit_failure_log(src_path, reason)

    def _convert_file_to_jxl(self, src_path):
        proceed, dst_path, ext = self._jxl_should_convert(src_path)
        if not proceed:
            return
        src_path = str(src_path)
        ok, reason = self._jxl_run_conversion(src_path, dst_path, ext)
        self._jxl_record_outcome(src_path, dst_path, ok, reason)

    def _start_jxl_worker_if_needed(self):
        """Lazily spawn the background cjxl worker pool on first enqueue.

        Spawns ``self._jxl_workers`` daemon threads at once; a no-op when any
        worker from a previous spawn is still alive."""
        if any(t.is_alive() for t in self._jxl_worker_threads):
            return
        self._jxl_worker_threads = []
        for i in range(self._jxl_workers):
            t = threading.Thread(
                target=self._jxl_worker_loop, daemon=True, name=f"jxl-worker-{i}"
            )
            t.start()
            self._jxl_worker_threads.append(t)

    def _jxl_worker_loop(self):
        """Process queued source paths.  One shared queue feeds every worker,
        so each pops the next available file.  Exits cleanly when it pops a
        ``None`` sentinel pushed by ``_drain_jxl_queue`` (one sentinel per
        worker).  Uses a 1-second polling timeout so the thread can exit if
        the main thread crashes before pushing the sentinel."""
        import queue as _queue_mod
        while not self._stop_event.is_set():
            try:
                src_path = self._jxl_queue.get(timeout=1.0)
            except _queue_mod.Empty:
                continue
            if src_path is None:
                with contextlib.suppress(Exception):
                    self._jxl_queue.task_done()
                return
            try:
                with contextlib.suppress(Exception):
                    self._convert_file_to_jxl(src_path)
            finally:
                with contextlib.suppress(Exception):
                    self._jxl_queue.task_done()

    def _enqueue_jxl(self, src_path):
        """Hand a source file off to the background worker.  Non-blocking;
        falls through silently when JXL is disabled or the path is empty."""
        if not self.jxl_enable or not src_path:
            return
        self._start_jxl_worker_if_needed()
        with contextlib.suppress(Exception):
            self._jxl_queue.put(str(src_path))

    def _discard_pending_jxl_items(self):
        """Drain queued items without running cjxl (used on user stop so the
        user is not left waiting on a long backlog).  Does not interrupt
        the in-flight conversion."""
        while True:
            try:
                self._jxl_queue.get_nowait()
            except (Empty, Exception):
                return
            with contextlib.suppress(Exception):
                self._jxl_queue.task_done()

    def _drain_jxl_queue(self):
        """Wait for queued conversions to finish, then shut the worker pool down.

        On normal completion, blocks until every enqueued file has been
        processed so the JXL summary is accurate.  On a user-initiated stop,
        discards pending items first (keeps in-flight conversions only) so
        the user is not left waiting minutes for a long backlog.  Pushes one
        ``None`` sentinel per live worker and joins each."""
        workers = [t for t in self._jxl_worker_threads if t.is_alive()]
        if not workers:
            return
        if self._stop_event.is_set():
            self._discard_pending_jxl_items()
        with contextlib.suppress(Exception):
            self._jxl_queue.join()
        with contextlib.suppress(Exception):
            for _ in workers:
                self._jxl_queue.put(None)
        for worker in workers:
            with contextlib.suppress(Exception):
                worker.join(timeout=5.0)
