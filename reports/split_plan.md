# Complexity Baseline & Future Split Plan

Captured: Phase 24 baseline (2026-04-25)

## File-level rating (radon mi)

| File | MI | Status |
|---|---|---|
| `app/core/pixiv_api.py` | B | acceptable |
| `app/core/pixiv_thread_utils.py` | B | acceptable |
| `app/core/thread_pid_scan.py` | B | acceptable |
| `app/core/thread_download.py` | **C** | needs refactor (was higher pre-Phase 18-22) |
| `app/core/thread_url_fetch.py` | **C** | needs refactor |

(Several files un-rated due to UTF-8 BOM — see Phase 25 BOM cleanup.)

## Top complexity offenders (radon cc, ≥ C grade)

| Func | File | CC | Grade |
|---|---|---|---|
| `get_download_url` | thread_url_fetch.py | 52 | **F** |
| `gif_download` | thread_download.py | 37 | **E** |
| `_convert_file_to_jxl` | thread_download.py | 34 | **E** |
| `Pixiv_info` | pixiv_api.py | 31 | **E** |
| `_commit_step2_outputs` | thread_pid_scan.py | 30 | D |
| `thread_no_use_seleium_get_pid` | thread_pid_scan.py | 30 | D |
| `get_download_url` | pixiv_api.py | 29 | D |
| `check_exist` | thread_url_fetch.py | 26 | D |
| `_finalize_on_complete` | thread_url_fetch.py | 26 | D |
| `_finalize_downloads` | thread_download.py | 25 | D |
| `jpg_download` | thread_download.py | 24 | D |
| `load_data` (Userdata_controller) | user_info.py | 23 | D |
| `setinfo` | user_info.py | 22 | D |
| `normalize_cookie_entries` | pixiv_thread_utils.py | 22 | D |
| `_load_saved_cookie_requirement_map` | thread_url_fetch.py | 21 | D |

## Files exceeding 500 lines

| File | Lines | Note |
|---|---|---|
| `app/core/thread_download.py` | ~1912 | Phase 18-22 reduced from 1946 |
| `app/core/thread_url_fetch.py` | 1610 | untouched in Phase 18-22 |
| `app/gui/controller.py` | 1672 | god class — many `on_*_clicked` slots |
| `app/core/pixiv_api.py` | 939 | mixes Pixiv_info parsing + scraper helpers |
| `app/core/pixiv_thread_utils.py` | 659 | utility grab bag |

## Future split candidates

### thread_url_fetch.py (1610 lines, MI=C)

- `get_download_url` (CC=52, 142 lines, lines 1388-1609) — must be decomposed:
  fetch URL + handle response + extract URL list + retry on cookie + persist diagnostics
- `check_exist` (CC=26, 97 lines) — file scan + meta merge + diff compute
- Consider: extract a `Step3Pipeline` helper class

### thread_download.py (still 1912 lines)

- `gif_download` (CC=37, 139 lines) and `jpg_download` (CC=24, 91 lines) share a
  parallel structure: fetch → metadata → cookie selection → headers → stream → save.
  Candidate: extract `_fetch_pixiv_artwork_image(pid, url, kind)` + a media-specific writer
- `_convert_file_to_jxl` (CC=34, 92 lines) — ext gating, retry logic, success/fail accounting,
  log emission. Candidate: split into validate, convert, accounting

### pixiv_api.py (939 lines)

- `Pixiv_info` (CC=31, 173 lines) and its inner `_parse_payload` (CC=32, 56 lines) —
  large response parsing. Candidate: split into HTTP fetch + JSON shape adapters.
- `get_download_url` (CC=29, 55 lines) — cookie/non-cookie branches duplicated

### controller.py (1672 lines)

- Many `on_*_clicked` slots — natural candidate to extract behaviour into separate
  facade objects (e.g., `CookiePoolController`, `WindowChromeController`).
- `_apply_window_visual_style` is 103 lines doing pure styling — could become a
  module-level constant + small applier.

## Recommended Phase candidates (NOT scheduled here)

| ID | Target | Goal |
|---|---|---|
| P-α | `thread_url_fetch.get_download_url` | F → D within 2 commits |
| P-β | `thread_download.gif_download` + `jpg_download` | extract shared fetch helper; both → C |
| P-γ | `pixiv_api.Pixiv_info` | extract `_parse_payload` shape adapters; → C |
| P-δ | `_write_all_url_file` | lift to `pixiv_thread_utils.py` (56 lines duplicated) |
| P-ε | UTF-8 BOM stripping | radon currently can't parse 7 files |

## Numbers to beat (next baseline)

After Phase 26, re-run:

```bash
radon cc app/ -n C -s
radon mi app/ -n B
lizard -C 15 -L 100 app/
```

The main metric: number of E/F-graded functions (currently 4) should drop or stay flat.
