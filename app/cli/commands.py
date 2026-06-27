"""argparse CLI for headless use by humans / LLMs.

Read commands print JSON to stdout (with --json); human logs go to stderr.
Exit codes are meaningful (0 ok, non-zero on error).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys


def _base_path() -> str:
    p = os.path.join(os.getenv("APPDATA", ""), "pixiv_download")
    os.makedirs(p, exist_ok=True)
    return p


def _store():
    from app.core.settings_store import SettingsStore
    s = SettingsStore(_base_path())
    s.migrate_from_legacy()
    return s


def _infer_from_literal(raw: str):
    """Infer a type from the raw text when there is no typed default to copy.

    Recognizes bool / int literals so keys absent from DEFAULTS (e.g.
    download.combined_mode) still round-trip as their natural type; falls back
    to the string otherwise.
    """
    text = str(raw).strip()
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(text)
    except (TypeError, ValueError):
        return str(raw)


def _coerce_like(existing, raw: str):
    """Infer the type to store from the existing/default value's type."""
    if isinstance(existing, bool):
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(existing, int):
        return int(raw)
    if isinstance(existing, list):
        return [x for x in str(raw).split(",") if x]
    if existing is None:
        return _infer_from_literal(raw)
    return str(raw)


def _cmd_status(args) -> int:
    from app.core.metadata_db import MetadataDB, DB_FILENAME
    base = _base_path()
    db = MetadataDB(base)
    try:
        counts = db.page_status_counts()
        meta = db.meta_count()
    finally:
        db.close()
    payload = {
        "pending_pages": int(counts.get("pending", 0)),
        "downloaded_pages": int(counts.get("downloaded", 0)),
        "failed_pages": int(counts.get("failed", 0)),
        "revoked_pages": int(counts.get("revoked", 0)),
        "meta_count": int(meta),
        "db_path": os.path.join(base, DB_FILENAME),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for k, v in payload.items():
            print(f"{k}: {v}")
    return 0


def _cmd_config(args) -> int:
    store = _store()
    if "." not in args.key:
        print("key must be <section>.<field>", file=sys.stderr)
        return 2
    section, field = args.key.split(".", 1)
    sec = store.get_section(section)
    if args.action == "get":
        value = sec.get(field)
        if args.json:
            print(json.dumps({"key": args.key, "value": value}, ensure_ascii=False))
        else:
            print(value)
        return 0
    # set
    new_value = _coerce_like(sec.get(field), args.value)
    store.update_fields(section, {field: new_value})
    print(f"set {args.key} = {new_value!r}", file=sys.stderr)
    return 0


def _cmd_cookie_test(args) -> int:
    from app.core import pixiv_api
    auth = _store().get_section("auth")
    agent = str(auth.get("agent") or "").strip() or "Mozilla/5.0"
    entries = auth.get("cookies_entries") or []
    cookies = [str(e.get("cookie", "")).strip() for e in entries if isinstance(e, dict)]
    cookies = [c for c in cookies if c] or ([str(auth.get("cookies", "")).strip()] if auth.get("cookies") else [])
    results = []
    for c in cookies:
        try:
            # Test_cookies prints exceptions to stdout; redirect to stderr so a
            # `--json` run keeps stdout a single clean JSON line (the CLI
            # contract: JSON to stdout, human/diagnostic text to stderr).
            with contextlib.redirect_stdout(sys.stderr):
                count, _ = pixiv_api.Test_cookies([c], agent)
            ok = int(count) > 0
        except Exception:
            ok = False
        results.append({"ok": ok})
    payload = {"tested": len(results), "valid": sum(1 for r in results if r["ok"])}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"tested={payload['tested']} valid={payload['valid']}")
    return 0 if payload["valid"] > 0 else 1


def _cmd_following_export(args) -> int:
    from app.gui.run_actions import _load_author_list
    authors = _load_author_list()
    if args.json:
        print(json.dumps({"following": authors}, ensure_ascii=False))
    else:
        for a in authors:
            print(a)
    return 0


def _fmt_from_path_or_url(file_path, url) -> str:
    """Pick an image format from the file path extension, falling back to the URL."""
    for cand in (file_path, url):
        if not cand:
            continue
        ext = os.path.splitext(str(cand))[1].lstrip(".").lower()
        if ext:
            return ext
    return ""


def _build_page_path_index(download_path):
    """Walk ``download_path`` once, mapping ``(pid, page_index) -> abs path`` and
    collecting stale ``.part`` temp files left by a force-killed atomic write.

    Reuses the canonical filename parser so it understands every on-disk naming
    form. Returns ``(index, part_files)``; both empty when the folder is missing.
    One walk serves both so a 200k-file folder is scanned only once.
    """
    from app.core.pid_filesystem import extract_pid_pages
    index: dict[tuple[str, int], str] = {}
    part_files: list[str] = []
    if not download_path or not os.path.isdir(download_path):
        return index, part_files
    for dirpath, _dirs, files in os.walk(download_path):
        for fn in files:
            if fn.endswith(".part"):
                part_files.append(os.path.join(dirpath, fn))
                continue
            for pid, page in extract_pid_pages(fn):
                index.setdefault((str(pid), int(page)), os.path.join(dirpath, fn))
    return index, part_files


def _resolve_page_file(pid, page_index, url, file_path, path_index):
    """Resolve the on-disk path for a (pid, page): DB file_path first, else scan."""
    if file_path and os.path.isfile(file_path):
        return file_path
    return path_index.get((str(pid), int(page_index)))


def _cmd_verify_files(args) -> int:
    from app.core.image_integrity import validate_image_file
    from app.core.metadata_db import MetadataDB
    base = _base_path()
    download_path = str(_store().get_section("download").get("path") or "").strip()
    db = MetadataDB(base)
    try:
        pages = db.get_downloaded_pages()
        path_index, part_files = _build_page_path_index(download_path)
        ok = truncated = missing = 0
        bad_pages: list[tuple[str, int]] = []
        for pid, page_index, url, file_path in pages:
            resolved = _resolve_page_file(pid, page_index, url, file_path, path_index)
            if not resolved or not os.path.isfile(resolved):
                missing += 1
                bad_pages.append((str(pid), int(page_index)))
                continue
            fmt = _fmt_from_path_or_url(resolved, url)
            valid, _reason = validate_image_file(resolved, fmt)
            if valid:
                ok += 1
            else:
                truncated += 1
                bad_pages.append((str(pid), int(page_index)))
                if args.fix:
                    with contextlib.suppress(Exception):
                        os.remove(resolved)
        reset = 0
        if args.fix and bad_pages:
            for pid, page_index in bad_pages:
                db.mark_page_pending(pid, page_index)
            reset = len(bad_pages)
        # Stale .part temp files (force-kill mid-stream leaves the atomic-write
        # temp behind; each run uses a new timetag name so they accumulate).
        removed_part_files = 0
        if args.fix:
            for pf in part_files:
                with contextlib.suppress(Exception):
                    os.remove(pf)
                    removed_part_files += 1
    finally:
        db.close()
    payload = {
        "downloaded_pages": len(pages),
        "ok": ok,
        "truncated": truncated,
        "missing": missing,
        "reset": reset,
        "part_files": len(part_files),
        "removed_part_files": removed_part_files,
        "download_path": download_path,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(
            f"已下載頁數={payload['downloaded_pages']} 完整(ok)={ok} "
            f"截斷(truncated)={truncated} 遺失(missing)={missing} "
            f"已重設待下載(reset)={reset} "
            f"殘留.part={len(part_files)} 已清除.part={removed_part_files}",
            file=sys.stderr,
        )
    return 0


def _cmd_run(args) -> int:
    from app.cli.headless_runner import run_headless
    return run_headless(args.step, force_rescan=bool(getattr(args, "force_rescan", False)))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pixiv-cli", description="Pixiv downloader headless CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="run a pipeline step / all / combined")
    pr.add_argument("--step", required=True,
                    choices=["1", "2", "3", "4", "combined", "all"])
    pr.add_argument("--force-rescan", action="store_true",
                    help="Step 2 only: ignore the 30-day skip and re-scan all "
                         "artists to backfill author user_id")
    pr.set_defaults(func=_cmd_run)

    ps = sub.add_parser("status", help="print DB status")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=_cmd_status)

    pc = sub.add_parser("config", help="get/set a settings field")
    pc.add_argument("action", choices=["get", "set"])
    pc.add_argument("key", help="<section>.<field>, e.g. download.like_num")
    pc.add_argument("value", nargs="?", help="value for 'set'")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=_cmd_config)

    pk = sub.add_parser("cookie", help="cookie utilities")
    ksub = pk.add_subparsers(dest="cookie_cmd", required=True)
    pkt = ksub.add_parser("test", help="test configured cookies")
    pkt.add_argument("--json", action="store_true")
    pkt.set_defaults(func=_cmd_cookie_test)

    pv = sub.add_parser("verify-files",
                        help="scan downloaded pages for truncated/missing files")
    pv.add_argument("--fix", action="store_true",
                    help="re-queue truncated/missing pages (mark pending) and "
                         "delete truncated files")
    pv.add_argument("--json", action="store_true")
    pv.set_defaults(func=_cmd_verify_files)

    pf = sub.add_parser("following", help="following utilities")
    fsub = pf.add_subparsers(dest="following_cmd", required=True)
    pfe = fsub.add_parser("export", help="print the following list")
    pfe.add_argument("--json", action="store_true")
    pfe.set_defaults(func=_cmd_following_export)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "config" and args.action == "set" and args.value is None:
        print("config set requires a value", file=sys.stderr)
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
