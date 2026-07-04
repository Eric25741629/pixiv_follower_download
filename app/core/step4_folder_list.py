"""Download-folder listing + PID-from-filename parsing for ``download_thread``
(file-size refactor).

The three filename→PID parsers, the ``splitID`` dispatcher over a file list,
and the ``get_filelist`` os.walk collector. Mixed into ``download_thread`` via
``_Step4FolderListMixin``; the parsers are static and ``splitID`` reaches them
through inheritance, so behaviour is unchanged.
``tests/test_split_id.py`` pins the parsing cases.
"""
from __future__ import annotations

import os
import re


class _Step4FolderListMixin:
    @staticmethod
    def _parse_pid_from_pid_equals(file):
        # Format: "...PID=12345_p0.jpg" → requires 4 < len < 12.
        try:
            candidate = file.split('PID=')[1].split('_')[0]
        except IndexError:
            return None
        if 4 < len(candidate) < 12:
            return candidate
        return None

    @staticmethod
    def _parse_pid_from_pid_prefix(file):
        # Format: "...PID12345 ..." → requires 4 < len <= 13.
        # Fallback: if too long/short but leading "p"-split is digit, keep dot-stripped form.
        try:
            candidate = file.split('PID')[1].split(' ')[0]
        except IndexError:
            return None
        if 4 < len(candidate) <= 13:
            return candidate
        head = candidate.split('p')[0]
        if head.isdigit():
            return candidate.split('.')[0]
        return None

    @staticmethod
    def _parse_pid_from_underscore(file):
        # Format: "illust_12345_..." → requires 4 < len < 12.
        try:
            candidate = file.split('_')[1]
        except IndexError:
            return None
        if 4 < len(candidate) < 12:
            return candidate
        return None

    def splitID(self, Filelist):
        parsers = (
            self._parse_pid_from_pid_equals,
            self._parse_pid_from_pid_prefix,
            self._parse_pid_from_underscore,
        )
        seen = set()
        for file in Filelist:
            if not re.search(r'\.jpg|\.png|\.gif', file):
                continue
            if not re.search(r'PID|illust', file):
                continue
            for parser in parsers:
                pid = parser(file)
                if pid:
                    seen.add(pid)
                    break
        return list(seen)

    def get_filelist(self, path):
        file_list = []
        try:
            for root, _, files in os.walk(path):
                for name in files:
                    file_list.append(os.path.join(root, name))
        except Exception:
            pass
        return file_list
