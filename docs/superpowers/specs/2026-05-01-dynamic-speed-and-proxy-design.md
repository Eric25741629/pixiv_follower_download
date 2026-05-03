# Dynamic Speed Multiplier + Per-Account Cooldown + Per-Account Proxy Binding

Date: 2026-05-01
Status: Approved (brainstorming complete)

## Goal

引入三項相關功能：
1. **單一可動冷卻參數**：使用者在設定頁拖一個 slider 即時調整「單帳號平均冷卻秒數」，執行中的 worker thread 立即生效。
2. **Per-account cooldown 模型**：每個 cookie 有獨立的冷卻計時器，單線程依「最早可用」原則 round-robin。
3. **Per-account proxy binding**：每個 cookie 在 cookies 頁手動配對一個 proxy（或本機 IP）；同帳號永遠走同 IP，破壞此契約即為設計錯誤。

## 非目標 (Non-Goals)

- 不做多線程並發（使用者明確要求單線程）。
- 不在新模型中引入 fallback-to-direct 行為（會破壞同帳號同 IP 契約）。
- 不重做 cookies 池儲存格式（沿用 `cookies_entries` / `cookies_aliases`）。
- 不重做 Step 1 (`thread_following`) — Step 1 只跑一次、用主 cookie，不進新模型。

## 影響範圍

| 模組 | 變更 |
|---|---|
| `app/core/account_scheduler.py` | **新增** — round-robin 排程器 |
| `app/core/proxy_utils.py` | **新增** — proxy URL 解析、`requests` proxies dict 構造 |
| `app/core/settings_store.py` | 新欄位、舊欄位遷移 |
| `app/core/pixiv_thread_base.py` | 接入 scheduler |
| `app/core/thread_pid_scan.py` | Step 2 改用 scheduler |
| `app/core/thread_url_fetch.py` | Step 3 改用 scheduler |
| `app/core/thread_download.py` | Step 4 改用 scheduler，移除 `cookie_speed_divisor` 用法 |
| `app/core/pixiv_api.py` | 主要 fetch 函式接受可選 `session` 參數，session 帶 proxy |
| `app/core/thread_following.py` | **不變**（Step 1 不進新模型） |
| `app/gui/views/settings_view.py` | 移除 min/max，加冷卻 slider + proxy 多行框 |
| `app/gui/views/cookies_view.py` | 加 proxy 綁定 dropdown column |
| `tests/test_account_scheduler.py` | **新增** |
| `tests/test_proxy_utils.py` | **新增** |
| `tests/test_cookie_proxy_binding.py` | **新增** |
| `tests/test_speed_settings.py` | **新增** |
| `tests/test_proxy_live.py` | **新增**，標 `@pytest.mark.integration` |

## 架構

### AccountScheduler (`app/core/account_scheduler.py`)

純狀態機，不打網路。

```python
@dataclass
class AccountState:
    cookie: str
    alias: str
    proxy_url: str | None      # None = 用本機 IP
    cooldown_until: float = 0.0  # time.monotonic() 時間戳
    disabled_reason: str | None = None  # 'proxy_dead' / None

class AccountScheduler:
    def __init__(
        self,
        accounts: list[AccountState],
        get_cooldown_avg: Callable[[], float],  # live read settings
        pause_event: threading.Event,
        stop_event: threading.Event,
        emit: Callable[[str], None] | None = None,  # 紅字 log
    ): ...

    def acquire(self) -> AccountState | None:
        """阻塞直到有可用 account，回傳；遇 stop_event 回傳 None。
        遇 pause_event clear 時阻塞，set 時恢復。"""

    def release(self, account: AccountState, ok: bool = True) -> None:
        """ok=True：cooldown_until = now + jittered_cooldown()
        ok=False：disable(account, ...)"""

    def disable(self, account: AccountState, reason: str) -> None:
        """整輪禁用該 cookie；emit 紅字 log。"""

    def jittered_cooldown(self) -> float:
        """avg = get_cooldown_avg(); randint(avg*0.7, avg*1.3)"""

    def average_cooldown(self) -> float:
        """純 read-only，給 UI 預覽。"""

    def all_disabled(self) -> bool:
        ...
```

**單線程語意**：scheduler 內用 `threading.Lock` 保護 `cooldown_until` map；`acquire` 內輪詢（每 0.5s 喚醒一次檢查 stop/pause）。

**等待策略**：找到「最早可用」account → 若已可用立即回傳；否則 sleep 到 `min(cooldown_until)`（同樣 0.5s 粒度，能被 stop/pause 打斷）。

**「冷卻時間」定義**：`avg` 從 settings.json 的 `performance.pid_cooldown_avg` 即時讀取（每次 `release` 才讀，所以 settings 改了下一個 release 開始生效）。

### Proxy 解析 (`app/core/proxy_utils.py`)

```python
def parse_proxy_url(raw: str) -> str | None:
    """正規化 proxy URL；空字串/格式錯誤回 None。
    支援 http://, https://, socks5://, socks5h://, socks4://"""

def to_requests_proxies(proxy_url: str | None) -> dict | None:
    """proxy_url=None → None（直連）
    其他 → {'http': proxy_url, 'https': proxy_url}"""

def parse_proxy_list(text: str) -> list[str]:
    """多行文字 → list[str]，去空行/註解（# 開頭）/重複。"""

def test_proxy(proxy_url: str | None, timeout: int = 10) -> tuple[bool, str]:
    """同步 GET https://www.pixiv.net 確認連通；回 (ok, message)。"""
```

`requests[socks]` 套件依賴：`requirements.txt` 加上 `requests[socks]>=2.31`。

### Worker 整合

**`pixiv_thread_base.py`**：
- `PauseableThread.__init__` 新增 `scheduler: AccountScheduler | None = None`。
- 提供 `_acquire_account()` / `_release_account(acc, ok)` 方法。
- `_sleep_with_countdown(delay)` 維持原樣（仍給 Step 1 / 同 PID 內等待用）。

**Step 2 / 3 / 4**：
1. 進入主 loop 前由 caller 注入 scheduler。
2. 每個工作單位（一個 author / 一個 PID）開頭：
   ```python
   acc = self._acquire_account()
   if acc is None:  # stop
       break
   ```
3. 用 `acc.cookie` 做 cookie 字串、`to_requests_proxies(acc.proxy_url)` 做 proxies kwarg。
4. 工作完成：`self._release_account(acc, ok=True)`。
5. 工作失敗 ProxyError/ConnectError：`self._release_account(acc, ok=False)` 並用 `scheduler.disable(acc, 'proxy_dead')`。
6. 同 PID 多頁仍走 `_sleep_within_pid`（1~3 秒禮貌延遲），這段不算冷卻。

**`thread_download` 移除**：
- `cookie_speed_divisor` 與 `apply_cookie_pool_speedup` 的用法
- `_calc_sleep_delay` 的 sqrt(N) 加速與 no_cookie 半速分支
- 改為單一 path：所有 PID 間的等待全由 scheduler 處理；intra-PID 等待沿用 1~3 秒（暫時硬編，後續可擴展為設定）

**`pixiv_api.py`**：
- 引入模組級 helper：
   ```python
   def make_session(proxy_url: str | None = None) -> requests.Session:
       sess = requests.Session()
       sess.headers.update({'User-Agent': ...})
       proxies = to_requests_proxies(proxy_url)
       if proxies:
           sess.proxies.update(proxies)
       return sess
   ```
- 主要 fetch 函式（`Pixiv_info`, `get_pixiv_cookie_requirement`, `Test_cookies` 等）新增 `session: requests.Session | None = None`，None 則用模組級無 proxy session（向後相容）。
- 不改現有函式簽名前面的位置參數，新增 `session=` 為純關鍵字後置參數。

### Settings 變更

```json
"performance": {
  "single_thread_mode": true,
  "pid_cooldown_avg": 35,        // 新；range 5 ~ 300
  "pid_cooldown_jitter": 0.3     // 新；±30%（不開放編輯，預設常數）
},
"auth": {
  // 既有欄位 ...
  "proxy_pool": [],              // 新；list[str]
  "cookie_proxy_map": {}         // 新；dict[cookie_str, proxy_url | null]
}
```

`SettingsStore.DEFAULTS` 補上預設。

**遷移**：
```python
# settings_store.py 在 _merge_defaults 之後跑
perf = merged.get("performance", {})
if "pid_cooldown_avg" not in perf and "pid_wait_min" in perf:
    avg = (int(perf.get("pid_wait_min", 10)) + int(perf.get("pid_wait_max", 60))) // 2
    perf["pid_cooldown_avg"] = max(5, min(300, avg))
    # 不刪舊欄位以保留回退能力；後續版本再清
```

### UI 變更

**`settings_view.py`**：

1. 「下載設定」tile 改名「冷卻設定」：
   - 移除 `_tf_dl_wait_min` / `_tf_dl_wait_max`。
   - 新增 `_sl_cooldown` (`ft.Slider`, min=5, max=300, divisions=59) + `_tf_cooldown` (`ft.TextField`)；`on_change` 雙向同步。
   - 下方一行 `_label_cooldown_hint`：`「相當於倍率 X.Xx；推薦 ≥ 30 秒」`，`< 30` 時改紅色。
   - `save()` 在寫入前若 `< 30` 跳 `AlertDialog`，取消則 cooldown 不寫入。
   - `single_thread_mode` switch 維持。

2. 新增「Proxy 設定」tile：
   - `_tf_proxy_pool` (`ft.TextField`, multiline, min_lines=4, max_lines=20)；
     placeholder 範例：
     ```
     # 一行一個 proxy
     http://1.2.3.4:8080
     socks5://user:pass@host:1080
     ```
   - `_btn_test_proxies`：對每行 proxy 跑 `proxy_utils.test_proxy()`；結果用 `Column` 條列 ✓/✗ 顯示。
   - 儲存時呼叫 `parse_proxy_list()` 過濾、寫入 `auth.proxy_pool`。
   - 若有 cookie 綁定的 proxy 不在新 pool，顯示警告（不自動清除綁定，由使用者去 cookies 頁修）。

**`cookies_view.py`**：

1. `DataTable` 新增第 5 column：`「Proxy 綁定」`。
2. 每列 cell 改為 `ft.Dropdown`，options：
   - `Dropdown.option(key="", text="（本機 IP）")`
   - 對 `proxy_pool` 中每個 proxy 加一個 option
   - `on_change` 寫入 `cookie_proxy_map`、call `_save_entries()`
3. Header 新增按鈕「自動配對」：把 N 個 cookie 依序對到前 N 個 proxy（多餘的 proxy 留空、多餘的 cookie 配「本機 IP」）。

**主畫面 `main_view.py`**：不動。`run_controller` 啟動 thread 前改成從 settings 構造 `AccountScheduler` 並注入。

## 失敗處理

| 情況 | 行為 |
|---|---|
| ProxyError / ConnectError / ReadTimeout（HTTP 層連不通） | `release(ok=False)` → `disable(account, 'proxy_dead')`；紅字 log；該工作換下一個 account 重試一次 |
| 重試後仍失敗 | 跳過該 PID，紅字 log 寫入 `pictures_id.txt` 待重跑 |
| HTTP 403 / login redirect | 沿用現有 cookie 失效處理（不 disable）；該 PID 用同 cookie 嘗試一次 retry-with-cookie 流程 |
| 所有 account 都 disabled | 紅字 log「全部 cookie 都已禁用，任務停止」；emit `WorkerEvent("error", ...)`；worker 結束 |
| 速度倍率輸入 ≤ 0 或 NaN | 強制改成預設 35 + 黃字警告 |
| Cookie 沒在 `cookie_proxy_map` 裡 | 視為綁本機 IP（合法）；log info 字樣 |

## Data Flow

```
[settings.json]
   pid_cooldown_avg ─────┐
                         ▼
        [AccountScheduler]  ◄─── live read
              ▲   │
              │   ▼ acquire / release
[run_controller] [worker thread]
              │   │
              │   ▼ requests.get(..., proxies=acc.proxies, headers={Cookie: acc.cookie})
              │   │
              ▼   ▼
         [event_q] ──► dispatcher ──► UI
```

## 測試計畫

### Unit (`@pytest.mark` 預設)

`tests/test_account_scheduler.py`:
- `test_acquire_returns_first_available`
- `test_release_sets_cooldown_with_avg`
- `test_acquire_blocks_until_cooldown_expires`（用 monotonic mock 或 small avg）
- `test_disabled_account_skipped`
- `test_speed_change_takes_effect_next_acquire`（改 settings → 下一輪 release 用新值）
- `test_all_disabled_returns_none`
- `test_acquire_returns_none_on_stop_event`
- `test_acquire_blocks_on_pause_event`
- `test_jitter_within_30_percent`

`tests/test_proxy_utils.py`:
- `test_parse_proxy_url_http`
- `test_parse_proxy_url_socks5`
- `test_parse_proxy_url_socks5h`
- `test_parse_proxy_url_with_auth`
- `test_parse_proxy_url_invalid_returns_none`
- `test_to_requests_proxies_none_passes_through`
- `test_parse_proxy_list_strips_comments_and_blanks`
- `test_parse_proxy_list_dedupes`

`tests/test_cookie_proxy_binding.py`:
- `test_cookie_with_proxy_uses_bound_proxy`
- `test_cookie_without_proxy_uses_direct`
- `test_auto_pair_assigns_in_order`
- `test_auto_pair_excess_cookies_get_local`
- `test_proxy_removed_from_pool_keeps_binding_with_warning`

`tests/test_speed_settings.py`:
- `test_settings_store_round_trip_pid_cooldown_avg`
- `test_settings_store_migrates_old_min_max_to_avg`
- `test_settings_store_default_when_neither_present`
- `test_save_below_30_warns_but_persists_after_confirm`（GUI 整合 -- 用 mock dialog）

### Integration (`@pytest.mark.integration`)

`tests/test_proxy_live.py`:
- `test_requests_with_socks5_proxy_reaches_pixiv`（需要環境變數 `PIXIV_TEST_PROXY_URL`）
- `test_requests_dead_proxy_raises_proxy_error`

### 手動驗證

1. 啟動 app，設定頁拖 cooldown slider 從 35 → 10 → 60，確認下方提示同步、< 30 跳警告。
2. cookies 頁綁定每個 cookie 一個不同 proxy，按「自動配對」確認分配正確。
3. Step 2 跑短時間（5 個 author），log 出現「acquire account=Cookie1 proxy=...」、「release after PID xxx」等訊息。
4. 故意填一個壞 proxy（`http://0.0.0.0:1`），確認該 cookie 在第一次失敗後紅字 log + disabled，後續流量不再使用該 cookie。

## 工作分割（給 writing-plans）

依相依順序：

1. **Proxy utils** — 純函式，無 GUI 依賴，最先做。
2. **Settings store** — 新欄位 + 遷移 + 測試。
3. **Account scheduler** — 純邏輯，TDD。
4. **`pixiv_api.py` session 注入** — 加 `session=` 參數但不影響既有 caller。
5. **Worker threads 整合** — 從 Step 4 開始（最大改動），再 Step 3、Step 2；最後跑 e2e 手動驗證。
6. **Settings UI** — slider + 警告對話框 + proxy 多行框。
7. **Cookies UI** — proxy dropdown column + 自動配對。
8. **移除 `cookie_speed_divisor` / `apply_cookie_pool_speedup` 用法** — 死碼清理；保留函式但加 deprecation 註解。
9. **Skill update** — 把這次學到的 Flet / threading 細節（如有）追加到 `.claude/skills/flet-0-84-pitfalls/SKILL.md`。

## 風險與緩解

| 風險 | 緩解 |
|---|---|
| `requests[socks]` 在某些 Windows 環境上裝不起來 | `parse_proxy_url('socks5://...')` 在 import 失敗時降級提示，並記錄到 log |
| AccountScheduler 死鎖（acquire 卡住） | 所有 wait 都是 0.5s 粒度且檢 stop_event；單元測試覆蓋 stop / pause 路徑 |
| Settings 並發讀寫 | `_acquire_account` 內每次 `release` 才讀一次 settings；不在 hot path 持鎖讀檔 |
| Step 4 多頁下載中途 release（同 PID 一頁失敗 → 後面頁怎麼辦） | 同 PID 多頁失敗一頁就放棄整個 PID，release(ok=False if ProxyError else True)；維持「PID 為原子工作單位」 |
| 使用者改 cookie pool 但忘記改 proxy 綁定 | settings 開啟時偵測；綁定的 proxy 不在 pool 內就 fallback 為本機 IP + log 警告 |
