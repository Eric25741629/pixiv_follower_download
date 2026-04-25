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


def Tag(resdicts):  # 回傳標籤
    Taglist = resdicts
    Taglist = [t for t in Taglist if "users入り" not in t]
    Taglist = [t.translate(str.maketrans(
        "", "", '"}{[]:<|>?/※\\⊂⊃*')) for t in Taglist]
    try:
        for i in range(0, len(Taglist)):
            lenth = len(Taglist[i])
            Taglist[i] = Taglist[i].replace('・', '_')
            Taglist[i] = Taglist[i].replace('.', '_')
            if lenth <= 5:
                bucket = _BUCKET_LTE5
            elif lenth > 5 and lenth <= 10:
                bucket = _BUCKET_GT5LTE10
            elif lenth > 10 and lenth < 20:
                bucket = _BUCKET_GT10LT20
            elif lenth >= 20:
                bucket = _BUCKET_GTE20
            else:
                bucket = []
            for old, new in bucket:
                Taglist[i] = Taglist[i].replace(old, new)
            for old, new in _BUCKET_ALWAYS:
                Taglist[i] = Taglist[i].replace(old, new)
        # print(Taglist)
        return Taglist
    except Exception:
        return Taglist
