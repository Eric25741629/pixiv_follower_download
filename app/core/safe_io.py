import os
import shutil
import json
import datetime


def _ensure_history_dir(file_path):
    try:
        d = os.path.dirname(file_path) or os.getcwd()
        hist = os.path.join(d, 'history')
        os.makedirs(hist, exist_ok=True)
        return hist
    except Exception:
        return None


def _next_unique_backup_path(hist, base, ts):
    """Return a backup path that doesn't exist yet (appends .1, .2, ... on collision)."""
    dst = os.path.join(hist, f"{base}.{ts}")
    if not os.path.exists(dst):
        return dst
    idx = 1
    while True:
        candidate = os.path.join(hist, f"{base}.{ts}.{idx}")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def _prune_old_backups(hist, base, max_history):
    """Keep at most ``max_history`` files in ``hist`` whose name starts with ``base.``."""
    try:
        files = [f for f in os.listdir(hist) if f.startswith(base + '.')]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(hist, x)), reverse=True)
        while len(files) > max_history:
            os.remove(os.path.join(hist, files.pop()))
    except Exception:
        pass


def backup_file(file_path, max_history=10):
    """
    備份檔案到 history/，並限制保留的歷史檔數量。
    :param file_path: 原始檔案路徑
    :param max_history: 最多保留的歷史檔數量（預設 10）
    """
    try:
        if not os.path.exists(file_path):
            return
        hist = _ensure_history_dir(file_path)
        if not hist:
            return
        base = os.path.basename(file_path)
        ts = datetime.datetime.now().strftime('%Y%m%d')
        dst = _next_unique_backup_path(hist, base, ts)
        shutil.copy2(file_path, dst)
        _prune_old_backups(hist, base, max_history)
    except Exception:
        pass


def atomic_write_text(file_path, lines, encoding='utf-8', backup=True):
    """
    原子寫入文字檔，可選擇是否備份。
    :param file_path: 檔案路徑
    :param lines: 要寫入的內容（list 或 str）
    :param encoding: 編碼
    :param backup: 是否備份到 history/（預設 True）
    """
    try:
        if backup:
            try:
                backup_file(file_path)
            except Exception:
                pass
        tmp = file_path + '.tmp'
        dirp = os.path.dirname(file_path)
        if dirp:
            os.makedirs(dirp, exist_ok=True)
        with open(tmp, 'w', encoding=encoding) as f:
            if isinstance(lines, (list, tuple)):
                f.writelines([str(x) + '\n' for x in lines])
            else:
                f.write(str(lines))
        os.replace(tmp, file_path)
    except Exception:
        try:
            with open(file_path, 'w', encoding=encoding) as f:
                if isinstance(lines, (list, tuple)):
                    f.writelines([str(x) + '\n' for x in lines])
                else:
                    f.write(str(lines))
        except Exception:
            pass


def atomic_write_json(file_path, obj, encoding='utf-8', backup=True):
    """
    原子寫入 JSON 檔，可選擇是否備份。
    :param file_path: 檔案路徑
    :param obj: 要寫入的物件
    :param encoding: 編碼
    :param backup: 是否備份到 history/（預設 True）
    """
    try:
        if backup:
            try:
                backup_file(file_path)
            except Exception:
                pass
        tmp = file_path + '.tmp'
        dirp = os.path.dirname(file_path)
        if dirp:
            os.makedirs(dirp, exist_ok=True)
        with open(tmp, 'w', encoding=encoding) as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, file_path)
    except Exception:
        try:
            with open(file_path, 'w', encoding=encoding) as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def atomic_append_text(file_path, lines, encoding='utf-8'):
    try:
        dirp = os.path.dirname(file_path)
        if dirp:
            os.makedirs(dirp, exist_ok=True)
        # append without backup to avoid large history growth; caller can call backup_file first if desired
        with open(file_path, 'a', encoding=encoding) as f:
            if isinstance(lines, (list, tuple)):
                f.writelines([str(x) + '\n' for x in lines])
            else:
                f.write(str(lines))
    except Exception:
        pass
