"""Maintain AH candidates separately from the existing AG/AK monitor."""
import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import sys
import urllib.request

from ah_rules import (KST, MODEL, VERSION, daily_match, potential_match, hybrid_rows,
                      remaining_days, review_alerts, completed_at, message)
from notification_history import record_delivery
from update_candidate_monitor import fetch_display_chart, fetch_display_quote, stock_rows

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/ah-monitor.json"


def load_json(path, fallback):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def notify(topic, text):
    request = urllib.request.Request(f"https://ntfy.sh/{topic}", data=text.encode("utf-8"),
                                     headers={"Title": "AH stock review", "Tags": "chart_with_upwards_trend"}, method="POST")
    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.load(response)
    if not result.get("id"):
        raise RuntimeError("ntfy delivery was not acknowledged")


def extend_universe(market, payload, current, cache_root=None, refresh_universe=True):
    """The legacy bundle requires about 428 bars; AH needs only 105."""
    extra = payload.setdefault("universeExtra", {})
    today = current.date().isoformat()
    if refresh_universe and payload.get("universeCheckedDate") != today:
        cached_report = cache_root / "current-long-ma-pullback/report.json" if cache_root else None
        if cached_report and cached_report.exists():
            listing = load_json(cached_report, {})["allResults"]
        else:
            sys.path.insert(0, str(ROOT))
            import server
            listing = server.fetch_stock_list("all")
        existing = {stock["c"] for stock in market["stocks"]}
        missing = [stock for stock in listing if stock["code"] not in existing]
        unavailable = []
        def daily_history(stock):
            cached = cache_root / "daily-long-ma-pullback/charts" / f"{stock['code']}.json" if cache_root else None
            chart = load_json(cached, None) if cached else None
            if not chart:
                chart = fetch_display_chart(stock["code"], "day", current)
            return {"name": stock["name"], "market": stock.get("market", ""),
                    "history": [{key: row[key] for key in ("d", "c", "v")} for row in chart["history"]]}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            jobs = {executor.submit(daily_history, stock): stock["code"] for stock in missing}
            for job in concurrent.futures.as_completed(jobs):
                code = jobs[job]
                try:
                    extra[code] = job.result()
                except Exception:
                    unavailable.append(code)
        payload["universeErrors"] = unavailable
        payload["universeCheckedDate"] = today
    codes = {stock["c"] for stock in market["stocks"]}
    for code, stock in extra.items():
        if code not in codes:
            market["stocks"].append({"c": code, "n": stock["name"], "m": stock["market"], "_ahDaily": stock["history"]})


def refresh(stock, previous, current, cache_root=None):
    code = stock["c"]
    charts = {}
    for timeframe, cache_folder in (("day", "daily-long-ma-pullback"), ("minute30", "current-long-ma-pullback")):
        key = "daily" if timeframe == "day" else "intraday"
        cached = cache_root / cache_folder / "charts" / f"{code}.json" if cache_root else None
        chart = load_json(cached, None) if cached else None
        if chart is None:
            chart = fetch_display_chart(code, timeframe, current, previous.get("displayCharts", {}).get(key))
        if not chart or not chart.get("history"):
            raise RuntimeError(f"{key} chart unavailable")
        charts[key] = chart
    if cache_root:
        daily = charts["daily"]["history"]
        latest = max(daily, key=lambda row: row["d"])
        quote = {"price": latest["c"], "time": latest["d"] + "T20:00:00+09:00", "source": "cached_daily"}
    else:
        try:
            quote = fetch_display_quote(code, current)
        except Exception:
            quote = {"dataStatus": "error"}
    charts["quote"] = quote or {}
    charts["intraday"]["series"] = hybrid_rows(charts["intraday"]["history"], charts["daily"]["history"], current)
    charts["intraday"]["ahModel"] = MODEL
    charts["intraday"]["referenceRebounds"] = []
    return charts


def seed_signal_day(daily, native, days):
    # Bootstrap may include a transient intraday qualification even if the final close fails.
    earlier = [row["d"] for row in daily if row["d"] < days[0]]
    previous = daily_match(daily, max(earlier)) if earlier else False
    for day in days:
        matched = daily_match(daily, day) or (potential_match(daily, day) and any(
            row["complete"] and row["t"][:10] == day and daily_match(daily, day, row["c"]) for row in native))
        if matched and not previous:
            return day
        previous = matched
    return None


def update(payload, market, current, *, no_notify=False, bootstrap=False, charts_only=False,
           cache_root=None, sender=notify, refresh_fn=refresh):
    bootstrap = bootstrap or not payload.get("initializedAt")
    topic = os.environ.get("NTFY_TOPIC", "")
    stamp = current.isoformat(timespec="seconds")
    calendar = sorted(day for day in market["dates"] if day <= current.date().isoformat())
    if not calendar:
        raise ValueError("No available trading dates")
    live_session = current.weekday() < 5 and dt.time(9) <= current.time().replace(tzinfo=None) < dt.time(20)
    scan_day = current.date().isoformat() if live_session else calendar[-1]
    if scan_day not in calendar:
        if market.get("sessionDate") == scan_day:
            calendar.append(scan_day)
        else:
            live_session = False
            scan_day = calendar[-1]
    recent = calendar[-5:]
    registry = payload.setdefault("registry", {})
    existing = {item["code"]: item for item in payload.get("candidates", [])}
    stocks = {stock["c"]: stock for stock in market["stocks"]}
    selected = set(existing)
    potential = set()
    for code, stock in stocks.items():
        if charts_only:
            continue
        history = stock.get("_ahDaily") or [{"d": row["date"], "c": row["close"], "v": row["volume"]} for row in stock_rows(market, stock)]
        days = recent if bootstrap else [scan_day]
        if any(potential_match(history, day) for day in days):
            potential.add(code)
    selected.update(potential)
    candidates, errors = [], []
    refreshed = {}
    def get_charts(code):
        stock = stocks.get(code) or {"c": code}
        return refresh_fn(stock, existing.get(code, {}), current, cache_root)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        jobs = {executor.submit(get_charts, code): code for code in selected}
        for job in concurrent.futures.as_completed(jobs):
            code = jobs[job]
            try:
                refreshed[code] = job.result()
            except Exception as error:
                errors.append({"code": code, "message": str(error)[:180]})
    delivered = 0
    for code in sorted(selected):
        item = existing.get(code)
        record = registry.get(code, {})
        if item and remaining_days(item["dailySignalDate"], calendar) == 0:
            item = None
        if code not in refreshed:
            if item and remaining_days(item["dailySignalDate"], calendar) > 0:
                candidates.append(item)
            continue
        charts = refreshed[code]
        daily = charts["daily"]["history"]
        native = charts["intraday"]["series"]
        quote = charts["quote"]
        if quote.get("dataStatus") == "error":
            errors.append({"code": code, "message": "Quote unavailable; no live registration"})
        fresh_quote = (quote.get("price", 0) > 0 and quote.get("time", "")[:10] == scan_day)
        if live_session:
            try:
                age = current - dt.datetime.fromisoformat(quote.get("time", ""))
                fresh_quote = fresh_quote and dt.timedelta(minutes=-5) <= age <= dt.timedelta(minutes=30)
            except (ValueError, TypeError):
                fresh_quote = False
        qualified = daily_match(daily, scan_day, quote["price"]) if fresh_quote else False
        if not live_session:
            qualified = daily_match(daily, scan_day)
        signal_day = seed_signal_day(daily, native, recent) if bootstrap else scan_day if qualified else None
        if charts_only:
            signal_day = None
        # Do not reset a live registration or re-enroll a continuously matching expired stock.
        eligible = signal_day and not item and (bootstrap or not record.get("lastMatched", False))
        if eligible:
            stock = stocks[code]
            item = {"code": code, "name": stock["n"], "market": stock.get("m", ""),
                    "candidateTier": "ah", "dailySignalDate": signal_day, "registeredAt": stamp,
                    "initialImport": bootstrap,
                    "registrationPrice": quote.get("price"), "status": "watching",
                    "alertCursor": stamp if bootstrap else (current - dt.timedelta(microseconds=1)).isoformat(), "enrollmentPending": not bootstrap,
                    "enrollmentHandled": bootstrap}
        if not charts_only and (not live_session or fresh_quote):
            registry[code] = {**record, "lastMatched": qualified, "lastCheckedDate": scan_day,
                              "lastSignalDate": item["dailySignalDate"] if item else record.get("lastSignalDate")}
        if not item or remaining_days(item["dailySignalDate"], calendar) == 0:
            continue
        item["displayCharts"] = charts
        item["tradingDaysRemaining"] = remaining_days(item["dailySignalDate"], calendar)
        latest = next((row for row in reversed(native) if row["complete"]), None)
        item["intraday"] = {"lastPrice": quote.get("price") or (native[-1]["c"] if native else None),
                             "lastPriceTime": quote.get("time") or (native[-1]["t"] if native else None)}
        if not charts_only:
            alerts = review_alerts(item, native, calendar, current, item.get("enrollmentPending", False))
            failed = False
            if not bootstrap and not no_notify and topic:
                sent = set(payload.setdefault("sentKeys", []))
                for alert in alerts:
                    if alert["key"] in sent:
                        continue
                    prior = [row for row in daily if row["d"] < alert["bar"]["t"][:10]]
                    text = message(item, alert, prior[-1]["c"] if prior else None)
                    try:
                        sender(topic, text)
                    except Exception as error:
                        failed = True
                        errors.append({"code": code, "message": f"ntfy: {type(error).__name__}"})
                        break
                    sent.add(alert["key"])
                    payload["sentKeys"].append(alert["key"])
                    delivered += 1
                    item["lastReview"] = {"kind": alert["kind"], "barTime": alert["bar"]["t"], "sentAt": stamp}
                    record_delivery(payload, code=code, name=item["name"], bar_time=alert["bar"]["t"],
                                    sent_at=stamp, kind="ah_review", label="AH 매수검토", signature=alert["key"], stage=alert["kind"])
                if not failed:
                    item["enrollmentPending"] = False
                    item["enrollmentHandled"] = True
                    if latest:
                        item["alertCursor"] = completed_at(latest).isoformat(timespec="seconds")
            if bootstrap:
                item["alertCursor"] = stamp
            item["status"] = "signal" if item.get("lastReview", {}).get("barTime", "")[:10] == scan_day else "watching"
        candidates.append(item)
    if not charts_only:
        # Record a fixed-condition failure too, so a later real re-entry can enroll again.
        for code, record in registry.items():
            if code not in potential and code not in existing:
                record.update(lastMatched=False, lastCheckedDate=scan_day)
    payload.update(version=VERSION, model=MODEL, asOf=calendar[-1], generatedAt=stamp,
                   initializedAt=payload.get("initializedAt", stamp), candidates=candidates,
                   windowTradingDays=5, initialHistoryAlertsSuppressed=True,
                   summary={"active": len(candidates), "scanned": len(stocks), "potential": len(potential),
                            "dataErrors": len(errors) + len(payload.get("universeErrors", [])), "alertsSentThisRun": delivered, "pushConfigured": bool(topic)},
                   errors=errors, scanDay=scan_day)
    payload["sentKeys"] = payload.get("sentKeys", [])[-5000:]
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--charts-only", action="store_true")
    parser.add_argument("--cache-root", type=Path, help="Offline initial validation only; never used by workflows")
    args = parser.parse_args()
    if args.cache_root and not args.no_notify:
        parser.error("Offline caches require --no-notify")
    current = dt.datetime.now(KST)
    market = load_json(ROOT / "data/stock-data.json", None)
    if not market:
        raise SystemExit("Missing full-market stock-data.json")
    if current.weekday() < 5 and dt.time(9) <= current.time().replace(tzinfo=None) < dt.time(20):
        try:
            probe = fetch_display_quote("005930", current)
            if probe["time"][:10] == current.date().isoformat():
                market["sessionDate"] = current.date().isoformat()
        except Exception:
            pass
    saved = load_json(OUTPUT, {})
    if args.bootstrap:
        if not args.no_notify or saved.get("notificationHistory"):
            parser.error("Bootstrap requires --no-notify and no acknowledged alert history")
        saved["candidates"] = []
        saved["registry"] = {}
    extend_universe(market, saved, current, args.cache_root, not args.charts_only)
    payload = update(saved, market, current, no_notify=args.no_notify,
                     bootstrap=args.bootstrap, charts_only=args.charts_only, cache_root=args.cache_root)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=True))


if __name__ == "__main__":
    main()
