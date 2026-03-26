from datetime import datetime
import re
import pixiv_thread


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


def _load_no_to_check(controller):
    no_to_check = []
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
    try:
        enable = bool(controller.ui.jxl_enable.isChecked())
    except Exception:
        enable = False
    try:
        cjxl_path = str(controller.ui.jxl_cjxl_path.text()).strip()
    except Exception:
        cjxl_path = r"C:\Users\Eric\Downloads\jxl-x64-windows\bin\cjxl.exe"
    try:
        delete_original = bool(controller.ui.jxl_delete_original.isChecked())
    except Exception:
        delete_original = False
    try:
        effort = int(controller.ui.jxl_effort.value())
    except Exception:
        effort = 7
    return enable, cjxl_path, delete_original, effort


def _connect_common(controller, thread, with_countdown=True, with_timechanged=False, with_thenext=False):
    try:
        thread.finished.connect(controller._on_qthread_finished)
    except Exception:
        pass
    thread._signal.connect(controller.progress_changed)
    thread._output.connect(controller.add_output)
    thread._finished.connect(controller.notice)
    if with_countdown and hasattr(thread, "_countdown"):
        try:
            thread._countdown.connect(controller.update_countdown)
        except Exception:
            pass
    if with_timechanged:
        thread._timechanged.connect(controller.timechanged)
    if with_thenext:
        thread._thenext.connect(controller.the_next)


def start_get_following(controller):
    controller.disable_button()
    controller.ui_cookies()
    controller.ui.progressBar.reset()
    controller.log_start('獲取關注畫師')
    controller.thread1 = pixiv_thread.get_following(
        controller.userid, controller.cookies, controller.Agent, controller.ui.hidefollow
    )
    _connect_common(controller, controller.thread1, with_countdown=False)
    controller.thread1.start()
    controller.enable_thread_controls()


def start_get_pid(controller):
    controller.disable_button()
    controller.ui_cookies()
    controller.log_start('獲取關注畫師的圖片ID')
    single_mode = _get_single_mode(controller)
    pid_wait_min, pid_wait_max = _get_wait_range(controller, 10, 60)
    controller.thread1 = pixiv_thread.get_pixiv_author_imgID_Thread(
        controller.Author_list,
        controller.Agent,
        controller.path,
        controller.cookies,
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
    controller.log_start('獲取圖片id的詳細資料')
    no_to_check = _load_no_to_check(controller)
    single_mode = _get_single_mode(controller)
    pid_wait_min, pid_wait_max, pid_wait_nocookie_min, pid_wait_nocookie_max = _get_wait_ranges(controller, 10, 60, 1, 6)
    controller.thread1 = pixiv_thread.get_img_url_thread(
        controller.Author_list,
        controller.Agent,
        controller.cookies,
        controller.exist_pid,
        controller.ban_tag,
        controller.must_tag,
        controller.ui.like_num.value(),
        no_to_check,
        single_mode,
        pid_wait_min,
        pid_wait_max,
        pid_wait_nocookie_min,
        pid_wait_nocookie_max,
    )
    _connect_common(controller, controller.thread1)
    controller.thread1.start()
    controller.enable_thread_controls()


def start_download(controller):
    controller.ui_cookies()
    controller.disable_button()
    try:
        controller.ui.progressBar.reset()
        controller.ui.progressBar.setValue(0)
    except Exception:
        pass
    controller.log_start('開始下載')
    single_mode = _get_single_mode(controller)
    pid_wait_min, pid_wait_max, pid_wait_nocookie_min, pid_wait_nocookie_max = _get_wait_ranges(controller, 1, 3, 0, 1)
    jxl_enable, jxl_cjxl_path, jxl_delete_original, jxl_effort = _get_jxl_options(controller)
    controller.thread1 = pixiv_thread.download_thread(
        controller.ui.nogif.isChecked(),
        controller.ui.notag.isChecked(),
        controller.ui.notime.isChecked(),
        controller.ui.create_dir.isChecked(),
        controller.ui.user_path1.text(),
        controller.cookies,
        controller.Agent,
        datetime.strptime(
            controller.ui.download_time.dateTime().toString("yyyy-MM-dd hh:mm:ss"),
            "%Y-%m-%d %H:%M:%S",
        ),
        controller.ui.no_R18G_dir.isChecked(),
        controller.ui.like_num.value(),
        jxl_enable,
        jxl_cjxl_path,
        jxl_delete_original,
        jxl_effort,
        single_mode,
        pid_wait_min,
        pid_wait_max,
        None,
        None,
        pid_wait_nocookie_min,
        pid_wait_nocookie_max,
        None,
        None,
    )
    _connect_common(controller, controller.thread1, with_timechanged=True)
    controller.thread1.start()
    controller.enable_thread_controls()


def start_all(controller):
    controller.disable_button()
    controller.ui_cookies()
    controller.ui.progressBar.reset()
    controller.log_start('一鍵開始')
    controller.thread1 = pixiv_thread.get_following(
        controller.userid, controller.cookies, controller.Agent, controller.ui.hidefollow
    )
    _connect_common(controller, controller.thread1, with_countdown=True, with_thenext=True)
    controller.thread1.start()
    controller.enable_thread_controls()


def continue_all(controller, num):
    if num == -1:
        controller.enable_button()
        controller.notice('下載失敗')
        return
    if num == 2:
        controller.log_start('獲取關注畫師的圖片ID')
        single_mode = _get_single_mode(controller)
        pid_wait_min, pid_wait_max = _get_wait_range(controller, 10, 60)
        controller.thread1 = pixiv_thread.get_pixiv_author_imgID_Thread(
            controller.Author_list,
            controller.Agent,
            controller.path,
            controller.cookies,
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
        controller.ui_cookies()
        no_to_check = _load_no_to_check(controller)
        single_mode = _get_single_mode(controller)
        pid_wait_min, pid_wait_max, pid_wait_nocookie_min, pid_wait_nocookie_max = _get_wait_ranges(controller, 10, 60, 1, 6)
        controller.thread1 = pixiv_thread.get_img_url_thread(
            controller.Author_list,
            controller.Agent,
            controller.cookies,
            controller.exist_pid,
            controller.ban_tag,
            controller.must_tag,
            controller.ui.like_num.value(),
            no_to_check,
            single_mode,
            pid_wait_min,
            pid_wait_max,
            pid_wait_nocookie_min,
            pid_wait_nocookie_max,
        )
        _connect_common(controller, controller.thread1, with_thenext=True)
        controller.thread1.start()
        controller.enable_thread_controls()
        return
    if num == 4:
        start_download(controller)
