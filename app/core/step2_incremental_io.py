"""Incremental persistence + output commit for ``get_pixiv_author_imgID_Thread``.

The Step 2 incremental-save subsystem — the PID-cutoff truncation pass,
the best-effort incremental flush, ``author_progress.json`` persistence, the
``pictures_id.txt`` queue/merge/append/write helpers, the early-skip list, the
final author-grouping regroup, and the run()-tail ``_commit_step2_outputs``.
Pulled out of ``thread_pid_scan.py`` and mixed into the worker via
``_Step2IncrementalIOMixin``. Every method uses only ``self.`` for cross-method
calls (resolved through inheritance) plus the module-level names imported
below, so behavior is byte-for-byte identical to the originals. State these
methods read/write (``_collected_pids`` / ``_seen_pids`` / ``_progress_updates``
/ ``_step2_early_skip_pids`` / ``exist_pid`` and their locks, plus
``_emit_output`` / ``_metadata_db``) lives on the concrete class. The
``compute_author_order`` import is deferred inside the method (as in the
original) to avoid an import cycle with ``thread_download``.
"""
from __future__ import annotations

import contextlib
import json
import os

from app.core.pixiv_thread_utils import atomic_write_text, normalize_pid
from app.core.worker_event import WorkerEvent


class _Step2IncrementalIOMixin:
    """Step 2 incremental persistence + output commit, mixed into the worker."""

    def _flush_step2_incremental(self, reason: str = "incremental") -> None:
        """Best-effort incremental save of pictures_id + author_progress.

        Safe to call from any thread (executor worker, main thread, crash
        hook). All file writes go through atomic helpers; pictures_id is
        merge-appended so concurrent callers can't truncate each other.
        """
        if not hasattr(self, "_collected_pids"):
            return
        lock = getattr(self, "_step2_flush_lock", None)
        if lock is None:
            return
        if not lock.acquire(blocking=False):
            return
        try:
            try:
                progress_file = os.path.join(self.path, "author_progress.json")
                self._persist_author_progress(progress_file)
            except Exception:
                pass
            try:
                self._write_step2_pictures_id([])
            except Exception:
                pass
        finally:
            lock.release()

    def _collect_step2_incremental_pid(self, raw_pid_list):
        """
        Keep only latest PIDs before the first known exist_pid boundary.
        Sort by PID numeric size (newer PID is larger). If PID is not numeric,
        fallback to non-truncated behavior to avoid missing data.
        """
        ordered = []
        seen = set()
        for raw_pid in raw_pid_list:
            pid = normalize_pid(raw_pid)
            if not pid:
                continue
            if pid in seen:
                continue
            seen.add(pid)
            ordered.append(pid)

        if not ordered:
            return [], [], {
                "input_count": 0,
                "kept_count": 0,
                "truncated_count": 0,
                "boundary_pid": "",
                "used_cutoff": False,
                "sorted_by_pid_size": False,
                "fallback_full_scan": False,
                "fallback_reason": "",
            }

        non_numeric = [pid for pid in ordered if not str(pid).isdigit()]
        if non_numeric:
            return ordered, [], {
                "input_count": len(ordered),
                "kept_count": len(ordered),
                "truncated_count": 0,
                "boundary_pid": "",
                "used_cutoff": False,
                "sorted_by_pid_size": False,
                "fallback_full_scan": True,
                "fallback_reason": "non_numeric_pid",
            }

        sorted_desc = sorted(ordered, key=lambda value: int(value), reverse=True)

        keep = []
        skipped = []
        boundary_pid = ""
        for index, pid in enumerate(sorted_desc):
            if pid in self.exist_pid:
                boundary_pid = pid
                skipped = sorted_desc[index:]
                break
            keep.append(pid)

        truncated_count = len(skipped)
        used_cutoff = bool(boundary_pid)
        return keep, skipped, {
            "input_count": len(sorted_desc),
            "kept_count": len(keep),
            "truncated_count": truncated_count,
            "boundary_pid": boundary_pid,
            "used_cutoff": used_cutoff,
            "sorted_by_pid_size": True,
            "fallback_full_scan": False,
            "fallback_reason": "",
        }

    def _persist_author_progress(self, progress_file):
        """寫入 author_progress.json（concern 1）：silent-failure 一致保留。"""
        try:
            if not (hasattr(self, '_progress_updates') and self._progress_updates):
                return
            try:
                prog = {}
                if os.path.isfile(progress_file):
                    try:
                        with open(progress_file, encoding='utf-8') as pf:
                            prog = json.load(pf)
                    except Exception:
                        prog = {}
                with self._progress_updates_lock:
                    for aid, ts in self._progress_updates:
                        prog[str(aid)] = ts
                tmpfile = progress_file + '.tmp'
                with open(tmpfile, 'w', encoding='utf-8') as pf:
                    json.dump(prog, pf, ensure_ascii=False, indent=2)
                os.replace(tmpfile, progress_file)
            except Exception as e:
                try:
                    self._q.put(WorkerEvent("output",f"<p><font color='red'>寫入 author_progress 失敗：{e}</font></p>"))
                except Exception:
                    pass
        except Exception:
            pass

    def _collect_step2_pids_from_queue(self, end):
        """收集 ``_collected_pids``（鎖保護的 ``(pid, user_id)`` buffer）並與 ``end`` 合併。

        ``end`` 是 run() 階段組出來的 flat PID 字串清單（已過濾 exist_pid），這條路徑
        沒有 user_id 資訊；``_collected_pids`` 來自 worker 在抓 profile/all 時即時
        附上的 author。

        合併策略：先以 ``end`` 的順序為主（保留 ``pictures_id.txt`` 的歷史寫入順序
        契約），用 ``collected`` 建 ``pid -> user_id`` lookup 把 user_id 補上去；
        ``collected`` 中**多出來**（end 沒有，例如 incremental flush 時還沒進 end
        的）的 PID 接在最後。下游 ``_merge_step2_pids_with_existing`` 依 PID 字串
        做 first-occurrence dedup，因此「end 提供順序、collected 提供 user_id」
        兩個語意都被保住。"""
        try:
            with self._collected_pids_lock:
                collected = list(self._collected_pids)
        except Exception:
            collected = list(getattr(self, '_collected_pids', []))
        # Build pid → user_id lookup, keeping the first non-None uid seen.
        uid_lookup = {}
        for entry in collected:
            if not isinstance(entry, tuple) or not entry:
                continue
            pid = str(entry[0])
            uid = entry[1] if len(entry) > 1 else None
            if uid is not None and pid not in uid_lookup:
                uid_lookup[pid] = uid
        end_tuples = [(str(pid), uid_lookup.get(str(pid))) for pid in end]
        return end_tuples + collected

    def _merge_step2_pids_with_existing(self, pics_file, combined_pids):
        """讀現有 pictures_id.txt 並 dedup，回傳 ``(existing_list, new_candidates)``。

        ``combined_pids`` 是 ``(pid, user_id)`` tuple list；``new_candidates``
        保持同樣 tuple 形狀，給下游分頭使用（txt 只寫 PID、DB 寫 PID + user_id）。"""
        existing_list = []
        if os.path.isfile(pics_file):
            try:
                with open(pics_file, encoding='utf-8') as pf:
                    existing_list = [line.strip() for line in pf if line.strip()]
            except Exception:
                existing_list = []
        existing_seen = set(existing_list)
        new_candidates = []
        for entry in combined_pids:
            if isinstance(entry, tuple):
                raw_pid = entry[0]
                uid = entry[1] if len(entry) > 1 else None
            else:
                raw_pid = entry
                uid = None
            spid = str(raw_pid).strip()
            if not spid or spid in self.exist_pid or spid in existing_seen:
                continue
            new_candidates.append((spid, uid))
            existing_seen.add(spid)
        return existing_list, new_candidates

    def _append_new_pids_to_file(self, pics_file: str, new_candidates: list) -> None:
        """寫 pictures_id.txt——只寫 PID 字串，user_id 純由 DB 路徑承載。"""
        try:
            with open(pics_file, 'a+', encoding='utf-8') as pf:
                for entry in new_candidates:
                    pid = entry[0] if isinstance(entry, tuple) else entry
                    pf.write(str(pid) + '\n')
        except Exception as e2:
            self._emit_output(f"<p><font color='red'>寫入 pictures_id 失敗：{e2}</font></p>")

    def _persist_pending_pids_to_db(self, new_candidates: list) -> None:
        """寫 DB——tuples 直接 forward 給 ``upsert_pending_pids``。"""
        db = getattr(self, "_metadata_db", None)
        if db is None or not new_candidates:
            return
        with contextlib.suppress(Exception):
            db.upsert_pending_pids(new_candidates)

    def _write_step2_pictures_id(self, end):
        """concern 2：合併 collected pids 並寫入 pictures_id.txt。"""
        pics_file = os.path.join(self.path, 'pictures_id.txt')
        with contextlib.suppress(Exception):
            os.makedirs(self.path, exist_ok=True)
        with contextlib.suppress(Exception):
            with open(pics_file, 'a+', encoding='utf-8'):
                pass
        combined = self._collect_step2_pids_from_queue(end)
        existing_list, new_candidates = self._merge_step2_pids_with_existing(pics_file, combined)
        if new_candidates:
            self._append_new_pids_to_file(pics_file, new_candidates)
        self._persist_pending_pids_to_db(new_candidates)
        self._emit_output(
            f"<p><font color='gray'>pictures_id 既有 {len(existing_list)} 筆，"
            f"新增 {len(new_candidates)} 筆，合計 {len(existing_list) + len(new_candidates)} 筆</font></p>"
        )

    def _write_step2_skip_pids(self):
        """concern 3：寫入 step2_skip_pid.txt 提前跳過清單。"""
        skip_file = os.path.join(self.path, "step2_skip_pid.txt")
        with self._step2_skip_lock:
            skip_lines = sorted(
                [str(x) for x in self._step2_early_skip_pids if str(x).strip()],
                key=lambda s: (0, int(s)) if str(s).isdigit() else (1, str(s)),
            )
        atomic_write_text(skip_file, skip_lines, backup=True)
        try:
            self._q.put(WorkerEvent("output",
                f"<p><font color='gray'>[PID增量] 已寫入步驟2提前跳過清單：{skip_file}（{len(skip_lines)} 筆）</font></p>"
            ))
        except Exception:
            pass

    def _regroup_pictures_id_by_author(self):
        """最終把 pictures_id.txt 依作者重排（同作者連續，作者不明排最後）。

        只在 run() 收尾呼叫一次——增量 flush 仍維持 append-only（崩潰安全）。
        此時新 PID 的 user_id 已寫進 DB，重排讀整檔 + DB user_id，重用步驟4
        的純函式 compute_author_order。重排前的版本經 atomic_write_text
        (backup=True) 留進 history/。DB 不可用 / 取 uid_map 失敗 / 檔案空 /
        已是分組順序 → 跳過（不報錯）。
        """
        if not getattr(self, "author_order", False):
            return
        db = getattr(self, "_metadata_db", None)
        if db is None:
            return
        pics_file = os.path.join(self.path, "pictures_id.txt")
        try:
            with open(pics_file, encoding="utf-8") as pf:
                pids = [line.strip() for line in pf if line.strip()]
        except Exception:
            return
        if not pids:
            return
        try:
            uid_map = db.user_id_map_for_pids(pids)
        except Exception:
            return
        from app.core.thread_download import compute_author_order
        flat, _ = compute_author_order(pids, uid_map)
        if flat == pids:
            return  # 已是分組順序，免寫
        unknown = sum(1 for p in pids if not str(uid_map.get(p) or "").strip())
        try:
            atomic_write_text(pics_file, flat, backup=True)
        except Exception as e:
            self._emit_output(f"<p><font color='red'>依作者重排 pictures_id 失敗：{e}</font></p>")
            return
        tail = f"，其中 {unknown} 筆作者不明排最後" if unknown else ""
        self._emit_output(
            f"<p><font color='gray'>[作者排序] 已依作者重排 pictures_id.txt"
            f"（{len(flat)} 筆{tail}）</font></p>"
        )

    def _commit_step2_outputs(self, end):
        progress_file = os.path.join(self.path, 'author_progress.json')
        self._persist_author_progress(progress_file)
        # 原本 pictures_id 與 step2_skip 共用一個 outer try/except: pass，保留同等 silent-failure 邊界
        try:
            self._write_step2_pictures_id(end)
            self._write_step2_skip_pids()
            self._regroup_pictures_id_by_author()
        except Exception:
            pass
