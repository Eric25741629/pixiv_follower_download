# pixiv-img-download

Pixiv desktop downloader (PyQt5 GUI) for:

- fetching followed artists
- fetching artwork IDs
- fetching artwork detail/URLs
- downloading images/ugoira files

## Features

- GUI workflow with step-by-step actions (`Step 1` -> `Step 4`)
- one-click pipeline run (`Run All`)
- pause / resume / stop for long-running tasks
- tag include/exclude filtering
- minimum likes filter
- skip gif / skip tag / skip by time options
- auto-save runtime state (cookies/settings/history files)

## Project Structure

- `main.py`: app entry point
- `controller.py`: UI/controller layer
- `run_actions.py`: workflow action orchestration
- `pixiv_thread.py`: threaded fetch/download jobs
- `pixiv_api.py`: Pixiv request/parsing and cookie helpers
- `download_img.py`, `download_url.py`: download utility logic
- `safe_io.py`: atomic write + optional file history backup
- `Ui2.py` / `test.ui`: PyQt UI code/layout

## Requirements

Recommended environment:

- Windows 10/11
- Python 3.8+
- Chrome + compatible ChromeDriver (for Selenium login flows if needed)

Core Python packages used in this project include:

- `PyQt5`
- `requests`
- `beautifulsoup4`
- `selenium`
- `numpy`
- `tqdm`
- `imageio`
- `loguru`
- `gdown`

Install example:

```bash
pip install PyQt5 requests beautifulsoup4 selenium numpy tqdm imageio loguru gdown
```

## Run

```bash
python main.py
```

## Typical Workflow

1. Configure account/cookies and download path in GUI.
2. Run `Step 1: Get Following Artists`.
3. Run `Step 2: Get Artwork IDs`.
4. Run `Step 3: Get Artwork Details`.
5. Run `Step 4: Start Download`.

Or click `Run All (1->4)`.

## Data and Backup

Runtime files are usually under `%APPDATA%/pixiv_download/`.

When `atomic_write_*` is used with backup enabled, old versions are copied to a sibling `history/` directory.

- naming format: `filename.YYYYMMDD(.N)`
- keep latest 10 backup files by default
- some files (for example `cookies.json`) may be written with backup disabled

## Notes

- Respect Pixiv terms of service and local laws.
- For private/restricted content, valid cookies may be required.
- If rate-limited, enable single-thread mode and increase wait range in UI settings.

