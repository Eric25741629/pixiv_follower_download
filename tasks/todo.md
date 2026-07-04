---
goal: 設定頁「儲存設定」改為浮動未儲存提示列 + 路徑欄位可直接輸入/貼上
started: 2026-07-04
status: in_progress
branch: worktree-settings-unsaved-bar (worktree 隔離)
---

# 規劃:設定頁浮動儲存列 + 路徑可輸入

## 需求(使用者原話拆解)
1. 「儲存設定」按鈕改成浮動的:偵測到有修改時,在視窗下方彈出浮動列,
   提示使用者還有設定尚未儲存(含儲存按鈕)。
2. 下載路徑 / cjxl 路徑欄位要可以直接輸入、複製貼上,不是只能用資料夾選擇器。

## 設計決策
- 浮動列放在 SettingsView 內部:`build()` 改回傳
  `ft.Stack([原 Column, 浮動列], expand=True, fit=ft.StackFit.EXPAND)`。
  非定位子(Column)在 EXPAND fit 下吃滿空間,scroll 不變;浮動列用
  `bottom=16, left=0, right=0` 定位 + 內部置中。只在設定頁可見
  (`_activate_view` 只切根控件 visible),不用碰 flet_app/page.overlay。
- dirty 偵測:switch 與語言下拉「已經即時 autosave」→ 不算 dirty。
  其餘明確儲存的控件(text fields / dropdowns / sliders / tag 增刪 /
  picker 寫回)全部掛 `_mark_dirty`。`save()` 結尾 `_clear_dirty()`。
- 底部原本常駐的儲存按鈕移除,儲存按鈕只活在浮動列(= 使用者要的
  「儲存設定改成浮動的」)。
- 設計系統:新增 `components/layout.py: unsaved_bar(theme, *, text, save_button)`
  factory(glass_panel 包 Row[Text, 按鈕]),`__init__.py` 再匯出,
  按鈕用既有 `c.primary_button`。
- 路徑欄位:拿掉 `read_only=True` 即可輸入;`save()` 對 path 做最小
  正規化(strip 空白 + 去除前後引號,Explorer 複製路徑常帶引號)。
  下游 `thread_download` 用 `os.path.join` + `os.makedirs(exist_ok=True)`,
  不依賴結尾斜線 → 不做存在性驗證(deliberate skip;打錯字會在執行時紅字報錯)。

## Todo
- [x] i18n:zh-TW / en 加 `settings.unsaved`
- [x] components/layout.py:`unsaved_bar` factory + `__init__.py` 匯出
- [x] settings_view:dirty 追蹤(_mark_dirty/_clear_dirty/_unsaved_bar)
- [x] settings_view:所有明確儲存控件掛 on_change/on_select;cooldown
      兩個既有 handler 結尾補 _mark_dirty;tag 增刪、兩個 picker 補
- [x] settings_view:`_tf_path` / `_tf_jxl_path` 拿掉 read_only;save() 路徑正規化
- [x] settings_view:build() 改 Stack、移除底部儲存按鈕
- [x] 測試:test_settings_unsaved_bar.py(新)、test_ui_components.py(factory)、
      修 test_settings_view_layout.py 的 _tile_titles(root 變 Stack)
- [x] 全套 pytest + ruff check app/ + 兩個 UI 守門測試綠
- [x] Review(ui-design-system-review)+ commit(worktree 分支)

## Review
- 實作:Opus subagent;複查:Fable(diff 逐項對 spec + ui-design-system-review checklist)。
- 驗證:pixiv_env 全套 pytest 1086 passed / 1 skipped(integration);ruff 對改動檔全過
  (app/ 整體 142 個 baseline 違規為既有,本次零新增)。
- 掛點確認:19 個 text/number/multiline field、4 個 dropdown(on_select)、jxl slider、
  cooldown 兩 handler、tag 增刪(有實際變動才標)、兩個 picker、UA 偵測;switch 與語言
  下拉維持即時 autosave 不標 dirty。save() 寫入後清 dirty(cooldown<30 取消儲存則保留)。
- 已知殘留:真機目視驗證(浮動列彈出/消失、貼上帶引號路徑被清)待使用者在真 app 確認 —
  綠測試不等於 GUI 驗收(lessons)。
- status: done(2026-07-04,worktree 分支 worktree-settings-unsaved-bar)
