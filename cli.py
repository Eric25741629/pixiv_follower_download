"""Thin entry point so `python cli.py ...` works from the repo root."""
from app.cli.commands import main

if __name__ == "__main__":
    raise SystemExit(main())
