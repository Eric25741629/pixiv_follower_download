"""pictures_id.txt loading + skip-file prefilter for the Step 3 query engine
``get_img_url_thread`` (file-size refactor).

Locating the pictures_id source (DB pending_pids first, then file candidates),
partitioning lines against the no_to_check / step2_skip sets, and surfacing the
parse summary or failure. Mixed into ``get_img_url_thread`` via
``_Step3CheckExistMixin``; combined mode's ``_build_work_lists`` calls
``check_exist`` directly and keeps working because the call resolves through
inheritance. Every method reaches worker state (``self.path`` /
``self.no_to_check`` / ``self._q`` / ``self._diag`` / ``self._metadata_db``)
through inheritance, so behaviour is unchanged.
"""
from __future__ import annotations

import os

from app import i18n
from app.core.pixiv_thread_utils import normalize_pid, normalize_pid_set
from app.core.worker_event import WorkerEvent


class _Step3CheckExistMixin:
    def _check_exist_candidate_paths(self):
        """Return the list of pictures_id.txt paths to try, in priority order."""
        candidates = [os.path.join(self.path, "pictures_id.txt")]
        try:
            appdata_path = os.path.join(os.getenv('APPDATA') + r'/pixiv_download/', 'pictures_id.txt')
            if appdata_path not in candidates:
                candidates.append(appdata_path)
        except Exception:
            pass
        return candidates

    def _load_check_exist_block_set(self):
        """Build the PID set that should be excluded from Step 3 (no_to_check)."""
        try:
            if isinstance(self.no_to_check, list):
                return normalize_pid_set(self.no_to_check)
        except Exception:
            pass
        return set()

    def _load_step2_skip_set(self):
        """Load PIDs that Step 2 already filed as 'skip' (so we won't re-fetch)."""
        skip_file = os.path.join(self.path, "step2_skip_pid.txt")
        try:
            if os.path.isfile(skip_file):
                with open(skip_file, encoding="utf-8", errors="ignore") as f:
                    return normalize_pid_set(
                        [line.rstrip() for line in f if str(line).strip()]
                    )
        except Exception:
            pass
        return set()

    def _scan_pictures_id_lines(self, file_iter, block_set, step2_skip_set):
        """Walk an iterator of pictures_id lines and partition them.

        Returns ``(pids, raw_count, excluded_by_block, excluded_by_step2_skip)``.
        Pulled out so the caller can try strict-UTF-8 first, then fall back to
        a lenient re-read on UnicodeDecodeError without duplicating the logic.
        """
        pictures_id = []
        excluded_by_skip_file = 0
        excluded_by_step2_skip = 0
        raw_count = 0
        for line in file_iter:
            text = str(line).strip()
            if not text:
                continue
            raw_count += 1
            pid_key = normalize_pid(text)
            if pid_key and pid_key in block_set:
                excluded_by_skip_file += 1
                if pid_key in step2_skip_set:
                    excluded_by_step2_skip += 1
                continue
            pictures_id.append(text)
        return pictures_id, raw_count, excluded_by_skip_file, excluded_by_step2_skip

    def _scan_pictures_id_file(self, pic_path, block_set, step2_skip_set):
        """Stream-parse pictures_id.txt with UTF-8 strict, then lenient fallback."""
        try:
            with open(pic_path, encoding='utf-8') as f:
                return self._scan_pictures_id_lines(f, block_set, step2_skip_set)
        except UnicodeDecodeError:
            with open(pic_path, encoding='utf-8', errors='ignore') as f:
                return self._scan_pictures_id_lines(f, block_set, step2_skip_set)

    def _emit_check_exist_summary(self, pic_path, pictures_id, raw, blocked, step2_blocked):
        """Surface the parse summary to the user and append to the diagnostic log."""
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>pictures_id 來源: {pic_path}</font></p>"))
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[TaskFilter][Step3-Pre] pictures_id原始={raw}, "
                f"skip_file排除={blocked}, 待去重={len(pictures_id)}</font></p>"
            ))
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[TaskFilter][Step3-Pre] 這次從 step2_skip_pid.txt "
                f"排除 {step2_blocked} 筆（步驟2提前跳過，所以之前沒有存下來）</font></p>"
            ))
        except Exception:
            pass
        self._diag(
            "step3_skip_file_prefilter",
            source_path=str(pic_path),
            raw_count=int(raw),
            skipped_no_to_check=int(blocked),
            skipped_step2_skip_file=int(step2_blocked),
            post_skip_file_count=int(len(pictures_id)),
        )

    def _emit_check_exist_failure(self, candidates, last_err):
        """Notify the user and shut down Step 3 when no candidate file was readable."""
        detail = "" if last_err is None else f" ({last_err})"
        try:
            self._q.put(WorkerEvent("output",
                "<p><font color='red'>找不到 pictures_id.txt: {}</font></p>".format(
                    ' | '.join(candidates))))
            self._q.put(WorkerEvent("output",
                f"<p><font color='red'>讀取失敗: {detail}</font></p>"))
        except Exception:
            pass
        self._q.put(WorkerEvent("finished", i18n.t("log.url.stopped")))
        self._q.put(WorkerEvent("next", -1))

    def check_exist(self):
        block_set = self._load_check_exist_block_set()
        step2_skip_set = self._load_step2_skip_set()
        # DB-first: prefer pending_pids table when it has rows
        db = getattr(self, "_metadata_db", None)
        if db is not None:
            try:
                db_pids = db.get_pending_pids()
                if db_pids:
                    pictures_id, raw, blocked, step2_blocked = self._scan_pictures_id_lines(
                        db_pids, block_set, step2_skip_set
                    )
                    self._emit_check_exist_summary(
                        "DB:pending_pids", pictures_id, raw, blocked, step2_blocked
                    )
                    return pictures_id
            except Exception:
                pass
        # Fallback: file
        file_candidates = self._check_exist_candidate_paths()
        last_err = None
        for pic_path in file_candidates:
            if not os.path.isfile(pic_path):
                continue
            try:
                pictures_id, raw, blocked, step2_blocked = self._scan_pictures_id_file(
                    pic_path, block_set, step2_skip_set
                )
                self._emit_check_exist_summary(
                    pic_path, pictures_id, raw, blocked, step2_blocked
                )
                return pictures_id
            except Exception as err:
                last_err = err
        self._emit_check_exist_failure(file_candidates, last_err)
        return 0
