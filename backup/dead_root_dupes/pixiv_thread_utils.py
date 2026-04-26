import datetime
import json
import os
import re
import shutil
import sys
import traceback


def output_err(e):
    error_class = e.__class__.__name__
    detail = e.args[0] if e.args else ""
    cl, exc, tb = sys.exc_info()
    if tb is None:
        return "[{}] {}".format(error_class, detail)
    lastCallStack = traceback.extract_tb(tb)[-1]
    fileName = lastCallStack[0]
    lineNum = lastCallStack[1]
    funcName = lastCallStack[2]
    errMsg = "File \"{}\", line {}, in {}: [{}] {}".format(fileName, lineNum, funcName, error_class, detail)
    return errMsg


def normalize_pid(value):
    s = str(value).strip()
    if not s:
        return ""
    if '_' in s:
        s = s.split('_', 1)[0]
    s = s.replace('p0', '')
    m = re.search(r"\d+", s)
    if m:
        return m.group(0)
    return s


def normalize_pid_set(values):
    out = set()
    if not values:
        return out
    try:
        for v in values:
            pid = normalize_pid(v)
            if pid:
                out.add(pid)
    except Exception:
        pid = normalize_pid(values)
        if pid:
            out.add(pid)
    return out


def _ensure_history_dir(file_path):
    try:
        d = os.path.dirname(file_path) or os.getcwd()
        hist = os.path.join(d, 'history')
        os.makedirs(hist, exist_ok=True)
        return hist
    except Exception:
        return None


def backup_file(file_path, max_history=10):
    try:
        if not os.path.exists(file_path):
            return
        hist = _ensure_history_dir(file_path)
        if not hist:
            return
        ts = datetime.datetime.now().strftime('%Y%m%d')
        base = os.path.basename(file_path)
        dst = os.path.join(hist, f"{base}.{ts}")
        if os.path.exists(dst):
            idx = 1
            while True:
                candidate = os.path.join(hist, f"{base}.{ts}.{idx}")
                if not os.path.exists(candidate):
                    dst = candidate
                    break
                idx += 1
        shutil.copy2(file_path, dst)
        try:
            files = [f for f in os.listdir(hist) if f.startswith(base + '.')]
            files.sort(key=lambda x: os.path.getmtime(os.path.join(hist, x)), reverse=True)
            while len(files) > max_history:
                old_file = os.path.join(hist, files.pop(0))
                os.remove(old_file)
        except Exception:
            pass
    except Exception:
        pass


def atomic_write_text(file_path, lines, encoding='utf-8', backup=True):
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
