"""Scan volume-qualified stocks, then join the existing common monitor."""
import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import urllib.request

from first_rise_rules import (KST, VERSION, CONFIRM_TIME, WINDOW_DAYS, daily_context, detect,
                              remaining_days, registration_message, after_close, closing_confirmation, confirmed)
from ah_rules import hybrid_rows
from update_candidate_monitor import fetch_bytes, fetch_display_chart, fetch_display_quote, stock_rows
from notification_history import record_delivery
from update_ah_monitor import extend_universe

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/first-rise-monitor.json"


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def volume_snapshot():
    result = {}
    for market in ("KOSPI", "KOSDAQ"):
        page, count, total = 1, 0, 1
        while count < total:
            response = json.loads(fetch_bytes(f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100"))
            total = int(response["totalCount"])
            stocks = response["stocks"]
            if not stocks:
                raise ValueError("Incomplete market volume snapshot")
            count += len(stocks)
            for stock in stocks:
                try:
                    volume = float(str(stock.get("accumulatedTradingVolumeRaw", stock["accumulatedTradingVolume"])).replace(",", ""))
                    result[stock["itemCode"]] = {"volume": volume, "day": stock["localTradedAt"][:10]}
                except (KeyError, ValueError, TypeError):
                    continue
            page += 1
    return result


def refresh(stock, previous, current, cache_root=None):
    charts = {}
    for key, timeframe, folder in (("daily", "day", "daily-long-ma-pullback"),
                                    ("intraday", "minute30", "current-long-ma-pullback")):
        cached = cache_root / folder / "charts" / f"{stock['c']}.json" if cache_root else None
        charts[key] = load(cached, None) if cached else None
        if charts[key] is None:
            charts[key] = fetch_display_chart(stock["c"], timeframe, current, previous.get("displayCharts", {}).get(key))
        if charts[key].get("dataStatus") != "ok" or not charts[key].get("history"):
            raise ValueError(f"{key} native chart unavailable")
    charts["intraday"]["series"] = hybrid_rows(charts["intraday"]["history"], [], current)
    if cache_root:
        daily = charts["daily"]["history"]
        last, prior = daily[-1], daily[-2]
        charts["quote"] = {"source": "cached_daily", "dataStatus": "ok", "price": last["c"],
                           "previousClose": prior["c"], "change": 100*(last["c"]/prior["c"]-1),
                           "time": last["d"] + "T20:00:00+09:00"}
    else:
        charts["quote"] = fetch_display_quote(stock["c"], current)
    return charts


def notify(topic, message):
    request = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                                     headers={"Title": "First-rise observation", "Tags": "chart_with_upwards_trend"}, method="POST")
    with urllib.request.urlopen(request, timeout=25) as response:
        receipt = json.load(response)
    if not receipt.get("id"):
        raise ValueError("ntfy delivery was not acknowledged")


def confirm_chart(charts, day, current):
    daily = charts["daily"]
    if daily.get("dataStatus") != "ok":
        return None
    if day == current.date().isoformat() and daily.get("marketStatus") != "CLOSE":
        return None
    quote = charts.get("quote", {})
    close = next((r.get("c") for r in daily["history"] if r["d"] == day), None)
    if quote.get("dataStatus") == "ok" and quote.get("time", "")[:10] == day and close != quote.get("price"):
        return None
    return closing_confirmation(daily["history"], day, current, quote)


def update(payload, market, current, *, volumes=None, no_notify=False, charts_only=False,
           cache_root=None, refresh_fn=refresh, sender=notify, excluded=()):
    stamp = current.isoformat(timespec="seconds")
    bootstrap = not payload.get("initializedAt")
    calendar = sorted(set(d for d in market["dates"] if d <= current.date().isoformat()))
    today = current.date().isoformat()
    if volumes:
        calendar = sorted(set(calendar) | {v["day"] for v in volumes.values() if v["day"] <= today})
    if not calendar:
        raise ValueError("No available trading session")
    day = calendar[-1]
    allow_new = (not charts_only and current.weekday() < 5 and day == today and after_close(day, current))
    blocked = set(excluded)
    existing = {x["code"]: x for x in payload.get("candidates", []) + payload.get("pendingCloseValidation", [])}
    history = payload.setdefault("history", [])
    for record in sorted(history, key=lambda entry: entry.get("dailySignalDate", ""), reverse=True):
        if (record.get("archiveReason") == "first_day_close_below_5pct"
                and record.get("reviewedUnderVersion") != VERSION and record["code"] not in blocked
                and remaining_days(record["dailySignalDate"], calendar) > 0):
            existing.setdefault(record["code"], {key: value for key, value in record.items()
                                                if key not in ("archivedAt", "archiveReason")})
    stocks = {s["c"]: s for s in market["stocks"]}
    selected = set(existing) - blocked
    scanned = 0
    for code, stock in stocks.items():
        if not allow_new or code in blocked:
            continue
        rows = stock.get("_ahDaily") or [{"d": r["date"], "c": r["close"], "v": r["volume"]} for r in stock_rows(market, stock)]
        context = daily_context(rows, day)
        if context is None:
            continue
        scanned += 1
        snapshot = (volumes or {}).get(code)
        if snapshot is None and (cache_root or day != today):
            end = next((r for r in rows if r["d"] == day), None)
            snapshot = {"volume": end["v"], "day": day} if end else None
        # This prefilter is monotone: later volume cannot hide an earlier qualification.
        if (snapshot and snapshot["day"] == day and snapshot["volume"] > 0 and context["prior19Volume"] > 0
                and snapshot["volume"] * 17 >= context["prior19Volume"] * 3):
            selected.add(code)
    refreshed, errors = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        jobs = {executor.submit(refresh_fn, stocks.get(code, {"c": code}), existing.get(code, {}), current, cache_root): code
                for code in selected}
        for job in concurrent.futures.as_completed(jobs):
            code = jobs[job]
            try:
                refreshed[code] = job.result()
            except Exception as error:
                errors.append({"code": code, "message": str(error)[:180]})
    registry = payload.setdefault("registry", {})
    candidates, additions, pending, sent = [], [], [], 0
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    for code in sorted(selected | set(existing)):
        item = existing.get(code)
        if item and (code in blocked or remaining_days(item["dailySignalDate"], calendar) == 0):
            history.append({**item, "archivedAt": stamp, "archiveReason": "manual_excluded" if code in blocked else "window_completed"})
            item = None
        if code in blocked:
            continue
        charts = refreshed.get(code)
        if not charts:
            if item:
                (candidates if confirmed(item) else pending).append(item)
            continue
        if item and (not confirmed(item) or not item["closeConfirmation"].get("previousCloseSource")):
            validation = confirm_chart(charts, item["dailySignalDate"], current)
            if validation is None:
                pending.append(item)
                errors.append({"code": code, "message": "First-day closing price validation pending"})
                continue
            for record in history:
                if (record.get("archiveReason") == "first_day_close_below_5pct" and record["code"] == code
                        and record.get("registeredAt") == item.get("registeredAt")
                        and record["dailySignalDate"] == item["dailySignalDate"]):
                    record.update(reviewedUnderVersion=VERSION, reviewedAt=stamp, revisedQualification=validation["qualified"])
            if not validation["qualified"]:
                history.append({**item, "closeConfirmation": validation, "archivedAt": stamp,
                                "archiveReason": "first_day_daily_ma3_cross10_not_met", "registrationNoticePending": False})
                continue
            item["closeConfirmation"] = validation
            item["registrationNoticePending"] = False
        close = confirm_chart(charts, day, current) if allow_new else None
        if allow_new and close is None:
            errors.append({"code": code, "message": "Final daily close unavailable or market not closed"})
        signals = detect(charts["intraday"]["history"], charts["daily"]["history"], day, current) if close and close["qualified"] else []
        if not item and signals and registry.get(code, {}).get("lastSignalDate") != day:
            stock = stocks[code]
            item = {"code": code, "name": stock["n"], "market": stock.get("m", ""), "candidateTier": "first_rise",
                    "dailySignalDate": day, "registeredAt": stamp, "registrationPrice": charts["quote"].get("price"),
                    "initialImport": bootstrap, "status": "watching", "firstRiseSignal": signals[0],
                    "registrationNoticePending": not bootstrap and day == today, "closeConfirmation": close}
            registry[code] = {"lastSignalDate": day, "registeredAt": stamp}
            additions.append({k: item[k] for k in ("code", "name", "candidateTier", "dailySignalDate", "registeredAt")})
        if not item:
            continue
        item["displayCharts"] = charts
        item["tradingDaysRemaining"] = remaining_days(item["dailySignalDate"], calendar)
        item["intraday"] = {"lastPrice": charts["quote"].get("price"), "lastPriceTime": charts["quote"].get("time")}
        if item.get("registrationNoticePending") and not no_notify and not charts_only and topic and allow_new:
            if item["registeredAt"][:10] != today:
                item["registrationNoticePending"] = False
            else:
                try:
                    sender(topic, registration_message(item))
                except Exception as error:
                    errors.append({"code": code, "message": f"ntfy: {type(error).__name__}"})
                else:
                    item["registrationNoticePending"] = False
                    item["registrationNoticeSentAt"] = stamp
                    sent += 1
                    record_delivery(payload, code, item["name"], item["firstRiseSignal"]["barTime"], stamp,
                                    "firstRiseRegistration", "장마감 첫날상승 확정", signature=f"first-rise|{code}|{item['registeredAt']}")
        candidates.append(item)
    payload.update(version=VERSION, initializedAt=payload.get("initializedAt", stamp), generatedAt=stamp, asOf=day,
                   windowTradingDays=WINDOW_DAYS, candidates=candidates, pendingCloseValidation=pending, history=history[-1000:], errors=errors,
                   candidateScan={"startedAt": stamp, "completedAt": stamp, "asOf": day, "checkedStocks": scanned,
                                  "newRegistrations": additions, "currentDayData": day == today,
                                  "closeOnly": True, "registrationAllowed": allow_new},
                   summary={"active": len(candidates), "scanned": scanned, "volumeQualified": len(selected),
                            "dataErrors": len(errors) + len(payload.get("universeErrors", [])), "pushConfigured": bool(topic), "alertsSentThisRun": sent})
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--charts-only", action="store_true")
    parser.add_argument("--cache-root", type=Path)
    args = parser.parse_args()
    if args.cache_root and not args.no_notify:
        parser.error("Offline caches require --no-notify")
    current = dt.datetime.now(KST)
    market = load(ROOT / "data/stock-data.json", {})
    scan_window = current.weekday() < 5 and current.time().replace(tzinfo=None) >= CONFIRM_TIME and not args.charts_only
    volumes = volume_snapshot() if scan_window and not args.cache_root else None
    exclusions = load(ROOT / "data/candidate-exclusions.json", {}).get("excluded", [])
    saved = load(OUTPUT, {})
    extend_universe(market, saved, current, args.cache_root, scan_window)
    payload = update(saved, market, current, volumes=volumes, no_notify=args.no_notify, charts_only=args.charts_only,
                     cache_root=args.cache_root, excluded=[x["code"] for x in exclusions])
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(payload["summary"]))


if __name__ == "__main__":
    main()
