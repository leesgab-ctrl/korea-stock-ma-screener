"""Completed native bars only. Paper fills are assumptions, not broker orders."""
import datetime as dt


def evaluate(candidate):
    chart = candidate.get("displayCharts", {}).get("intraday", {})
    bars = [b for b in chart.get("series", []) if b.get("complete")]
    daily = candidate.get("daily", {})
    spike = daily.get("spikeDate", candidate.get("dailySignalDate", ""))
    sessions = sorted({b["t"][:10] for b in bars if b["t"][:10] > spike}
                      | {b["d"] for b in candidate.get("displayCharts", {}).get("daily", {}).get("history", []) if b["d"] > spike})
    low = daily.get("preSpikeLow")
    registered = candidate.get("registeredAt")
    registration_time = dt.datetime.fromisoformat(registered) if registered else None
    if low is None:
        prior = next((b for b in candidate.get("displayCharts", {}).get("daily", {}).get("history", [])
                      if b["d"] == daily.get("preSpikeDate")), None)
        low = prior.get("l") if prior else None
    crossed = None
    deepest = False
    bottom = None
    attempt = False
    first_bottom = None
    rise_count = 0
    pullback = None
    events = []
    reason = None
    for i, b in enumerate(bars):
        if i == 0 or b["t"][:10] <= spike or registration_time is None or dt.datetime.fromisoformat(b["t"]) < registration_time:
            continue
        p = bars[i - 1]
        if not all(isinstance(x.get(k), (int, float)) for x in (p, b) for k in ("m3", "m10", "m20", "m40", "m60")):
            continue
        if low and b["m60"] > low and b["c"] < low:
            reason = "대량거래 전일 저가 이탈"
            break
        if dt.datetime.fromisoformat(p["t"]) < registration_time:
            continue
        down = p["m20"] >= p["m40"] and b["m20"] < b["m40"]
        if down and crossed is None:
            coverage_start = bars[0]["t"][:10] if bars else ""
            if sessions.index(b["t"][:10]) >= 4 and coverage_start <= spike and registration_time.date().isoformat() <= spike:
                reason = "4거래일 이후 조정 시작"
                break
            crossed = i
            bottom = b["l"]
        if crossed is not None:
            deepest |= b["m20"] < b["m60"]
            target = "m60" if deepest else "m40"
            rising = b["m20"] > p["m20"]
            rise_count = rise_count + 1 if rising else 0
            if rising and not attempt:
                first_bottom = bottom
                attempt = True
            if attempt and not rising and b["m20"] < b[target] and b["c"] < first_bottom:
                reason = "첫 회복 실패 후 조정 저점 이탈"
                break
            bottom = min(bottom, b["l"])
            # A fresh MA20 reclaim, followed by one completed holding bar.
            if i >= 2:
                q = bars[i - 2]
                if (q.get(target) is not None and q.get("m20") is not None
                        and q["m20"] <= q[target] and p["m20"] > p[target]
                        and b["m20"] > b[target] and rising and rise_count >= 5
                        and b["c"] > b[target] and b["m20"] > b["m40"] > b["m60"]):
                    events.append({"time": b["t"], "type": "recovery", "target": "MA60" if deepest else "MA40",
                                   "price": b["c"], "low": bottom})
                    crossed = None
                    attempt = False
                    deepest = False
        # Reference cycle: MA10 down, both MA3 and MA10 reclaim MA20.
        ordered = b["m20"] > b["m40"] > b["m60"]
        if pullback and (b["t"][:10] != pullback["day"] or b["m20"] <= b["m40"]
                         or b["m40"] <= p["m40"] or b["m60"] <= p["m60"]):
            pullback = None
        if not pullback and p["m10"] >= p["m20"] and b["m10"] < b["m20"]:
            yesterday = [x for x in bars[:i] if x["t"][:10] < b["t"][:10] and x.get("m20") is not None]
            previous_day = yesterday[-1]["t"][:10] if yesterday else None
            prior = [x for x in yesterday if x["t"][:10] == previous_day]
            flat = i >= 3 and bars[i - 3].get("m20") and abs(b["m20"] / bars[i - 3]["m20"] - 1) <= .002
            if (ordered and flat and b["m20"] / b["m40"] - 1 <= .01 and b["m40"] > p["m40"] and b["m60"] > p["m60"]
                    and len(prior) >= 2 and prior[-1]["m20"] > prior[0]["m20"]):
                pullback = {"day": b["t"][:10], "low": b["l"]}
        if pullback:
            pullback["low"] = min(pullback["low"], b["l"])
            both = b["m3"] > b["m20"] and b["m10"] > b["m20"]
            fresh = p["m3"] <= p["m20"] or p["m10"] <= p["m20"]
            if ordered and both and fresh:
                events.append({"time": b["t"], "type": "pullback", "target": "MA20",
                               "price": b["c"], "low": pullback["low"]})
                pullback = None
    patterns = events
    events = [event for event in patterns if registration_time is not None
              and dt.datetime.fromisoformat(event["time"]) >= registration_time]
    last = bars[-1] if bars else {}
    if (registration_time is None or not last or dt.datetime.fromisoformat(last["t"]) < registration_time
            or not all(isinstance(last.get(k), (int, float)) for k in ("m20", "m40"))):
        return {"group": "insufficient", "excludedReason": reason, "events": events, "historicalPatterns": patterns, "preSpikeLow": low}
    recent = [b for b in bars if dt.datetime.fromisoformat(b["t"]) >= registration_time][-4:]
    rising_structure = (len(recent) == 4 and all(b.get("m20") is not None and b.get("m40") is not None and b["m20"] > b["m40"] for b in recent)
                        and all(recent[i]["m40"] > recent[i - 1]["m40"] for i in range(1, 4)))
    group = "target" if crossed is not None and last["m20"] < last["m40"] else "reference" if rising_structure else "unclassified"
    return {"group": group, "excludedReason": reason, "events": events, "historicalPatterns": patterns, "preSpikeLow": low}


def update_paper(payload, current, notify=None):
    paper = payload.setdefault("paperTrading", {"positions": [], "seen": [], "startedAt": current.isoformat()})
    seen = set(paper["seen"])
    baseline = dt.datetime.fromisoformat(paper["startedAt"])
    records = {c["code"]: c for c in payload.get("history", [])}
    records.update({c["code"]: c for c in payload.get("candidates", [])})
    for candidate in payload.get("candidates", []):
        result = evaluate(candidate)
        candidate["paperStrategy"] = result
        candidate["status"] = "watching"
        chart = candidate.get("displayCharts", {}).get("intraday", {})
        if chart.get("dataStatus") != "ok":
            continue
        for event in result["events"]:
            key = f"{candidate['code']}|{candidate.get('dailySignalDate')}|{event['type']}|{event['time']}"
            if key in seen:
                continue
            end = dt.datetime.fromisoformat(event["time"]) + dt.timedelta(minutes=30)
            if end <= baseline or end > current or current - end > dt.timedelta(minutes=90):
                seen.add(key)
                continue
            price, stop = event["price"], event["low"]
            if not 0 < stop < price:
                seen.add(key)
                continue
            if any(p["code"] == candidate["code"] and p["status"] == "open" for p in paper["positions"]):
                seen.add(key)
                continue
            position = {"id": key, "code": candidate["code"], "name": candidate["name"], "mode": "virtual",
                        "strategyType": event["type"], "buyPrice": price, "quantity": 1, "investedAmount": price,
                        "adjustmentLow": stop, "stopPrice": stop, "stopPct": round((1 - stop / price) * 100, 2),
                        "targetPrice": round(price * 1.05, 2), "targetPct": 5,
                        "openedAt": current.isoformat(), "signalTime": event["time"], "entryBarEnd": end.isoformat(),
                        "lastPrice": price, "status": "open", "maxDrawdownPct": 0, "fillAssumption": "signal_close"}
            paper["positions"].append(position)
            seen.add(key)
            if notify:
                notify(candidate, position)
    for position in paper["positions"]:
        if position["status"] != "open":
            continue
        candidate = records.get(position["code"])
        if not candidate:
            continue
        for b in candidate.get("displayCharts", {}).get("intraday", {}).get("history", []):
            if b["t"] < position["openedAt"] or b["t"] <= position.get("lastEvaluatedBar", ""):
                continue
            if dt.datetime.fromisoformat(b["t"]) + dt.timedelta(minutes=30) > current:
                continue
            position["lastEvaluatedBar"] = b["t"]
            position["lastPrice"] = b["c"]
            position["maxDrawdownPct"] = min(position["maxDrawdownPct"], round((b["l"] / position["buyPrice"] - 1) * 100, 2))
            stop, target = b["l"] <= position["stopPrice"], b["h"] >= position["targetPrice"]
            if stop or target:
                position["status"] = "closed"
                position["closedAt"] = b["t"]
                position["exitReason"] = "ambiguous" if stop and target else "stop" if stop else "target"
                # Conservative outcome, gaps filled at the worse opening price.
                position["sellPrice"] = min(b["o"], position["stopPrice"]) if stop else position["targetPrice"]
                position["returnPct"] = round((position["sellPrice"] / position["buyPrice"] - 1) * 100, 2)
                break
    paper["seen"] = sorted(seen)
    paper["summary"] = {
        "open": sum(p["status"] == "open" for p in paper["positions"]),
        "closed": sum(p["status"] == "closed" for p in paper["positions"]),
        "target": sum(p.get("exitReason") == "target" for p in paper["positions"]),
        "stop": sum(p.get("exitReason") == "stop" for p in paper["positions"]),
        "ambiguous": sum(p.get("exitReason") == "ambiguous" for p in paper["positions"]),
    }
    paper["historicalExamples"] = [
        {"code": c["code"], "name": c["name"], "label": "과거 검토", **event}
        for c in payload.get("candidates", []) for event in c.get("paperStrategy", {}).get("historicalPatterns", [])
        if dt.datetime.fromisoformat(event["time"]) + dt.timedelta(minutes=30) <= baseline
    ]
