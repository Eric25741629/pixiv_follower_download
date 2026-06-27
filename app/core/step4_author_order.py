"""Author-ordering primitives for ``download_thread`` (file-size refactor).

Pure, module-level functions that reorder a PID sequence so each author's
works are contiguous. They take no ``self`` and touch no instance state, so
they live at module scope and are re-imported back into ``thread_download``
(``thread_download.compute_author_order`` is read by ``thread_combined`` and
``thread_pid_scan``). Behavior is byte-for-byte identical to the originals.
"""
from __future__ import annotations


def _leading_pid_int(pid) -> int | None:
    """Return the leading-digit run of ``pid`` as an int, or None if it has
    no leading digit. Robust to hash-form pids like ``"12345-abcdef"``."""
    s = str(pid)
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    return int(s[:i]) if i else None


def _within_author_sorted(pids: list[str]) -> list[str]:
    """Sort one author's pids: by leading-digit value descending (so hash-form
    pids like ``"12345-abcdef"`` still sort numerically), then any pids with no
    leading digit in reverse-lexical order at the end (deterministic)."""
    numeric = sorted((p for p in pids if _leading_pid_int(p) is not None),
                     key=_leading_pid_int, reverse=True)
    nonnumeric = sorted((p for p in pids if _leading_pid_int(p) is None),
                        reverse=True)
    return numeric + nonnumeric


def compute_author_order(pid_order, pid_to_user_id):
    """Reorder pids so each author's works are contiguous.

    - Authors are sequenced by first-encounter order in ``pid_order``.
    - Within an author, pids are PID-descending (see _within_author_sorted).
    - pids whose user_id is None/empty/missing form one "unknown" bucket
      appended last.

    Returns ``(flat_order, author_batches)`` where ``author_batches`` is a
    list of per-author pid lists (one batch per author, unknown bucket last)
    and ``flat_order`` is those batches concatenated.
    """
    author_seq: list[str] = []
    groups: dict[str, list[str]] = {}
    unknown: list[str] = []
    for pid in pid_order:
        uid = pid_to_user_id.get(pid)
        key = "" if uid is None else str(uid).strip()
        if not key:
            unknown.append(pid)
            continue
        if key not in groups:
            groups[key] = []
            author_seq.append(key)
        groups[key].append(pid)
    author_batches = [_within_author_sorted(groups[k]) for k in author_seq]
    if unknown:
        author_batches.append(_within_author_sorted(unknown))
    flat_order = [pid for batch in author_batches for pid in batch]
    return flat_order, author_batches
