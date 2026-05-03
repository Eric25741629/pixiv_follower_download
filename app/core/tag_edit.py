import json
from pathlib import Path

_RULES_PATH = Path(__file__).parent / "tag_rules.json"
TAG_RULES = json.loads(_RULES_PATH.read_text(encoding="utf-8"))

assert TAG_RULES, f"tag_rules.json is empty or missing at {_RULES_PATH}"

# Pre-split rules by length bucket for O(1) lookup at call time
_BUCKET_LTE5    = [(r[0], r[1]) for r in TAG_RULES if len(r) == 3 and r[2] == "lte5"]
_BUCKET_GT5LTE10 = [(r[0], r[1]) for r in TAG_RULES if len(r) == 3 and r[2] == "gt5lte10"]
_BUCKET_GT10LT20 = [(r[0], r[1]) for r in TAG_RULES if len(r) == 3 and r[2] == "gt10lt20"]
_BUCKET_GTE20   = [(r[0], r[1]) for r in TAG_RULES if len(r) == 3 and r[2] == "gte20"]
_BUCKET_ALWAYS  = [(r[0], r[1]) for r in TAG_RULES if len(r) == 2]


_FORBIDDEN_CHARS_TT = str.maketrans("", "", '"}{[]:<|>?/※\\⊂⊃*')
_USER_BUCKET_TAG = "users入り"


def _bucket_for_length(length):
    """Pick the rule bucket whose length range covers ``length``."""
    if length <= 5:
        return _BUCKET_LTE5
    if length <= 10:
        return _BUCKET_GT5LTE10
    if length < 20:
        return _BUCKET_GT10LT20
    return _BUCKET_GTE20


def _apply_replacements(tag, bucket):
    """Sequentially apply ``(old, new)`` replacements; bucket may be empty."""
    for old, new in bucket:
        tag = tag.replace(old, new)
    return tag


def _normalize_one_tag(tag):
    """Run a single tag through punctuation normalization, length-bucket
    replacements, and the always-on replacement bucket."""
    tag = tag.replace('・', '_').replace('.', '_')
    tag = _apply_replacements(tag, _bucket_for_length(len(tag)))
    tag = _apply_replacements(tag, _BUCKET_ALWAYS)
    return tag


def Tag(resdicts):  # 回傳標籤
    """Normalize a list of artwork tags via the tag_rules.json bucket system."""
    taglist = [t for t in resdicts if _USER_BUCKET_TAG not in t]
    taglist = [t.translate(_FORBIDDEN_CHARS_TT) for t in taglist]
    try:
        return [_normalize_one_tag(t) for t in taglist]
    except Exception:
        return taglist
