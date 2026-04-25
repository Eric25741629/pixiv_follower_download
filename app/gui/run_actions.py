from datetime import datetime
import re
import os
import json
import glob
from app.core import pixiv_thread
from app.core.pixiv_thread_utils import (
    append_diagnostic_event,
    sync_exist_pid_with_download_folder,
    invalidate_folder_file_count_cache,
)


def _find_default_cjxl_path():
    try:
        user_home = os.path.expanduser("~")
        found = glob.glob(os.path.join(user_home, "Downloads", "jxl*", "bin", "cjxl.exe"))
        if found:
            found = sorted(found, key=lambda x: os.path.getmtime(x), reverse=True)
            return found[0]
    except Exception:
        pass
    return "cjxl.exe"


def _get_single_mode(controller):
    try:
        return controller.ui.single_thread_mode.isChecked()
    except Exception:
        return False


def _get_wait_range(controller, default_min, default_max):
    try:
        wait_min = int(controller.ui.pid_wait_min.value())
        wait_max = int(controller.ui.pid_wait_max.value())
        return wait_min, wait_max
    except Exception:
        return default_min, default_max


def _get_wait_ranges(controller, default_cookie_min, default_cookie_max, default_no_cookie_min, default_no_cookie_max):
    cookie_min, cookie_max = _get_wait_range(controller, default_cookie_min, default_cookie_max)
    try:
        no_cookie_min = int(controller.ui.pid_wait_nocookie_min.value())
        no_cookie_max = int(controller.ui.pid_wait_nocookie_max.value())
    except Exception:
        no_cookie_min, no_cookie_max = default_no_cookie_min, default_no_cookie_max
    if no_cookie_min < 0:
        no_cookie_min = 0
    if no_cookie_max < no_cookie_min:
        no_cookie_max = no_cookie_min
    return cookie_min, cookie_max, no_cookie_min, no_cookie_max


def _get_cookie_payload(controller):
    try:
        getter = getattr(controller, "_get_cookie_entries", None)
        if callable(getter):
            entries = getter() or []
            normalized_entries = []
            seen = set()
            for item in entries:
                if not isinstance(item, dict):
                    continue
                cookie_text = str(item.get("cookie", "") or "").strip()
                alias_text = str(item.get("alias", "") or "").strip()
                if not cookie_text:
                    continue
                if cookie_text.lower().startswith("cookie:"):
                    cookie_text = cookie_text.split(":", 1)[1].strip()
                if not cookie_text or cookie_text in seen:
                    continue
                seen.add(cookie_text)
                normalized_entries.append({"cookie": cookie_text, "alias": alias_text})
            if normalized_entries:
                return normalized_entries
    except Exception:
        pass
    try:
        pool = list(getattr(controller, "cookies_pool", []) or [])
        pool = [str(x or "").strip() for x in pool if str(x or "").strip()]
        if pool:
            return pool
    except Exception:
        pass
    return getattr(controller, "cookies", "")


def _load_no_to_check(controller):
    no_to_check = []
    # Step2 提前跳過清單，讓 Step3 啟動前就直接排除。
    try:
        with open((controller.path + r"/step2_skip_pid.txt"), encoding="utf-8") as file:
            no_to_check += [line.rstrip() for line in file if line.rstrip()]
    except Exception:
        pass
    if controller.ui.pass_tag.isChecked():
        try:
            with open((controller.path + r"/tag_ban_pid.txt"), encoding="utf-8") as file:
                no_to_check += [line.rstrip() for line in file]
        except Exception:
            pass
    if controller.ui.pass_like.isChecked():
        try:
            with open((controller.path + r"/pid_num_pid.txt"), encoding="utf-8") as file:
                no_to_check += [line.rstrip() for line in file]
                no_to_check = list(set(no_to_check))
        except Exception:
            pass
    # Always exclude PIDs recorded as permanent low-like (like < 300)
    try:
        with open((controller.path + r"/like_less.txt"), encoding="utf-8") as file:
            for line in file:
                s = str(line).strip()
                if not s:
                    continue
                token = s.split()[0]
                m = re.match(r"^(\d+)", token)
                if m:
                    no_to_check.append(m.group(1))
                else:
                    no_to_check.append(token)
    except Exception:
        pass
    no_to_check = list(set(no_to_check))
    return no_to_check


def _get_jxl_options(controller):
    enable = False
    cjxl_path = _find_default_cjxl_path()
    delete_original = False
    effort = 7
    try:
        enable = bool(controller.ui.jxl_enable.isChecked())
    except Exception:
        pass
    try:
        ui_path = str(controller.ui.jxl_cjxl_path.text()).strip()
        if ui_path:
            cjxl_path = ui_path
    except Exception:
        pass
    try:
        delete_original = bool(controller.ui.jxl_delete_original.isChecked())
    except Exception:
        pass
    try:
        effort = int(controller.ui.jxl_effort.value())
    except Exception:
        pass
    # Fallback for UIs without JXL widgets: use persisted config if available.
    try:
        config_path = os.path.join(os.getenv("APPDATA") or "", "pixiv_download", "othersettings.json")
        if os.path.isfile(config_path):
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data, dict):
                if "jxl_enable" in data:
                    enable = bool(data.get("jxl_enable"))
                if str(data.get("jxl_cjxl_path", "")).strip():
                    cjxl_path = str(data.get("jxl_cjxl_path")).strip()
                if "jxl_delete_original" in data:
                    delete_original = bool(data.get("jxl_delete_original"))
                if "jxl_effort" in data:
                    effort = int(data.get("jxl_effort"))
    except Exception:
        pass
    if effort < 1:
        effort = 1
    if effort > 9:
        effort = 9
    return enable, cjxl_path, delete_original, effort


def _get_special_like_rules(controller):
    rules = []
    try:
        r18_like_num = int(controller.ui.r18_like_num.value())
    except Exception:
        r18_like_num = 0
    if r18_like_num > 0:
        rules.append({"label": "R-18", "tags": ["R-18", "R-18G"], "min_like": r18_like_num})

    for index in (1, 2):
        tag_widget = getattr(controller.ui, f"rule_tag_{index}", None)
        like_widget = getattr(controller.ui, f"rule_like_{index}", None)
        try:
            tag_text = str(tag_widget.text()).strip() if tag_widget is not None else ""
        except Exception:
            tag_text = ""
        try:
            like_value = int(like_widget.value()) if like_widget is not None else 0
        except Exception:
            like_value = 0
        if not tag_text or like_value <= 0:
            continue
        tags = [part.strip() for part in re.split(r"[\n,，、]+", tag_text) if part.strip()]
        if not tags:
            continue
        rules.append({"label": f"custom_{index}", "tags": tags, "min_like": like_value})
    return rules


def _get_ai_gen_dir(controller):
    try:
        return bool(controller.ui.ai_gen_dir.isChecked())
    except Exception:
        return False


def _set_countdown_output_mode(controller, enabled):
    try:
        controller._countdown_log_to_output = bool(enabled)
    except Exception:
        pass


def _connect_common(controller, thread, with_countdown=True, with_timechanged=False, with_thenext=False, with_finished=True):
    try:
        thread.finished.connect(controller._on_qthread_finished)
    except Exception:
        pass
    thread._signal.connect(controller.progress_changed)
    thread._output.connect(controller.add_output)
    if with_finished:
        thread._finished.connect(controller.notice)
    if with_countdown and hasattr(thread, "_countdown"):
        try:
            thread._countdown.connect(controller.update_countdown)
        except Exception:
            pass
    if with_timechanged and hasattr(thread, "_timechanged"):
        try:
            thread._timechanged.connect(controller.timechanged)
        except Exception:
            pass
    if with_thenext:
        thread._thenext.connect(controller.the_next)


def _sync_exist_pid_before_step34(controller):
    try:
        download_path = str(controller.ui.user_path1.text()).strip()
    except Exception:
        download_path = str(getattr(controller, "download_path", "") or "").strip()
    if not download_path:
        return
    try:
        result = sync_exist_pid_with_download_folder(
            controller.path,
            download_path,
            current_exist_pid=controller.exist_pid,
            recursive=True,
        )
        controller.exist_pid = result.get("merged_set", controller.exist_pid)
        cache_status = "[快取]" if result.get("used_cache") else "[掃描]"
        controller.add_output(
            "[exist_pid] {} 步驟 3/4 前遞迴掃描：檔案={}, PID={}, 新增={}".format(
                cache_status,
                result.get("scanned_files", 0),
                result.get("scanned_pid_count", 0),
                result.get("added_from_download", 0),
            )
        )
    except Exception as err:
        try:
            controller.add_output(
                f"<p><font color='orange'>[exist_pid] 遞迴掃描失敗：{err}</font></p>"
            )
        except Exception:
            pass


def _update_exist_pid_cache_after_download(controller):
    """
    下載完成後更新 exist_pid 快取。
    這會重新掃描下載資料夾並更新快取，確保下次執行時快取是最新的。
    """
    try:
        download_path = str(controller.ui.user_path1.text()).strip()
    except Exception:
        download_path = str(getattr(controller, "download_path", "") or "").strip()
    if not download_path:
        return
    try:
        # 使快取失效，強制下次重新掃描
        invalidate_folder_file_count_cache(controller.path, download_path)
        controller.add_output(
            "<p><font color='gray'>[exist_pid] 已清除快取，下次將重新掃描</font></p>"
        )
    except Exception as err:
        try:
            controller.add_output(
                f"<p><font color='orange'>[exist_pid] 清除快取失敗：{err}</font></p>"
            )
        except Exception:
            pass


def _diag(controller, event, **fields):
    try:
        base_path = str(getattr(controller, "path", "") or "").strip()
        append_diagnostic_event(base_path, event, stage="controller", **fields)
    except Exception:
        pass


def _existing_all_url_count(controller):
    try:
        base_path = str(getattr(controller, "path", "") or "").strip()
        if not base_path:
            base_path = os.path.join(os.getenv("APPDATA") or "", "pixiv_download")
        file_path = os.path.join(base_path, "all_url.txt")
        if not os.path.isfile(file_path):
            return 0
        count = 0
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if str(line).strip():
                    count += 1
        return count
    except Exception:
        return 0


def start_get_following(controller):
    controller.disable_button()
    controller.ui_cookies()
    _set_countdown_output_mode(controller, True)
    controller.ui.progressBar.reset()
    controller.log_start('步驟 1：抓取關注畫師')
    controller.thread1 = pixiv_thread.get_following(
        controller.userid, controller.cookies, controller.Agent, controller.ui.hidefollow
    )
    _connect_common(controller, controller.thread1, with_countdown=False)
    controller.thread1.start()
    controller.enable_thread_controls()


def start_get_pid(controller):
    controller.disable_button()
    controller.ui_cookies()
    _set_countdown_output_mode(controller, True)
    controller.log_start('步驟 2：抓取作品 PID')
    single_mode = _get_single_mode(controller)
    pid_wait_min, pid_wait_max = _get_wait_range(controller, 10, 60)
    controller.thread1 = pixiv_thread.get_pixiv_author_imgID_Thread(
        controller.Author_list,
        controller.Agent,
        controller.path,
        _get_cookie_payload(controller),
        controller.exist_pid,
        single_mode,
        pid_wait_min,
        pid_wait_max,
    )
    _connect_common(controller, controller.thread1)
    controller.thread1.start()
    controller.enable_thread_controls()


def start_get_url(controller):
    controller.ui_cookies()
    controller.disable_button()
    _set_countdown_output_mode(controller, True)
    try:
        if hasattr(controller, "reset_progress_state"):
            controller.reset_progress_state()
        else:
            controller.ui.progressBar.reset()
            controller.ui.progressBar.setValue(0)
    except Exception:
        pass
    _sync_exist_pid_before_step34(controller)
    controller.log_start('步驟 3：建立作品網址清單')
    no_to_check = _load_no_to_check(controller)
    single_mode = _get_single_mode(controller)
    pid_wait_min, pid_wait_max, pid_wait_nocookie_min, pid_wait_nocookie_max = _get_wait_ranges(controller, 10, 60, 1, 6)
    special_like_rules = _get_special_like_rules(controller)
    _diag(
        controller,
        "start_get_url",
        single_mode=bool(single_mode),
        like_min=int(controller.ui.like_num.value()),
        no_to_check_count=len(no_to_check),
        exist_pid_count=len(getattr(controller, "exist_pid", []) or []),
        special_like_rule_count=len(special_like_rules),
    )
    try:
        controller.thread1 = pixiv_thread.get_img_url_thread(
            Author_list=controller.Author_list,
            Agent=controller.Agent,
            cookies=_get_cookie_payload(controller),
            exist_pid=controller.exist_pid,
            ban_tag=controller.ban_tag,
            must_tag=controller.must_tag,
            like_num=controller.ui.like_num.value(),
            no_to_check=no_to_check,
            base_path=controller.path,
            single_thread_mode=single_mode,
            pid_wait_min=pid_wait_min,
            pid_wait_max=pid_wait_max,
            pid_wait_nocookie_min=pid_wait_nocookie_min,
            pid_wait_nocookie_max=pid_wait_nocookie_max,
            special_like_rules=special_like_rules,
        )
    except Exception as err:
        _diag(controller, "start_get_url_failed", error=str(err))
        controller.add_output(f"<p><font color='red'>[controller] 步驟 3 建立執行緒失敗：{err}</font></p>")
        try:
            controller.enable_button()
            controller.disable_thread_controls()
        except Exception:
            pass
        return
    _connect_common(controller, controller.thread1)
    controller.thread1.start()
    controller.enable_thread_controls()


def _connect_download_thread(controller, thread):
    """連接下載執行緒的信號，並在下載完成後更新 exist_pid 快取"""
    _connect_common(controller, thread, with_countdown=True, with_timechanged=True, with_finished=False)

    def _on_download_finished(message):
        controller.notice(message)
        if "下載完成" in str(message) or "Task finished" in str(message):
            _update_exist_pid_cache_after_download(controller)

    thread._finished.connect(_on_download_finished)


def start_download(controller):
    controller.ui_cookies()
    controller.disable_button()
    _set_countdown_output_mode(controller, True)
    _sync_exist_pid_before_step34(controller)
    try:
        controller.ui.progressBar.reset()
        controller.ui.progressBar.setValue(0)
    except Exception:
        pass
    controller.log_start('步驟 4：開始下載')
    single_mode = _get_single_mode(controller)
    pid_wait_min, pid_wait_max, pid_wait_nocookie_min, pid_wait_nocookie_max = _get_wait_ranges(controller, 1, 3, 0, 1)
    jxl_enable, jxl_cjxl_path, jxl_delete_original, jxl_effort = _get_jxl_options(controller)
    special_like_rules = _get_special_like_rules(controller)
    ai_gen_dir = _get_ai_gen_dir(controller)
    _diag(
        controller,
        "start_download",
        single_mode=bool(single_mode),
        like_min=int(controller.ui.like_num.value()),
        special_like_rule_count=len(special_like_rules),
        ai_gen_dir=bool(ai_gen_dir),
        jxl_enable=bool(jxl_enable),
        jxl_delete_original=bool(jxl_delete_original),
        jxl_effort=int(jxl_effort),
        ban_tag_count=len(list(getattr(controller, "ban_tag", []) or [])),
        must_tag_count=len(list(getattr(controller, "must_tag", []) or [])),
    )
    try:
        controller.thread1 = pixiv_thread.download_thread(
            nogif=controller.ui.nogif.isChecked(),
            notag=controller.ui.notag.isChecked(),
            notime=controller.ui.notime.isChecked(),
            create_dir=controller.ui.create_dir.isChecked(),
            download_path=controller.ui.user_path1.text(),
            cookies=_get_cookie_payload(controller),
            agent=controller.Agent,
            download_time=datetime.strptime(
                controller.ui.download_time.dateTime().toString("yyyy-MM-dd hh:mm:ss"),
                "%Y-%m-%d %H:%M:%S",
            ),
            no_R18G_dir=controller.ui.no_R18G_dir.isChecked(),
            single_thread_mode=single_mode,
            download_wait_min=pid_wait_min,
            download_wait_max=pid_wait_max,
            intra_pid_wait_min=pid_wait_nocookie_min,
            intra_pid_wait_max=pid_wait_nocookie_max,
            jxl_enable=jxl_enable,
            jxl_cjxl_path=jxl_cjxl_path,
            jxl_delete_original=jxl_delete_original,
            jxl_effort=jxl_effort,
            like_num=controller.ui.like_num.value(),
            ban_tag=list(getattr(controller, "ban_tag", []) or []),
            must_tag=list(getattr(controller, "must_tag", []) or []),
            special_like_rules=special_like_rules,
            ai_gen_dir=ai_gen_dir,
        )
    except Exception as err:
        _diag(controller, "start_download_failed", error=str(err))
        controller.add_output(f"<p><font color='red'>[controller] 步驟 4 建立執行緒失敗：{err}</font></p>")
        try:
            controller.enable_button()
            controller.disable_thread_controls()
        except Exception:
            pass
        return
    _connect_download_thread(controller, controller.thread1)
    controller.thread1.start()
    controller.enable_thread_controls()


def start_all(controller):
    controller.disable_button()
    controller.ui_cookies()
    _set_countdown_output_mode(controller, True)
    controller.ui.progressBar.reset()
    controller.log_start('全部執行（步驟 1 -> 4）')
    controller.thread1 = pixiv_thread.get_following(
        controller.userid, controller.cookies, controller.Agent, controller.ui.hidefollow
    )
    _connect_common(controller, controller.thread1, with_countdown=True, with_thenext=True)
    controller.thread1.start()
    controller.enable_thread_controls()


def continue_all(controller, num):
    if num == -1:
        controller.enable_button()
        controller.notice('流程失敗')
        return
    if num == 2:
        _set_countdown_output_mode(controller, True)
        controller.log_start('步驟 2：抓取作品 PID')
        single_mode = _get_single_mode(controller)
        pid_wait_min, pid_wait_max = _get_wait_range(controller, 10, 60)
        controller.thread1 = pixiv_thread.get_pixiv_author_imgID_Thread(
            controller.Author_list,
            controller.Agent,
            controller.path,
            _get_cookie_payload(controller),
            controller.exist_pid,
            single_mode,
            pid_wait_min,
            pid_wait_max,
        )
        _connect_common(controller, controller.thread1, with_thenext=True)
        controller.thread1.start()
        controller.enable_thread_controls()
        return
    if num == 3:
        _set_countdown_output_mode(controller, True)
        cached_url_count = _existing_all_url_count(controller)
        if cached_url_count > 0:
            controller.add_output(
                f"<p><font color='orange'>偵測到既有 all_url.txt（{cached_url_count} 筆），已跳過步驟 3，直接進入步驟 4。</font></p>"
            )
            _diag(controller, "skip_step3_by_cached_all_url", cached_url_count=int(cached_url_count))
            start_download(controller)
            return
        controller.ui_cookies()
        try:
            if hasattr(controller, "reset_progress_state"):
                controller.reset_progress_state()
            else:
                controller.ui.progressBar.reset()
                controller.ui.progressBar.setValue(0)
        except Exception:
            pass
        _sync_exist_pid_before_step34(controller)
        no_to_check = _load_no_to_check(controller)
        single_mode = _get_single_mode(controller)
        pid_wait_min, pid_wait_max, pid_wait_nocookie_min, pid_wait_nocookie_max = _get_wait_ranges(controller, 10, 60, 1, 6)
        special_like_rules = _get_special_like_rules(controller)
        controller.thread1 = pixiv_thread.get_img_url_thread(
            Author_list=controller.Author_list,
            Agent=controller.Agent,
            cookies=_get_cookie_payload(controller),
            exist_pid=controller.exist_pid,
            ban_tag=controller.ban_tag,
            must_tag=controller.must_tag,
            like_num=controller.ui.like_num.value(),
            no_to_check=no_to_check,
            base_path=controller.path,
            single_thread_mode=single_mode,
            pid_wait_min=pid_wait_min,
            pid_wait_max=pid_wait_max,
            pid_wait_nocookie_min=pid_wait_nocookie_min,
            pid_wait_nocookie_max=pid_wait_nocookie_max,
            special_like_rules=special_like_rules,
        )
        _connect_common(controller, controller.thread1, with_thenext=True)
        controller.thread1.start()
        controller.enable_thread_controls()
        return
    if num == 4:
        start_download(controller)







