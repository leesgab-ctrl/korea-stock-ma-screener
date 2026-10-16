"""Explicit MA rules linked to confirmed study images, not pixel similarity."""
import datetime as dt
import json
import math
from pathlib import Path
import urllib.request

KST = dt.timezone(dt.timedelta(hours=9))
RULES_PATH = Path(__file__).resolve().parents[1] / "data/image-search-rules.json"
BOARD_URL = "https://raw.githubusercontent.com/leesgab-ctrl/korea-stock-ma-screener/codex/study-images/data/study-images.json"
KEYS = ("m3", "m10", "m20", "m40", "m60")
RULE_TYPES = ("decline_ma40_recovery", "decline_ma10_ma40", "rising_cluster_ma3", "ma3_ma120_recovery")


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value)
    return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed.astimezone(KST)


def load_board(current):
    request = urllib.request.Request(f"{BOARD_URL}?t={int(current.timestamp())}", headers={"User-Agent": "StockMonitor/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.load(response)
    if value.get("version") != 1 or not isinstance(value.get("records"), list):
        raise ValueError("Invalid study image index")
    return value


def completed_rows(chart, current):
    result = {}
    for row in chart.get("series", []):
        try:
            start = timestamp(row["t"])
            if row.get("complete") is not True or start + dt.timedelta(minutes=30) > current:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        result[start] = row
    return [result[key] for key in sorted(result)]


def valid(row):
    return all(isinstance(row.get(key), (int, float)) and not isinstance(row[key], bool)
               and math.isfinite(row[key]) and row[key] > 0 for key in (*KEYS, "c"))


def detect_rising_cluster(rows, rule):
    dip = recovery = cluster = episode = None
    events = []
    for i, row in enumerate(rows):
        previous = rows[i - 1] if i else None
        missing_bar = previous and timestamp(row["t"]).date() == timestamp(previous["t"]).date() \
            and timestamp(row["t"]) - timestamp(previous["t"]) != dt.timedelta(minutes=30)
        if not valid(row) or previous and (not valid(previous) or missing_bar):
            dip = recovery = cluster = episode = None
            continue
        if not previous:
            continue
        if previous["m3"] >= previous["m60"] and row["m3"] < row["m60"]:
            dip = i
            recovery = None
        if dip is not None:
            if i - dip > rule["recoveryMaxBars"]:
                dip = None
            elif previous["m3"] <= previous["m60"] and row["m3"] > row["m60"]:
                recovery = {"index": i, "time": row["t"], "bars": i - dip}
                dip = None
        if recovery and i - recovery["index"] > rule["recoveryLookback"]:
            recovery = None
        trend_window = rows[max(0, i - rule["trendComparisons"]):i + 1]
        trend_ready = len(trend_window) == rule["trendComparisons"] + 1 \
            and all(valid(bar) for bar in trend_window) \
            and all(timestamp(a["t"]).date() != timestamp(b["t"]).date()
                    or timestamp(b["t"]) - timestamp(a["t"]) == dt.timedelta(minutes=30)
                    for a, b in zip(trend_window, trend_window[1:]))
        rising_background = trend_ready and row["m40"] > row["m60"] \
            and all(b[key] > a[key] for a, b in zip(trend_window, trend_window[1:]) for key in ("m40", "m60"))
        nonfalling_background = trend_ready \
            and all(b[key] >= a[key] for a, b in zip(trend_window, trend_window[1:]) for key in ("m40", "m60"))
        flat_window = rows[max(0, i - rule["flatBars"] + 1):i + 1]
        flat_spread = None
        if len(flat_window) == rule["flatBars"] and all(valid(bar) for bar in flat_window):
            values = [bar["m20"] for bar in flat_window]
            flat_spread = (max(values) - min(values)) / min(values) * 100
        values = [row[key] for key in ("m3", "m10", "m20", "m40")]
        spread = (max(values) - min(values)) / min(values) * 100
        values.append(row["m60"])
        five_spread = (max(values) - min(values)) / min(values) * 100
        flat = flat_spread is not None and flat_spread <= rule["flatMaxPercent"]
        if flat and nonfalling_background and five_spread < rule["clusterMaxPercent"]:
            cluster = {"index": i, "time": row["t"], "spread": five_spread, "lines": [3, 10, 20, 40, 60]}
        elif flat and rising_background and spread < rule["clusterMaxPercent"]:
            cluster = {"index": i, "time": row["t"], "spread": spread, "lines": [3, 10, 20, 40]}
        reference = episode["cluster"] if episode else cluster
        uptrend = nonfalling_background if reference and len(reference["lines"]) == 5 else rising_background
        stage = None
        if episode and (row["m3"] <= previous["m3"] or not uptrend):
            episode = None
        if episode:
            if not episode["done"]:
                episode["done"] = True
                if i == episode["index"] + 1 and uptrend and row["m3"] > previous["m3"]:
                    stage = "confirmation"
        elif flat and uptrend and cluster and i - cluster["index"] <= 1 and i >= 2 \
                and previous["m3"] <= rows[i - 2]["m3"] and row["m3"] > previous["m3"]:
            episode = {"index": i, "time": row["t"], "cluster": cluster, "done": False}
            stage = "primary"
        if stage:
            events.append({"id": f"{rule['id']}|{episode['time']}|{stage}", "ruleId": rule["id"],
                           "ruleVersion": rule["version"], "stage": stage, "signalType": "ma3Rise",
                           "stageLabel": f"{len(episode['cluster']['lines'])}선 밀집 · "
                           + ("MA3 1봉 상승 · 1차" if stage == "primary" else "MA3 2봉 연속 상승 · 2차"),
                           "riseCount": 1 if stage == "primary" else 2, "time": row["t"],
                           "completedAt": (timestamp(row["t"]) + dt.timedelta(minutes=30)).isoformat(timespec="minutes"),
                           "setupTime": episode["cluster"]["time"], "episodeTime": episode["time"], "price": row["c"],
                           "clusterLines": episode["cluster"]["lines"],
                           "clusterSpreadPercent": episode["cluster"]["spread"],
                           "currentSpreadPercent": five_spread if len(episode["cluster"]["lines"]) == 5 else spread,
                           "flatSpreadPercent": flat_spread, "fastRecovery": bool(recovery),
                           "recoveryTime": recovery["time"] if recovery else None,
                           "recoveryBars": recovery["bars"] if recovery else None, **{key: row[key] for key in KEYS}})
    return events


def detect_ma120_recovery(rows, rule):
    dip = episode = None
    events = []
    previous = None
    for row in rows:
        valid_row = all(isinstance(row.get(key), (int, float)) and not isinstance(row[key], bool)
                        and math.isfinite(row[key]) and row[key] > 0 for key in ("m3", "m120", "c"))
        if not valid_row:
            dip = episode = previous = None
            continue
        missing_bar = previous and timestamp(row["t"]).date() == timestamp(previous["t"]).date() \
            and timestamp(row["t"]) - timestamp(previous["t"]) != dt.timedelta(minutes=30)
        if missing_bar:
            dip = episode = previous = None
        stage = None
        if episode:
            if row["m3"] >= row["m120"]:
                stage = "confirmation"
            confirmed_episode = episode
            episode = None
        else:
            confirmed_episode = None
        if row["m3"] < row["m120"] and dip is None:
            dip = row["t"]
        if previous and dip and previous["m3"] <= previous["m120"] and row["m3"] > row["m120"]:
            episode = {"time": row["t"], "setupTime": dip}
            dip = None
            stage = "primary"
        event_episode = episode if stage == "primary" else confirmed_episode
        if stage:
            events.append({"id": f"{rule['id']}|{event_episode['time']}|{stage}", "ruleId": rule["id"],
                           "ruleVersion": rule["version"], "stage": stage, "signalType": "ma3Ma120",
                           "stageLabel": "MA3·MA120 상향 돌파 · 1차" if stage == "primary" else "다음 봉 MA3 ≥ MA120 유지 · 2차 확인",
                           "time": row["t"], "completedAt": (timestamp(row["t"]) + dt.timedelta(minutes=30)).isoformat(timespec="minutes"),
                           "setupTime": event_episode["setupTime"], "episodeTime": event_episode["time"], "price": row["c"],
                           "m3": row["m3"], "m120": row["m120"]})
        previous = row
    return events


def detect(rows, rule):
    if rule["type"] == "ma3_ma120_recovery":
        return detect_ma120_recovery(rows, rule)
    if rule["type"] == "rising_cluster_ma3":
        return detect_rising_cluster(rows, rule)
    if rule["type"] not in RULE_TYPES:
        return []
    comparisons, lookback = rule["declineComparisons"], rule["setupLookback"]
    setup = episode = None
    events = []
    for i, row in enumerate(rows):
        if not valid(row):
            setup = episode = None
            continue
        previous = rows[i - 1] if i else None
        if not previous or not valid(previous):
            continue
        window = rows[max(0, i - comparisons):i + 1]
        decline = len(window) == comparisons + 1 and all(valid(bar) and bar["m20"] < bar["m40"] < bar["m60"] for bar in window)
        decline = decline and all(b[key] < a[key] for a, b in zip(window, window[1:]) for key in ("m20", "m40", "m60"))
        if decline:
            setup = {"index": i, "time": window[0]["t"]}
        if episode and (row["m3"] <= row["m10"] or row["m3"] <= row["m40"] or i - episode["index"] > lookback):
            episode = None
            # A failed attempt needs a new falling setup, not another bounce in the same attempt.
            if not decline:
                setup = None
        cross3 = previous["m3"] <= previous["m40"] and row["m3"] > row["m40"]
        cross10 = previous["m10"] <= previous["m40"] and row["m10"] > row["m40"]
        stage = None
        if rule["type"] == "decline_ma10_ma40":
            if cross10 and setup and i - setup["index"] <= lookback and row["m20"] < row["m40"] < row["m60"] \
                    and row["m10"] > previous["m10"] and row["m3"] > row["m40"]:
                episode = {"index": i, "time": row["t"], "setupTime": setup["time"], "confirmed": True}
                stage = "confirmation"
                setup = None
        elif cross3 and setup and i - setup["index"] <= lookback and row["m20"] < row["m40"] < row["m60"] \
                and row["m3"] > previous["m3"] and row["m10"] > previous["m10"] and row["m10"] <= row["m40"]:
            episode = {"index": i, "time": row["t"], "setupTime": setup["time"], "confirmed": False}
            stage = "primary"
        elif episode and not episode["confirmed"] and cross10 and row["m10"] > previous["m10"] and row["m3"] > row["m40"]:
            episode["confirmed"] = True
            stage = "confirmation"
        if stage:
            events.append({"id": f"{rule['id']}|{episode['time']}|{stage}", "ruleId": rule["id"],
                           "ruleVersion": rule["version"], "stage": stage, "time": row["t"],
                           "completedAt": (timestamp(row["t"]) + dt.timedelta(minutes=30)).isoformat(timespec="minutes"),
                           "setupTime": episode["setupTime"], "episodeTime": episode["time"], "price": row["c"],
                           **{key: row[key] for key in KEYS}})
    return events


def send_alert(topic, candidate, payload, row, event, current):
    from color_notifications import send
    kind = "imageMa120Recovery" if event.get("signalType") == "ma3Ma120" else \
        "imageMa3Rise" if event.get("signalType") == "ma3Rise" else \
        "imageMa3Breakout" if event["stage"] == "primary" else "imageMa10Breakout"
    send(topic, candidate, payload, row, event, kind, current)


def process(payload, current, topic="", no_notify=True, board=None, rules=None, sender=send_alert):
    current = current.astimezone(KST)
    rules = rules if rules is not None else json.loads(RULES_PATH.read_text(encoding="utf-8"))["rules"]
    before = json.dumps(payload.get("imageSearchNotificationState", {}), sort_keys=True)
    tracking = payload.setdefault("imageSearchNotificationState", {})
    snapshot = {"version": 1, "updatedAt": current.isoformat(timespec="seconds"), "status": "ok", "scope": "active_ak_ag",
                "rules": [], "matches": [], "events": [], "errors": [], "candidateCount": 0}
    try:
        board = load_board(current) if board is None else board
        if board.get("version") != 1 or not isinstance(board.get("records"), list):
            raise ValueError("Invalid study image index")
        records = {item["id"]: item for item in board["records"] if isinstance(item, dict) and isinstance(item.get("id"), str)
                   and not item.get("deletedAt") and item.get("conditionConfirmed") is True}
    except (OSError, ValueError, TypeError) as exc:
        snapshot.update(status="error", errors=[f"이미지 조건 목록 조회 실패: {type(exc).__name__}"])
        payload["imageSearch"] = snapshot
        return 0, before != json.dumps(tracking, sort_keys=True)
    candidates = [item for item in payload.get("candidates", []) if item.get("candidateTier", "core") in ("core", "expanded_ak")]
    snapshot["candidateCount"] = len(candidates)
    count = 0
    jobs = {}
    delivered = {key: value for saved in tracking.values() for key, value in saved.get("sent", {}).items()}
    active_keys = set()
    for rule in rules:
        images = [records[key] for key in rule["imageIds"] if key in records]
        active = rule.get("enabled") is True and bool(images) and rule.get("type") in RULE_TYPES
        summary = {key: rule[key] for key in ("id", "version", "name", "description", "imageIds")}
        summary.update(active=active, enabled=rule.get("enabled") is True, confirmedImageIds=[item["id"] for item in images])
        snapshot["rules"].append(summary)
        if not active:
            continue
        try:
            enabled_at = min(timestamp(item.get("conditionConfirmedAt") or item["updatedAt"]) for item in images)
        except (TypeError, ValueError, KeyError):
            summary["active"] = False
            snapshot["errors"].append("확정 시각이 올바르지 않아 해당 조건의 알림을 중지했습니다.")
            continue
        for candidate in candidates:
            registered = candidate.get("registeredAt") or candidate.get("dailySignalDate")
            try:
                registration = timestamp(registered)
            except (TypeError, ValueError):
                continue
            chart = candidate.get("displayCharts", {}).get("intraday", {})
            rows = completed_rows(chart, current)
            if not rows:
                continue
            events = [event for event in detect(rows, rule) if timestamp(event["time"]) >= registration]
            for event in events:
                event.update(code=candidate["code"], name=candidate.get("name", candidate["code"]),
                             ruleName=rule["name"], sourceImageIds=[item["id"] for item in images])
            snapshot["events"].extend(events)
            if events:
                latest = {**events[-1], "dataStatus": chart.get("dataStatus", "unknown")}
                snapshot["matches"].append(latest)
            key = f"{candidate['code']}|{registered}|{rule['id']}|{rule['version']}"
            active_keys.add(key)
            saved = tracking.setdefault(key, {"baseline": rows[-1]["t"], "sent": {}})
            if chart.get("dataStatus") != "ok":
                continue
            # Regular and aftermarket bars use the same completed-bar and freshness rules.
            if no_notify or not topic or current.weekday() >= 5:
                continue
            by_time = {row["t"]: row for row in rows}
            for event in events:
                completion = timestamp(event["completedAt"])
                start = timestamp(event["time"])
                age = current - completion
                delivery = f"{candidate['code']}|{registered}|{event['time']}|{event['stage']}"
                if event.get("signalType") in ("ma3Rise", "ma3Ma120"):
                    delivery += "|" + event["signalType"]
                if start <= timestamp(saved["baseline"]) or completion < enabled_at or delivery in saved["sent"] \
                        or completion.date() != current.date() or not dt.time(9) <= start.time() <= dt.time(20) \
                        or not dt.timedelta(0) <= age <= dt.timedelta(minutes=90):
                    continue
                jobs.setdefault(delivery, []).append((candidate, by_time[event["time"]], event, saved))
    blocked = set()
    for delivery, entries in sorted(jobs.items(), key=lambda item: item[1][0][2]["completedAt"]):
        candidate, row, event, _ = entries[0]
        if candidate["code"] in blocked:
            continue
        if delivery not in delivered:
            combined = {**event, "ruleName": " / ".join(dict.fromkeys(entry[2]["ruleName"] for entry in entries))}
            try:
                sender(topic, candidate, payload, row, combined, current)
            except Exception as exc:
                snapshot["errors"].append(f"{candidate['code']} {event['stage']} 알림 전송 실패: {type(exc).__name__}")
                blocked.add(candidate["code"])
                continue
            delivered[delivery] = current.isoformat(timespec="seconds")
            count += 1
        for _, _, _, saved in entries:
            saved["sent"][delivery] = delivered[delivery]
    for key in list(tracking):
        if key not in active_keys:
            del tracking[key]
    snapshot["matches"].sort(key=lambda event: event["completedAt"], reverse=True)
    snapshot["events"].sort(key=lambda event: event["completedAt"], reverse=True)
    payload["imageSearch"] = snapshot
    return count, before != json.dumps(tracking, sort_keys=True)
