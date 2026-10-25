"""MA10 pullback to MA60 for confirmed first-rise members only."""
import datetime as dt
import math

from first_rise_rules import confirmed


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def membership(candidate):
    item = candidate if candidate.get("candidateTier") == "first_rise" else candidate.get("firstRiseMembership", {})
    return item if confirmed(item) and item.get("tradingDaysRemaining", 0) > 0 else None


def daily_reference(daily, row):
    # Only prior-session daily data is available at the historical signal time.
    prior = sorted((bar for bar in daily if bar.get("d", "") < row["t"][:10]), key=lambda bar: bar["d"])
    if not prior:
        return None
    bar = prior[-1]
    if not all(positive(bar.get(key)) for key in ("o", "c")) or bar["c"] <= bar["o"]:
        return None
    return {"day": bar["d"], "open": bar["o"], "close": bar["c"],
            "midpoint": (bar["o"] + bar["c"]) / 2,
            "retracementPercent": (bar["c"] - row["c"]) / (bar["c"] - bar["o"]) * 100}


def detect(rows, rule, candidate):
    member = membership(candidate)
    if not member:
        return []
    day = member.get("dailySignalDate", "")
    start = member.get("firstRiseSignal", {}).get("time") or day
    previous = peak = None
    for row in rows:
        if row["t"] < start:
            continue
        if not all(positive(row.get(key)) for key in ("m10", "m60", "c")):
            previous = peak = None
            continue
        if previous and row["t"][:10] == previous["t"][:10] and (
                dt.datetime.fromisoformat(row["t"]) - dt.datetime.fromisoformat(previous["t"])) != dt.timedelta(minutes=30):
            previous = peak = None
        if previous and row["m10"] > previous["m10"]:
            if peak is None or row["m10"] > peak["m10"]:
                peak = row
        gap = (row["m10"] / row["m60"] - 1) * 100
        if (previous and peak and peak["m10"] > peak["m60"] and row["m10"] < previous["m10"]
                and row["m10"] < peak["m10"] and row["t"][:10] > day
                and abs(gap) <= rule["maxGapPercent"] + 1e-9):
            return [{"id": f"{rule['id']}|{day}|watch", "ruleId": rule["id"], "ruleVersion": rule["version"],
                     "signalType": "firstRiseMa10Ma60", "stage": "watch", "stageLabel": "MA10·MA60 접근 · 매수검토",
                     "time": row["t"], "completedAt": (dt.datetime.fromisoformat(row["t"]) + dt.timedelta(minutes=30)).isoformat(timespec="minutes"),
                     "episodeTime": day, "setupTime": peak["t"], "selectionDay": day, "price": row["c"],
                     "m3": row.get("m3"), "m10": row["m10"], "m60": row["m60"], "gapPercent": gap,
                     "maxGapPercent": rule["maxGapPercent"], "closeApproach": abs(gap) <= rule["closeGapPercent"] + 1e-9,
                     "dailyReference": daily_reference(candidate.get("displayCharts", {}).get("daily", {}).get("history", []), row)}]
        previous = row
    return []
