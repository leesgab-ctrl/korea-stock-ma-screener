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
STRATEGY = {
    "candidateWindowTradingDays": 10,
    "minuteTimeframe": "30분봉",
    "maRule": "MA20 5회 상승, MA60 침범 시 MA60 재돌파, 일봉 MA10 하회 시 제외",
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
    checks = {
        "A": values["volumeSpikePct"] >= 200,
        "B": values["spikeClosePct"] >= 3,
        "C": -10 <= values["pullbackClosePct"] <= 0,
        "D": rows[pullback]["volume"] < rows[spike]["volume"],
        "E": values["signalClosePct"] >= 1,
        "F": rows[index]["volume"] > rows[pullback]["volume"],
        "G": -10 <= values["preSpikeVsMa20Pct"] <= 5,
    }
    if not all(checks.values()):
        return None
    return {
        "close": rows[index]["close"],
        "spikeDate": rows[spike]["date"],
        "preSpikeDate": rows[pre_spike]["date"],
        "preSpikeClose": rows[pre_spike]["close"],
        "checks": checks,
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
        candidate = {
                "code": stock["c"],
                "name": stock["n"],
                "market": stock["m"],
                "dailySignalDate": signal_date,
                "tradingDayAge": age,
                "tradingDaysRemaining": 10 - age,
                "daily": result,
                "dailyReference": {
                    "close": rows[-1]["close"],
                    "ma10": round(sum(row["close"] for row in rows[-10:]) / 10, 2),
                    "date": rows[-1]["date"],
                },
                "status": "watching",
                "naverUrl": f"https://stock.naver.com/domestic/stock/{stock['c']}/price",
            }
        prior = previous_candidates.get((stock["c"], signal_date), {})
        if prior.get("tracking"):
            candidate["tracking"] = prior["tracking"]
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


def depth_rule_context(
    bars: list[dict[str, Any]], context: dict[str, Any], index: int, daily_ma10: float | None
) -> dict[str, Any]:
    death_cross_index = context.get("deathCrossIndex")
    if death_cross_index is None or daily_ma10 is None:
        return {
            "ma60Ready": False,
            "breachedMa60": False,
            "recoveredMa60": False,
            "breachedDailyMa10": False,
            "signalRule": None,
            "signalReady": False,
        }
    path = bars[death_cross_index : index + 1]
    ma60_ready = bool(path and all(bar.get("ma60") is not None for bar in path))
    breached_ma60 = bool(
        ma60_ready and any(bar["ma20"] < bar["ma60"] for bar in path)
    )
    breached_daily_ma10 = any(
        bar.get("ma20") is not None and bar["ma20"] < daily_ma10 for bar in path
    )
    latest = bars[index]
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
        and not breached_daily_ma10
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


def analyze_intraday(candidate: dict[str, Any], current: dt.datetime, count: int) -> dict[str, Any]:
    rows = fetch_minute_rows(candidate["code"], count)
    bars = aggregate_30m(rows, current)
    signal_day = dt.date.fromisoformat(candidate["dailySignalDate"])
    tracking = candidate.setdefault("tracking", {})
    daily_ma10 = candidate.get("dailyReference", {}).get("ma10")
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
    eligible = [index for index, bar in enumerate(bars) if bar["time"].date() > signal_day]
    found_index = None
    for index in eligible:
        context = rise_context(bars, index)
        depth = depth_rule_context(bars, context, index, daily_ma10)
        if (
            depth["signalReady"]
            and context["reversalUnderMa40"]
            and context["priorDeathCross"]
            and valid_post_candidate_sequence(bars, context, signal_day, baseline_above is True)
        ):
            found_index = index
            break

    if not bars:
        return {"dataStatus": "no_data", "barCount": 0}
    latest_index = eligible[-1] if eligible else len(bars) - 1
    latest = bars[latest_index]
    latest_context = rise_context(bars, latest_index)
    latest_depth = depth_rule_context(bars, latest_context, latest_index, daily_ma10)
    observed_daily_ma10_breach = bool(
        daily_ma10 is not None
        and any(
            bars[index].get("ma20") is not None and bars[index]["ma20"] < daily_ma10
            for index in eligible
        )
    )
    eligible_reversal = bool(
        eligible
        and valid_post_candidate_sequence(bars, latest_context, signal_day, baseline_above is True)
    )
    latest_context["eligibleReversal"] = eligible_reversal
    if observed_daily_ma10_breach:
        tracking["breachedDailyMa10"] = True
    if eligible_reversal:
        tracking["breachedMa60"] = bool(
            tracking.get("breachedMa60") or latest_depth["breachedMa60"]
        )
    # Only persist events observed after the A-G confirmation day. The depth
    # context may reach back to an older death cross when no post-signal
    # sequence exists, which must not exclude a newly confirmed candidate.
    latest_depth["breachedMa60"] = bool(tracking.get("breachedMa60"))
    latest_depth["breachedDailyMa10"] = bool(tracking.get("breachedDailyMa10"))
    if latest_depth["breachedMa60"]:
        latest_depth["signalRule"] = "ma60_recovery"
        latest_depth["signalReady"] = bool(
            latest_depth["ma60Ready"]
            and latest_context.get("riseCount", 0) >= 5
            and latest_depth["recoveredMa60"]
            and not latest_depth["breachedDailyMa10"]
        )
    if not eligible_reversal:
        latest_context["rawRiseCount"] = latest_context["riseCount"]
        latest_context["riseCount"] = 0
    result: dict[str, Any] = {
        "dataStatus": "ok",
        "barCount": len(bars),
        "lastBarTime": latest["time"].isoformat(timespec="minutes"),
        "lastPrice": latest["close"],
        "baselineMa20AboveMa40": baseline_above,
        "ma20": round(latest["ma20"], 2) if latest["ma20"] is not None else None,
        "ma40": round(latest["ma40"], 2) if latest["ma40"] is not None else None,
        "ma60": round(latest["ma60"], 2) if latest["ma60"] is not None else None,
        "dailyMa10": daily_ma10,
        **latest_context,
        **latest_depth,
        "series": [
            {
                "t": bar["time"].isoformat(timespec="minutes"),
                "c": bar["close"],
                "m20": round(bar["ma20"], 2) if bar["ma20"] is not None else None,
                "m40": round(bar["ma40"], 2) if bar["ma40"] is not None else None,
                "m60": round(bar["ma60"], 2) if bar["ma60"] is not None else None,
            }
            for bar in bars[-60:]
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
                "signalRule": depth_rule_context(
                    bars, rise_context(bars, found_index), found_index, daily_ma10
                )["signalRule"],
                "entryReference": bars[found_index + 1]["open"] if found_index + 1 < len(bars) else None,
            }
        )
    return result


def notify_ntfy(topic: str, candidate: dict[str, Any]) -> None:
    intraday = candidate["intraday"]
    message = (
        f"{candidate['name']}({candidate['code']})\n"
        f"확정봉 {intraday['signalTime']}\n"
        f"매수 포착가격: {intraday['signalPrice']:,}원\n"
        f"MA20 {intraday['signalMa20']:,.2f} / MA40 {intraday['signalMa40']:,.2f} / MA60 {intraday['signalMa60']:,.2f}\n"
        "다음 30분봉부터 HTS 현재가와 거래량을 확인하세요."
    )
    body = json.dumps(
        {
            "topic": topic,
            "title": f"🔴 {candidate['name']} 매수시점 포착",
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


def enrich(payload: dict[str, Any], current: dt.datetime, count: int, no_notify: bool) -> tuple[int, int]:
    notified = set(payload.get("notifiedSignals", []))
    topic = os.getenv("NTFY_TOPIC", "").strip()
    new_alerts = 0
    pending = 0
    errors = 0
    for candidate in payload.get("candidates", []):
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
        elif intraday.get("breachedDailyMa10"):
            candidate["status"] = "excluded"
        elif intraday.get("baselineMa20AboveMa40") is None:
            candidate["status"] = "insufficient"
        elif intraday.get("baselineMa20AboveMa40") is False:
            candidate["status"] = "ineligible"
        elif intraday.get("breachedMa60") and not intraday.get("recoveredMa60"):
            candidate["status"] = "waiting60"
        elif intraday.get("ma60") is None:
            candidate["status"] = "insufficient"
        elif (
            intraday.get("riseCount", 0) > 0
            and intraday.get("reversalUnderMa40")
            and intraday.get("priorDeathCross")
            and intraday.get("eligibleReversal")
        ):
            candidate["status"] = "rising"
        else:
            candidate["status"] = "watching"

    payload["notifiedSignals"] = sorted(notified)[-200:]
    status_counts = defaultdict(int)
    for candidate in payload.get("candidates", []):
        status_counts[candidate["status"]] += 1
    payload["generatedAt"] = current.isoformat(timespec="seconds")
    payload["summary"] = {
        "active": len(payload.get("candidates", [])),
        "signals": status_counts["signal"],
        "signalHistory": status_counts["signaled"],
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
    alerts, pending = enrich(payload, current, args.minute_count, args.no_notify)
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
