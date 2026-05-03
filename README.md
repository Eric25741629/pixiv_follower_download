# pixiv-img-download

Pixiv desktop/web downloader rebuilt with **Flet (Material 3)**.

Chinese README: [README.zh-TW.md](README.zh-TW.md)

## Features

- 4-step pipeline: Step 1 (following) → Step 2 (PIDs) → Step 3 (URLs) → Step 4 (download)
- one-click `Run All` pipeline
- pause / resume / stop per step
- multi-account cookie pool with alias support and per-cookie validity testing
- `AccountScheduler`: round-robin cooldown (`avg × ln(N+1)` s) with per-cookie proxy binding
- tag include/exclude filter (chip UI), min-likes threshold (normal / R18)
- directory organisation by author ID, R18/R18G/AI subdirs
- optional JXL post-processing via `cjxl.exe`
- statistics view: download counts + per-cookie bar chart
- network retry (5 attempts, 60 s wait) for Steps 2/3/4 on proxy/connection errors
- atomic writes with up to 10 rolling history backups

## Requirements

- Windows 10/11, Python 3.8+

```bash
pip install flet "requests[socks]"
```

## Run

Desktop:

```bash
python main.py
```

Web (browser):

```bash
flet run app/gui/flet_app.py --web
```

## Typical Workflow

1. Set account / User ID, download path, and filter rules in the **Settings** tab.
2. Add and test Cookie strings in the **Cookie** tab.
3. On the **Home** tab, run steps 1–4 in order, or click **Run All**.

## Data Location

Runtime state under `%APPDATA%/pixiv_download/` (`othersettings.json`, `cookies.json`, `pictures_id.txt`, `pixiv_info_cache.json`). Backup copies go to a sibling `history/` directory as `filename.YYYYMMDD(.N)`; latest 10 kept.

## Notes

- Respect Pixiv terms of service and local laws.
- Valid login cookies are required for restricted content.
- If rate-limited, raise the cooldown average (≥ 30 s recommended) or enable single-thread PID mode.

