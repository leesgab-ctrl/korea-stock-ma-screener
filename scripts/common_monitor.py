"""Use the AH membership list in common monitoring without changing its strategy."""
import copy
import json

from ah_rules import hybrid_rows
from first_rise_rules import confirmed


def load_family_candidates(path, current, tier):
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for source in payload.get("candidates", []):
        if source.get("candidateTier") != tier or source.get("tradingDaysRemaining", 0) <= 0:
            continue
        if tier == "first_rise" and not confirmed(source):
            continue
        item = copy.deepcopy(source)
        chart = item.get("displayCharts", {}).get("intraday", {})
        # Common image/color rules must never consume the daily-estimated prefix.
        chart["series"] = hybrid_rows(chart.get("history", []), [], current) if chart.get("source") == "naver_native" else []
        chart.pop("ahModel", None)
        result.append(item)
    return result


def load_ah_candidates(path, current):
    return load_family_candidates(path, current, "ah")


def load_first_rise_candidates(path, current):
    return load_family_candidates(path, current, "first_rise")


def merge_candidates(primary, extra, excluded_codes=()):
    excluded = set(excluded_codes)
    result = {}
    for item in [*primary, *extra]:
        if item["code"] not in excluded:
            result.setdefault(item["code"], item)
    return list(result.values())
