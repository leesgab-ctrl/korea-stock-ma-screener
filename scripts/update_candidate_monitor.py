from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
TRACKING_RULE_VERSION = 4
CORE_TIER = "core"
EXPANDED_TIER = "expanded"
STRATEGY = {
    "candidateWindowTradingDays": 10,
    "candidateTiers": {
        CORE_TIER: "기존 A-G(A 거래량 +200%, E 종가 +1%)",
        EXPANDED_TIER: "확대 A-G(A 거래량 +150%, E 종가 +0.5%)",
    },
    "minuteTimeframe": "30분봉",
    "maRule": "A-G 통과 후 MA20 5회 상승, MA60 침범 시 MA60 재돌파(일봉 MA10은 참고선)",
    "entry": "신호봉 완성 후 다음 30분봉부터 HTS 현재가 확인",
}


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def rolling_average(values: list[int], window: int) -> list[float | None]:
    result: list[float | None] = []
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= window:
            total -= values[index - window]
        result.append(total / window if index >= window - 1 else None)
    return result


def stock_rows(payload: dict[str, Any], stock: dict[str, Any]) -> list[dict[str, Any]]:
    prices = stock.get("p", [])
    volumes = stock.get("v", [])
    if len(prices) != len(volumes):
        return []
    if "s" in stock:
        indexes = range(stock["s"], stock["s"] + len(prices))
    else:
        indexes = stock.get("i", [])
    dates = payload.get("dates", [])
    rows = []
    for index, close, volume in zip(indexes, prices, volumes):
        if 0 <= index < len(dates):
            rows.append({"date": dates[index], "close": close, "volume": volume})
    return rows


def evaluate_ag(rows: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    if index < 22:
        return None
    spike = index - 2
    pullback = index - 1
    pre_spike = index - 3
    close_ma20 = sum(row["close"] for row in rows[pre_spike - 19 : pre_spike + 1]) / 20
    volume_ma20 = sum(row["volume"] for row in rows[spike - 19 : spike + 1]) / 20
    if min(close_ma20, volume_ma20, rows[pre_spike]["close"]) <= 0:
        return None
    values = {
        "volumeSpikePct": 100 * (rows[spike]["volume"] / volume_ma20 - 1),
        "spikeClosePct": 100 * (rows[spike]["close"] / rows[pre_spike]["close"] - 1),
        "pullbackClosePct": 100 * (rows[pullback]["close"] / rows[spike]["close"] - 1),
        "signalClosePct": 100 * (rows[index]["close"] / rows[pullback]["close"] - 1),
        "preSpikeVsMa20Pct": 100 * (rows[pre_spike]["close"] / close_ma20 - 1),
    }
    core_checks = {
        "A": values["volumeSpikePct"] >= 200,
        "B": values["spikeClosePct"] >= 3,
        "C": -10 <= values["pullbackClosePct"] <= 0,
        "D": rows[pullback]["volume"] < rows[spike]["volume"],
        "E": values["signalClosePct"] >= 1,
        "F": rows[index]["volume"] > rows[pullback]["volume"],
        "G": -10 <= values["preSpikeVsMa20Pct"] <= 5,
    }
    expanded_checks = {
        **core_checks,
        "A": values["volumeSpikePct"] >= 150,
        "E": values["signalClosePct"] >= 0.5,
    }
    if all(core_checks.values()):
        candidate_tier = CORE_TIER
        checks = core_checks
    elif all(expanded_checks.values()):
        candidate_tier = EXPANDED_TIER
        checks = expanded_checks
    else:
        return None
    return {
        "candidateTier": candidate_tier,
        "close": rows[index]["close"],
        "spikeDate": rows[spike]["date"],
        "preSpikeDate": rows[pre_spike]["date"],
        "preSpikeClose": rows[pre_spike]["close"],
        "checks": checks,
        "coreChecks": core_checks,
        "values": {key: round(value, 2) for key, value in values.items()},
    }


def load_previous(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"notifiedSignals": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"notifiedSignals": []}


def build_daily_candidates(stock_data: Path, previous: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(stock_data.read_text(encoding="utf-8"))
    calendar = payload.get("dates", [])
    if not calendar:
        raise RuntimeError("일봉 거래일 데이터가 없습니다.")
    if not any("v" in stock for stock in payload.get("stocks", [])):
        raise RuntimeError("stock-data.json에 거래량이 없습니다. generate_static_data.py를 먼저 실행하세요.")

    active_dates = set(calendar[-10:])
    calendar_index = {date: index for index, date in enumerate(calendar)}
    latest_date = calendar[-1]
    candidates = []
    previous_candidates = {
        (item.get("code"), item.get("dailySignalDate")): item
        for item in previous.get("candidates", [])
    }
    for stock in payload.get("stocks", []):
        if stock.get("h"):
            continue
        rows = stock_rows(payload, stock)
        latest_match = None
        for index in range(max(22, len(rows) - 14), len(rows)):
            if rows[index]["date"] not in active_dates:
                continue
            result = evaluate_ag(rows, index)
            if result:
                latest_match = (index, result)
        if latest_match is None:
            continue
        index, result = latest_match
        signal_date = rows[index]["date"]
        age = calendar_index[latest_date] - calendar_index[signal_date]
        if age >= 10:
            continue
        ma10_previous_by_date = {}
        for daily_index in range(max(10, len(rows) - 20), len(rows)):
            previous_closes = [row["close"] for row in rows[daily_index - 10 : daily_index]]
            if len(previous_closes) == 10:
                ma10_previous_by_date[rows[daily_index]["date"]] = round(
                    sum(previous_closes) / 10, 2
                )
        candidate = {
                "code": stock["c"],
                "name": stock["n"],
                "market": stock["m"],
                "candidateTier": result["candidateTier"],
                "dailySignalDate": signal_date,
                "tradingDayAge": age,
                "tradingDaysRemaining": 10 - age,
                "daily": result,
                "dailyReference": {
                    "close": rows[-1]["close"],
                "ma10": round(sum(row["close"] for row in rows[-10:]) / 10, 2),
                "date": rows[-1]["date"],
                "ma10PreviousByDate": ma10_previous_by_date,
                },
                "status": "watching",
                "naverUrl": f"https://stock.naver.com/domestic/stock/{stock['c']}/price",
            }
        prior = previous_candidates.get((stock["c"], signal_date), {})
        if prior.get("tracking"):
            candidate["tracking"] = prior["tracking"]
        if prior.get("intradayHistory"):
            candidate["intradayHistory"] = prior["intradayHistory"]
        candidates.append(candidate)

    candidates.sort(key=lambda item: (item["dailySignalDate"], item["name"]), reverse=True)
    return {
        "version": 1,
        "generatedAt": now_kst().isoformat(timespec="seconds"),
        "asOf": latest_date,
        "source": "네이버 증권 일봉 및 분봉",
        "strategy": STRATEGY,
        "summary": {},
        "candidates": candidates,
        "notifiedSignals": previous.get("notifiedSignals", []),
    }


def fetch_minute_rows(code: str, count: int) -> list[dict[str, Any]]:
    url = (
        "https://fchart.stock.naver.com/sise.nhn"
        f"?symbol={code}&timeframe=minute&count={count}&requestType=0"
    )
    text = fetch_bytes(url).decode("euc-kr", errors="ignore")
    rows = []
    for raw in re.findall(r'<item data="([^"]+)"', text):
        values = raw.split("|")
        if len(values) < 6 or values[4] in ("", "null") or values[5] in ("", "null"):
            continue
        try:
            stamp = dt.datetime.strptime(values[0], "%Y%m%d%H%M").replace(tzinfo=KST)
            rows.append({"time": stamp, "price": int(values[4]), "cumulativeVolume": int(values[5])})
        except ValueError:
            continue
    rows.sort(key=lambda row: row["time"])
    return rows


def fetch_daily_chart(code: str, count: int = 130) -> dict[str, Any]:
    url = (
        "https://fchart.stock.naver.com/sise.nhn"
        f"?symbol={code}&timeframe=day&count={count}&requestType=0"
    )
    text = fetch_bytes(url).decode("euc-kr", errors="ignore")
    rows = []
    for raw in re.findall(r'<item data="([^"]+)"', text):
        values = raw.split("|")
        if len(values) < 6:
            continue
        try:
            rows.append(
                {
                    "date": dt.datetime.strptime(values[0], "%Y%m%d").date().isoformat(),
                    "open": int(values[1]),
                    "high": int(values[2]),
                    "low": int(values[3]),
                    "close": int(values[4]),
                    "volume": int(values[5]),
                }
            )
        except ValueError:
            continue
    rows.sort(key=lambda row: row["date"])
    closes = [row["close"] for row in rows]
    ma5 = rolling_average(closes, 5)
    ma10 = rolling_average(closes, 10)
    ma20 = rolling_average(closes, 20)
    ma60 = rolling_average(closes, 60)
    series = []
    for index, row in enumerate(rows):
        series.append(
            {
                "d": row["date"],
                "o": row["open"],
                "h": row["high"],
                "l": row["low"],
                "c": row["close"],
                "v": row["volume"],
                "m5": round(ma5[index], 2) if ma5[index] is not None else None,
                "m10": round(ma10[index], 2) if ma10[index] is not None else None,
                "m20": round(ma20[index], 2) if ma20[index] is not None else None,
                "m60": round(ma60[index], 2) if ma60[index] is not None else None,
            }
        )
    return {"dataStatus": "ok" if series else "no_data", "series": series[-60:]}


def aggregate_30m(rows: list[dict[str, Any]], current: dt.datetime) -> list[dict[str, Any]]:
    grouped: dict[tuple[dt.date, int], list[dict[str, Any]]] = defaultdict(list)
    previous_volume: dict[dt.date, int] = {}
    for row in rows:
        stamp = row["time"]
        if not dt.time(9, 0) <= stamp.time() <= dt.time(15, 30):
            continue
        day = stamp.date()
        cumulative = row["cumulativeVolume"]
        prior = previous_volume.get(day, 0)
        row = {**row, "volume": max(0, cumulative - prior)}
        previous_volume[day] = cumulative
        minutes = stamp.hour * 60 + stamp.minute - 9 * 60
        slot = min(minutes // 30, 12)
        grouped[(day, slot)].append(row)

    bars = []
    for (day, slot), ticks in sorted(grouped.items()):
        start = dt.datetime.combine(day, dt.time(9), KST) + dt.timedelta(minutes=30 * slot)
        end = start + dt.timedelta(minutes=30) if slot < 12 else dt.datetime.combine(day, dt.time(15, 31), KST)
        if day == current.date() and current < end:
            continue
        prices = [tick["price"] for tick in ticks]
        bars.append(
            {
                "time": start,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
                "volume": sum(tick["volume"] for tick in ticks),
            }
        )
    closes = [bar["close"] for bar in bars]
    ma20 = rolling_average(closes, 20)
    ma40 = rolling_average(closes, 40)
    ma60 = rolling_average(closes, 60)
    for index, bar in enumerate(bars):
        bar["ma20"] = ma20[index]
        bar["ma40"] = ma40[index]
        bar["ma60"] = ma60[index]
    return bars


def load_saved_30m(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    saved = candidate.get("intradayHistory", {}).get("series")
    if not saved:
        saved = candidate.get("intraday", {}).get("series", [])
    bars = []
    for row in saved:
        try:
            bars.append(
                {
                    "time": dt.datetime.fromisoformat(row["t"]),
                    "open": int(row["o"]),
                    "high": int(row["h"]),
                    "low": int(row["l"]),
                    "close": int(row["c"]),
                    "volume": int(row.get("v", 0)),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return bars


def merge_30m_history(
    candidate: dict[str, Any], fresh_bars: list[dict[str, Any]], limit: int = 220
) -> list[dict[str, Any]]:
    saved_bars = load_saved_30m(candidate)
    if not saved_bars:
        bars = fresh_bars
    else:
        by_time = {bar["time"]: bar for bar in saved_bars}
        by_time.update({bar["time"]: bar for bar in fresh_bars})
        bars = [by_time[key] for key in sorted(by_time)][-limit:]
        closes = [bar["close"] for bar in bars]
        ma20 = rolling_average(closes, 20)
        ma40 = rolling_average(closes, 40)
        ma60 = rolling_average(closes, 60)
        for index, bar in enumerate(bars):
            bar["ma20"] = ma20[index]
            bar["ma40"] = ma40[index]
            bar["ma60"] = ma60[index]
    candidate["intradayHistory"] = {
        "series": [
            {
                "t": bar["time"].isoformat(timespec="minutes"),
                "o": bar.get("open", bar["close"]),
                "h": bar.get("high", bar["close"]),
                "l": bar.get("low", bar["close"]),
                "c": bar["close"],
                "v": bar.get("volume", 0),
            }
            for bar in bars[-limit:]
        ]
    }
    return bars


def latest_trading_days(
    bars: list[dict[str, Any]], day_count: int = 5
) -> list[dict[str, Any]]:
    """Return all bars belonging to the latest distinct trading days."""
    if not bars or day_count <= 0:
        return []
    days = sorted({bar["time"].date() for bar in bars})[-day_count:]
    selected_days = set(days)
    return [bar for bar in bars if bar["time"].date() in selected_days]


def latest_death_cross_index(bars: list[dict[str, Any]], index: int, lookback: int = 160) -> int | None:
    found = None
    for cursor in range(max(1, index - lookback), index + 1):
        previous = bars[cursor - 1]
        current = bars[cursor]
        if None in (previous["ma20"], previous["ma40"], current["ma20"], current["ma40"]):
            continue
        if previous["ma20"] >= previous["ma40"] and current["ma20"] < current["ma40"]:
            found = cursor
    return found


def prior_death_cross(bars: list[dict[str, Any]], index: int, lookback: int = 160) -> bool:
    return latest_death_cross_index(bars, index, lookback) is not None


def rise_context(bars: list[dict[str, Any]], index: int) -> dict[str, Any]:
    start = index
    while start > 0:
        current_ma = bars[start]["ma20"]
        previous_ma = bars[start - 1]["ma20"]
        if current_ma is None or previous_ma is None or current_ma <= previous_ma:
            break
        start -= 1
    count = index - start
    reversal_under = bool(
        start >= 1
        and bars[start]["ma20"] is not None
        and bars[start]["ma40"] is not None
        and bars[start]["ma20"] < bars[start]["ma40"]
        and bars[start - 1]["ma20"] is not None
        and bars[start]["ma20"] < bars[start - 1]["ma20"]
    )
    death_cross_index = latest_death_cross_index(bars, start) if reversal_under else None
    return {
        "riseCount": count,
        "reversalIndex": start,
        "reversalUnderMa40": reversal_under,
        "priorDeathCross": death_cross_index is not None,
        "deathCrossIndex": death_cross_index,
    }


def consecutive_ma20_falls(bars: list[dict[str, Any]], index: int) -> int:
    count = 0
    for cursor in range(index, 0, -1):
        current_ma = bars[cursor].get("ma20")
        previous_ma = bars[cursor - 1].get("ma20")
        if current_ma is None or previous_ma is None or current_ma >= previous_ma:
            break
        count += 1
    return count


def valid_post_candidate_sequence(
    bars: list[dict[str, Any]], context: dict[str, Any], signal_day: dt.date, baseline_above: bool
) -> bool:
    death_cross_index = context.get("deathCrossIndex")
    if not baseline_above or death_cross_index is None:
        return False
    return (
        bars[death_cross_index]["time"].date() > signal_day
        and bars[context["reversalIndex"]]["time"].date() > signal_day
        and death_cross_index <= context["reversalIndex"]
    )


def daily_ma10_for_bar(daily_reference: dict[str, Any] | float | None, bar: dict[str, Any]) -> float | None:
    if isinstance(daily_reference, (int, float)):
        return float(daily_reference)
    if not isinstance(daily_reference, dict):
        return None
    day = bar["time"].date().isoformat()
    history = daily_reference.get("ma10PreviousByDate", {})
    if day in history:
        return float(history[day])
    reference_date = daily_reference.get("date")
    if not reference_date and daily_reference.get("ma10") is not None:
        return float(daily_reference["ma10"])
    if reference_date and day > reference_date and daily_reference.get("ma10") is not None:
        return float(daily_reference["ma10"])
    return None


def depth_rule_context(
    bars: list[dict[str, Any]], context: dict[str, Any], index: int,
    daily_reference: dict[str, Any] | float | None,
) -> dict[str, Any]:
    death_cross_index = context.get("deathCrossIndex")
    if death_cross_index is None:
        return {
            "ma60Ready": False,
            "breachedMa60": False,
            "recoveredMa60": False,
            "breachedDailyMa10": False,
            "signalRule": None,
            "signalReady": False,
        }
    path = bars[death_cross_index : index + 1]
    # Naver's short minute-history window can leave MA60 blank at the start of
    # a valid post-candidate path. Use every comparable completed bar instead
    # of discarding the whole path because its earliest MA60 values are blank.
    ma60_path = [
        bar for bar in path
        if bar.get("ma20") is not None and bar.get("ma60") is not None
    ]
    ma60_ready = bool(ma60_path)
    breached_ma60 = bool(any(bar["ma20"] < bar["ma60"] for bar in ma60_path))
    latest = bars[index]
    latest_daily_ma10 = daily_ma10_for_bar(daily_reference, latest)
    breached_daily_ma10 = bool(
        latest.get("ma20") is not None
        and latest_daily_ma10 is not None
        and latest["ma20"] < latest_daily_ma10
    )
    recovered_ma60 = bool(
        ma60_ready
        and latest.get("ma20") is not None
        and latest.get("ma60") is not None
        and latest["ma20"] > latest["ma60"]
    )
    signal_rule = "ma60_recovery" if breached_ma60 else "five_rises"
    signal_ready = bool(
        ma60_ready
        and context.get("riseCount", 0) >= 5
        and (not breached_ma60 or recovered_ma60)
    )
    return {
        "ma60Ready": ma60_ready,
        "breachedMa60": breached_ma60,
        "recoveredMa60": recovered_ma60,
        "breachedDailyMa10": breached_daily_ma10,
        "signalRule": signal_rule,
        "signalReady": signal_ready,
    }


def infer_missing_baseline(
    bars: list[dict[str, Any]], signal_day: dt.date
) -> tuple[bool, int | None]:
    """Recover legacy candidates whose A-G baseline fell outside Naver history."""
    for index, bar in enumerate(bars):
        if bar["time"].date() <= signal_day or None in (bar.get("ma20"), bar.get("ma40")):
            continue
        return True, index if bar["ma20"] < bar["ma40"] else None
    return False, None


def apply_inferred_cross(context: dict[str, Any], synthetic_cross_index: int | None) -> dict[str, Any]:
    if (
        synthetic_cross_index is not None
        and context.get("reversalUnderMa40")
        and context.get("deathCrossIndex") is None
        and context.get("reversalIndex", -1) >= synthetic_cross_index
    ):
        context = dict(context)
        context["priorDeathCross"] = True
        context["deathCrossIndex"] = synthetic_cross_index
        context["deathCrossInferred"] = True
    return context


def analyze_intraday(candidate: dict[str, Any], current: dt.datetime, count: int) -> dict[str, Any]:
    rows = fetch_minute_rows(candidate["code"], count)
    bars = merge_30m_history(candidate, aggregate_30m(rows, current))
    signal_day = dt.date.fromisoformat(candidate["dailySignalDate"])
    tracking = candidate.setdefault("tracking", {})
    if tracking.get("ruleVersion") != TRACKING_RULE_VERSION:
        tracking.pop("breachedDailyMa10", None)
        tracking.pop("breachedMa60", None)
        tracking["ruleVersion"] = TRACKING_RULE_VERSION
    daily_reference = candidate.get("dailyReference", {})
    daily_ma10 = daily_reference.get("ma10")
    baseline_above = tracking.get("baselineMa20AboveMa40")
    if baseline_above is None:
        baseline_rows = [
            bar for bar in bars
            if bar["time"].date() <= signal_day and bar["ma20"] is not None and bar["ma40"] is not None
        ]
        if baseline_rows:
            baseline = baseline_rows[-1]
            baseline_above = baseline["ma20"] > baseline["ma40"]
            tracking["baselineMa20AboveMa40"] = baseline_above
            tracking["baselineTime"] = baseline["time"].isoformat(timespec="minutes")
    baseline_inferred, synthetic_cross_index = (False, None)
    if baseline_above is None:
        baseline_inferred, synthetic_cross_index = infer_missing_baseline(bars, signal_day)
    eligible = [index for index, bar in enumerate(bars) if bar["time"].date() > signal_day]
    found_index = None
    found_rule_override = None
    pending_ma60_confirmation = False
    for index in eligible:
        context = apply_inferred_cross(rise_context(bars, index), synthetic_cross_index)
        depth = depth_rule_context(bars, context, index, daily_reference)
        sequence_valid = bool(
            context["reversalUnderMa40"]
            and context["priorDeathCross"]
            and valid_post_candidate_sequence(
                bars, context, signal_day, baseline_above is True or baseline_inferred
            )
        )
        if sequence_valid and context.get("riseCount", 0) >= 5 and not depth["ma60Ready"]:
            pending_ma60_confirmation = True
        if (
            pending_ma60_confirmation
            and bars[index].get("ma20") is not None
            and bars[index].get("ma40") is not None
            and bars[index].get("ma60") is not None
            and bars[index]["ma20"] > bars[index]["ma40"]
            and bars[index]["ma20"] > bars[index]["ma60"]
        ):
            found_index = index
            found_rule_override = "ma60_recovery_inferred"
            break
        if (
            depth["signalReady"]
            and sequence_valid
        ):
            found_index = index
            break

    if not bars:
        return {"dataStatus": "no_data", "barCount": 0}
    latest_index = eligible[-1] if eligible else len(bars) - 1
    latest = bars[latest_index]
    latest_context = apply_inferred_cross(rise_context(bars, latest_index), synthetic_cross_index)
    latest_fall_count = consecutive_ma20_falls(bars, latest_index)
    latest_depth = depth_rule_context(bars, latest_context, latest_index, daily_reference)
    latest_daily_ma10 = daily_ma10_for_bar(daily_reference, latest)
    observed_daily_ma10_breach = bool(
        latest.get("ma20") is not None
        and latest_daily_ma10 is not None
        and latest["ma20"] < latest_daily_ma10
    )
    eligible_reversal = bool(
        eligible
        and valid_post_candidate_sequence(
            bars, latest_context, signal_day, baseline_above is True or baseline_inferred
        )
    )
    latest_context["eligibleReversal"] = eligible_reversal
    if eligible_reversal:
        tracking["breachedMa60"] = bool(
            tracking.get("breachedMa60") or latest_depth["breachedMa60"]
        )
    # Only persist events observed after the A-G confirmation day. The depth
    # context may reach back to an older death cross when no post-signal
    # sequence exists, which must not exclude a newly confirmed candidate.
    latest_depth["breachedMa60"] = bool(tracking.get("breachedMa60"))
    latest_depth["breachedDailyMa10"] = observed_daily_ma10_breach
    if latest_depth["breachedMa60"]:
        latest_depth["signalRule"] = "ma60_recovery"
        latest_depth["signalReady"] = bool(
            latest_depth["ma60Ready"]
            and latest_context.get("riseCount", 0) >= 5
            and latest_depth["recoveredMa60"]
        )
    if not eligible_reversal:
        latest_context["rawRiseCount"] = latest_context["riseCount"]
        latest_context["riseCount"] = 0
    first_complete_index = next(
        (
            index for index, bar in enumerate(bars)
            if None not in (bar.get("ma20"), bar.get("ma40"), bar.get("ma60"))
        ),
        None,
    )
    complete_display_bars = [] if first_complete_index is None else [
        bar for bar in bars[first_complete_index:] if bar["time"].date() >= signal_day
    ]
    display_bars = latest_trading_days(complete_display_bars, 5)
    result: dict[str, Any] = {
        "dataStatus": "ok",
        "barCount": len(bars),
        "lastBarTime": latest["time"].isoformat(timespec="minutes"),
        "lastPrice": latest["close"],
        "baselineMa20AboveMa40": baseline_above,
        "baselineInferred": baseline_inferred,
        "ma20": round(latest["ma20"], 2) if latest["ma20"] is not None else None,
        "ma40": round(latest["ma40"], 2) if latest["ma40"] is not None else None,
        "ma60": round(latest["ma60"], 2) if latest["ma60"] is not None else None,
        "dailyMa10": daily_ma10,
        "fallCount": latest_fall_count,
        "structuralExcluded": bool(
            latest_fall_count >= 3
            and None not in (latest.get("ma20"), latest.get("ma40"), latest.get("ma60"))
            and latest["ma20"] < latest["ma40"] < latest["ma60"]
        ),
        **latest_context,
        **latest_depth,
        "series": [
            {
                "t": bar["time"].isoformat(timespec="minutes"),
                "o": bar.get("open", bar["close"]),
                "h": bar.get("high", bar["close"]),
                "l": bar.get("low", bar["close"]),
                "c": bar["close"],
                "v": bar.get("volume", 0),
                "m20": round(bar["ma20"], 2) if bar["ma20"] is not None else None,
                "m40": round(bar["ma40"], 2) if bar["ma40"] is not None else None,
                "m60": round(bar["ma60"], 2) if bar["ma60"] is not None else None,
            }
            for bar in display_bars
        ],
    }
    if found_index is not None:
        signal_bar = bars[found_index]
        result.update(
            {
                "signalTime": signal_bar["time"].isoformat(timespec="minutes"),
                "signalPrice": signal_bar["close"],
                "signalMa20": round(signal_bar["ma20"], 2),
                "signalMa40": round(signal_bar["ma40"], 2),
                "signalMa60": round(signal_bar["ma60"], 2),
                "signalRule": found_rule_override or depth_rule_context(
                    bars,
                    apply_inferred_cross(rise_context(bars, found_index), synthetic_cross_index),
                    found_index,
                    daily_reference,
                )["signalRule"],
                "entryReference": bars[found_index + 1]["open"] if found_index + 1 < len(bars) else None,
            }
        )
    return result


def notify_ntfy(topic: str, candidate: dict[str, Any]) -> None:
    intraday = candidate["intraday"]
    tier_label = "핵심 후보" if candidate.get("candidateTier") == CORE_TIER else "확대 후보"
    message = (
        f"{candidate['name']}({candidate['code']})\n"
        f"후보등급: {tier_label}\n"
        f"확정봉 {intraday['signalTime']}\n"
        f"매수 포착가격: {intraday['signalPrice']:,}원\n"
        f"MA20 {intraday['signalMa20']:,.2f} / MA40 {intraday['signalMa40']:,.2f} / MA60 {intraday['signalMa60']:,.2f}\n"
        "다음 30분봉부터 HTS 현재가와 거래량을 확인하세요."
    )
    body = json.dumps(
        {
            "topic": topic,
            "title": f"🔴 [{tier_label}] {candidate['name']} 매수시점 포착",
            "message": message,
            "priority": 4,
            "tags": ["chart_with_upwards_trend"],
            "click": candidate["naverUrl"],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://ntfy.sh", data=body, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status >= 300:
            raise RuntimeError(f"ntfy 전송 실패: HTTP {response.status}")


def enrich(
    payload: dict[str, Any],
    current: dt.datetime,
    count: int,
    no_notify: bool,
    refresh_daily_chart: bool = False,
) -> tuple[int, int]:
    notified = set(payload.get("notifiedSignals", []))
    topic = os.getenv("NTFY_TOPIC", "").strip()
    new_alerts = 0
    pending = 0
    errors = 0
    for candidate in payload.get("candidates", []):
        if refresh_daily_chart:
            try:
                candidate["dailyChart"] = fetch_daily_chart(candidate["code"])
            except (OSError, TimeoutError, ValueError) as exc:
                candidate.setdefault(
                    "dailyChart",
                    {"dataStatus": "error", "error": str(exc), "series": []},
                )
        try:
            intraday = analyze_intraday(candidate, current, count)
        except (OSError, TimeoutError, ValueError) as exc:
            intraday = {"dataStatus": "error", "error": str(exc), "barCount": 0}
            errors += 1
        candidate["intraday"] = intraday
        if intraday.get("signalTime"):
            signal_at = dt.datetime.fromisoformat(intraday["signalTime"])
            signal_age = current - signal_at
            signal_is_fresh = dt.timedelta(0) <= signal_age <= dt.timedelta(minutes=90)
            candidate["status"] = "signal" if signal_is_fresh else "signaled"
            intraday["signalFresh"] = signal_is_fresh
            signature = f"{candidate['code']}|{candidate['dailySignalDate']}|{intraday['signalTime']}"
            candidate["signalSignature"] = signature
            if signature not in notified and signal_is_fresh:
                pending += 1
                if topic and not no_notify:
                    notify_ntfy(topic, candidate)
                    notified.add(signature)
                    new_alerts += 1
        elif intraday.get("structuralExcluded"):
            candidate["status"] = "excluded"
        elif (
            intraday.get("baselineMa20AboveMa40") is None
            and not intraday.get("baselineInferred")
        ):
            candidate["status"] = "insufficient"
        elif intraday.get("baselineMa20AboveMa40") is False:
            candidate["status"] = "ineligible"
        elif (
            intraday.get("breachedMa60")
            and not intraday.get("recoveredMa60")
            and intraday.get("riseCount", 0) >= 5
        ):
            candidate["status"] = "waiting60"
        elif intraday.get("ma60") is None:
            candidate["status"] = "insufficient"
        elif (
            0 < intraday.get("riseCount", 0) < 5
            and intraday.get("reversalUnderMa40")
            and intraday.get("priorDeathCross")
            and intraday.get("eligibleReversal")
        ):
            candidate["status"] = "rising"
        elif (
            intraday.get("eligibleReversal")
            and intraday.get("priorDeathCross")
            and intraday.get("ma20") is not None
            and intraday.get("ma40") is not None
            and intraday["ma20"] < intraday["ma40"]
        ):
            candidate["status"] = "setup"
        elif intraday.get("breachedMa60") and not intraday.get("recoveredMa60"):
            candidate["status"] = "waiting60"
        else:
            candidate["status"] = "watching"

    payload["notifiedSignals"] = sorted(notified)[-200:]
    status_counts = defaultdict(int)
    tier_counts = defaultdict(int)
    for candidate in payload.get("candidates", []):
        status_counts[candidate["status"]] += 1
        if candidate["status"] not in ("excluded", "ineligible"):
            tier_counts[candidate.get("candidateTier", CORE_TIER)] += 1
    payload["generatedAt"] = current.isoformat(timespec="seconds")
    payload["summary"] = {
        "active": sum(
            candidate["status"] not in ("excluded", "ineligible")
            for candidate in payload.get("candidates", [])
        ),
        "total": len(payload.get("candidates", [])),
        "coreCandidates": tier_counts[CORE_TIER],
        "expandedCandidates": tier_counts[EXPANDED_TIER],
        "signals": status_counts["signal"],
        "signalHistory": status_counts["signaled"],
        "setup": status_counts["setup"],
        "rising": status_counts["rising"],
        "watching": status_counts["watching"],
        "insufficient": status_counts["insufficient"],
        "ineligible": status_counts["ineligible"],
        "excluded": status_counts["excluded"],
        "waiting60": status_counts["waiting60"],
        "dataErrors": errors,
        "newAlerts": new_alerts,
        "pendingNotifications": pending - new_alerts,
        "pushConfigured": bool(topic),
    }
    return new_alerts, pending - new_alerts


def now_kst() -> dt.datetime:
    return dt.datetime.now(tz=KST)


def write_github_output(path: str | None, alerts: int, pending: int) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"alerts={alerts}\n")
        handle.write(f"pending={pending}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A-G 후보와 30분봉 MA 신호를 관리합니다.")
    parser.add_argument("--mode", choices=("daily", "intraday"), required=True)
    parser.add_argument("--stock-data", default="data/stock-data.json")
    parser.add_argument("--output", default="data/candidate-monitor.json")
    parser.add_argument("--minute-count", type=int, default=5000)
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--now", help="테스트용 KST ISO 시각")
    parser.add_argument("--github-output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = ROOT / args.output
    previous = load_previous(output)
    if args.mode == "daily":
        payload = build_daily_candidates(ROOT / args.stock_data, previous)
    else:
        payload = previous
        if not payload.get("candidates"):
            payload.setdefault("version", 1)
            payload.setdefault("strategy", STRATEGY)
            payload.setdefault("candidates", [])
            payload.setdefault("notifiedSignals", [])
    current = dt.datetime.fromisoformat(args.now).astimezone(KST) if args.now else now_kst()
    alerts, pending = enrich(
        payload,
        current,
        args.minute_count,
        args.no_notify,
        refresh_daily_chart=args.mode == "daily",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_github_output(args.github_output, alerts, pending)
    print(
        json.dumps(
            {"mode": args.mode, "active": len(payload["candidates"]), "alerts": alerts, "pending": pending},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
