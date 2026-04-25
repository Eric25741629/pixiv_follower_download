# Findings

## Cookie 重複位置確認

### Module-level in pixiv_thread.py
- `_normalize_cookie_entries` line 44–78
- `_normalize_cookie_pool` line 81–82 (呼叫上面那個)
- `_cookie_usage_label` line 85–101
- `_format_cookie_usage_summary` line 104–125

### Per-class cookie methods (重複 2–3 次)
| 方法 | 位置 |
|---|---|
| `_cookie_speed_divisor` | get_pixiv_author_imgID_Thread:420, get_img_url_thread:1462, download_thread:3056 |
| `_apply_cookie_pool_speedup` | get_pixiv_author_imgID_Thread:433, get_img_url_thread:1475 |
| `_cookie_alias_for_value` | get_img_url_thread:1487（等同 module-level `_cookie_usage_label`）|

### __init__ cookie 初始化 (3 處相同的 4 行)
```python
self.cookie_entries = _normalize_cookie_entries(cookies)
self.cookie_pool = [x.get("cookie","") for x in self.cookie_entries ...]
self._cookie_alias_map = {str(x.get("cookie",""))...: ...}
self.cookies = self.cookie_pool[0] if self.cookie_pool else str(cookies or "").strip()
```
出現在：
- get_pixiv_author_imgID_Thread.__init__ line 348
- get_img_url_thread.__init__ line 933
- download_thread.__init__ line 2579

### GUI 層重複
- `user_info.py:488` `_normalize_cookie_entries` instance method（改用 utils 版本即可）
- `controller.py:103` `_normalize_cookie_pool` instance method（改用 utils 版本即可）

## pause/resume/stop 重複位置

| class | pause | resume | stop | flush hook? |
|---|---|---|---|---|
| get_following | 235 | 240 | 244 | _flush_following_snapshot |
| get_pixiv_author_imgID_Thread | 389 | 393 | 397 | 無 |
| get_img_url_thread | 1775 | 1780 | 1784 | _flush_url_meta_snapshot |
| download_thread | 4461 | 4465 | 4469 | stop() 邏輯複雜，需 override |
| test_thread | 4506 | 4510 | 無 | QWaitCondition 不同，不納入 |

## _sleep_with_countdown 位置
- `get_pixiv_author_imgID_Thread:401` — 完整實作
- `get_img_url_thread:1424` — 叫 `_sleep_ultra_slow`，logic 略有不同（有 cache_hit 判斷、nocookie 分支）
  → 只把 get_pixiv_author_imgID_Thread 的移到 base class，get_img_url_thread 的保留（邏輯不同）

## pixiv_thread_utils.py 現有 imports
`datetime, json, os, re, shutil, sys, traceback`
→ 加入 cookie functions 不需額外 import
