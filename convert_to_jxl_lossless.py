import argparse
import subprocess
from pathlib import Path

DEFAULT_CJXL = r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe"
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def build_command(cjxl: Path, src: Path, dst: Path, effort: int) -> list[str]:
    ext = src.suffix.lower()
    cmd = [str(cjxl), str(src), str(dst), "--effort", str(effort)]

    # JPEG can use bit-exact lossless JPEG recompression.
    if ext in {".jpg", ".jpeg"}:
        cmd += ["--lossless_jpeg=1"]
    else:
        # Lossless for non-JPEG formats.
        cmd += ["--distance=0"]

    return cmd


def iter_sources(root: Path, recursive: bool):
    iterator = root.rglob("*") if recursive else root.glob("*")
    for p in iterator:
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            yield p


def convert_one(cjxl: Path, src: Path, effort: int, dry_run: bool) -> tuple[bool, str]:
    dst = src.with_suffix(".jxl")
    if dst.exists():
        return True, f"skip (exists): {dst}"

    cmd = build_command(cjxl, src, dst, effort)
    if dry_run:
        return True, f"dry-run: {' '.join(cmd)}"

    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:
        return False, f"error: {src} -> {exc}"

    if completed.returncode == 0 and dst.exists():
        return True, f"ok: {src} -> {dst}"

    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    reason = stderr if stderr else stdout
    if not reason:
        reason = f"cjxl exit code {completed.returncode}"
    return False, f"fail: {src} -> {reason}"


def main():
    parser = argparse.ArgumentParser(description="Batch convert images to lossless JXL via cjxl.")
    parser.add_argument("input_dir", help="Folder containing downloaded images")
    parser.add_argument("--cjxl", default=DEFAULT_CJXL, help="Path to cjxl.exe")
    parser.add_argument("--effort", type=int, default=7, help="cjxl effort 1~9 (default: 7)")
    parser.add_argument("--no-recursive", action="store_true", help="Only process top-level folder")
    parser.add_argument("--delete-original", action="store_true", help="Delete source file after successful conversion")
    parser.add_argument("--dry-run", action="store_true", help="Preview commands without running")
    args = parser.parse_args()

    root = Path(args.input_dir)
    cjxl = Path(args.cjxl)

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"input_dir not found: {root}")
    if not cjxl.exists() or not cjxl.is_file():
        raise SystemExit(f"cjxl not found: {cjxl}")

    recursive = not args.no_recursive
    effort = min(9, max(1, int(args.effort)))

    total = 0
    ok_count = 0
    fail_count = 0

    for src in iter_sources(root, recursive=recursive):
        total += 1
        ok, msg = convert_one(cjxl, src, effort=effort, dry_run=args.dry_run)
        print(msg)
        if ok:
            ok_count += 1
            if args.delete_original and (not args.dry_run):
                try:
                    src.unlink(missing_ok=True)
                    print(f"deleted: {src}")
                except Exception as exc:
                    print(f"warn: failed to delete {src}: {exc}")
        else:
            fail_count += 1

    print("=" * 60)
    print(f"total={total}, ok={ok_count}, fail={fail_count}, dry_run={args.dry_run}")
    print("note: GIF conversion depends on your cjxl build capabilities.")


if __name__ == "__main__":
    main()
