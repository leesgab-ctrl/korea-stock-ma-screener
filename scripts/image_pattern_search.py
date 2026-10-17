"""Explicit MA rules linked to confirmed study images, not pixel similarity."""
import datetime as dt
import json
import math
from pathlib import Path
import urllib.request
from notification_prices import day_change_percent

KST = dt.timezone(dt.timedelta(hours=9))
RULES_PATH = Path(__file__).resolve().parents[1] / "data/image-search-rules.json"
BOARD_URL = "https://raw.githubusercontent.com/leesgab-ctrl/korea-stock-ma-screener/codex/study-images/data/study-images.json"
KEYS = ("m3", "m10", "m20", "m40", "m60")
RULE_TYPES = ("decline_ma40_recovery", "decline_ma10_ma40", "rising_cluster_ma3", "ma3_ma120_recovery", "ma20_pyramid_ma60_recovery", "ma3_ma60_near_rebound")


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
    episode = None
    events, history = [], []
    lookback = rule.get("belowLookbackBars", 5)
    for row in rows:
        valid_row = all(isinstance(row.get(key), (int, float)) and not isinstance(row[key], bool)
                        and math.isfinite(row[key]) and row[key] > 0 for key in ("m3", "m60", "m120", "c"))
        if not valid_row:
            episode, history = None, []
            continue
        previous = history[-1] if history else None
        missing_bar = previous and timestamp(row["t"]).date() == timestamp(previous["t"]).date() \
            and timestamp(row["t"]) - timestamp(previous["t"]) != dt.timedelta(minutes=30)
        if missing_bar:
            episode, history, previous = None, [], None
        background = previous and row["m120"] > previous["m120"] and row["m60"] > row["m120"]
        stage = None
        if episode:
            if background and row["m3"] >= row["m120"]:
                stage = "confirmation"
            confirmed_episode = episode
            episode = None
        else:
            confirmed_episode = None
        if background and previous["m3"] <= previous["m120"] and row["m3"] > row["m120"]:
            # The recent below-line observation matters, not when a long dip first began.
            below = next((history[j] for j in range(len(history) - 1, max(0, len(history) - lookback) - 1, -1)
                          if j > 0 and history[j]["m3"] < history[j]["m120"]
                          and history[j]["m60"] > history[j]["m120"]
                          and history[j]["m120"] > history[j - 1]["m120"]), None)
            if below:
                episode = {"time": row["t"], "setupTime": below["t"]}
                stage = "primary"
        event_episode = episode if stage == "primary" else confirmed_episode
        if stage:
            events.append({"id": f"{rule['id']}|{event_episode['time']}|{stage}", "ruleId": rule["id"],
                           "ruleVersion": rule["version"], "stage": stage, "signalType": "ma3Ma120",
                           "stageLabel": "MA3·MA120 상향 돌파 · 1차" if stage == "primary" else "다음 봉 MA3 ≥ MA120 유지 · 2차 확인",
                           "time": row["t"], "completedAt": (timestamp(row["t"]) + dt.timedelta(minutes=30)).isoformat(timespec="minutes"),
                           "setupTime": event_episode["setupTime"], "episodeTime": event_episode["time"], "price": row["c"],
                           "belowLookbackBars": lookback, "m120Previous": previous["m120"],
                           "m3": row["m3"], "m60": row["m60"], "m120": row["m120"]})
        history.append(row)
        history = history[-(lookback + 1):]
    return events


def pyramid_setup(rows, start, end, rule):
    """Use only the already observed rise and fall, never the later rebound."""
    minimum, maximum = rule["minLegBars"], rule["maxLegBars"]
    for peak in range(end - minimum, max(start + minimum, end - maximum) - 1, -1):
        if not rows[peak - 1]["m20"] < rows[peak]["m20"] >= rows[peak + 1]["m20"]:
            continue
        left = min(range(max(start, peak - maximum), peak - minimum + 1), key=lambda j: rows[j]["m20"])
        up, down = rows[left:peak + 1], rows[peak:end + 1]
        rise = (up[-1]["m20"] / up[0]["m20"] - 1) * 100
        fall = (1 - down[-1]["m20"] / down[0]["m20"]) * 100
        rise_bars, fall_bars = len(up) - 1, len(down) - 1
        rise_speed, fall_speed = rise / rise_bars, fall / fall_bars
        if rise < rule["minRisePercent"] or fall < rule["minFallPercent"] \
                or fall_speed < rise_speed * rule["minFallSpeedRatio"]:
            continue
        if sum(b["m20"] > a["m20"] for a, b in zip(up, up[1:])) / rise_bars < rule["minDirectionalRatio"] \
                or sum(b["m20"] < a["m20"] for a, b in zip(down, down[1:])) / fall_bars < rule["minDirectionalRatio"]:
            continue
        day = timestamp(up[0]["t"]).date()
        baseline = next((rows[j]["m20"] for j in range(left - 1, start - 1, -1)
                         if timestamp(rows[j]["t"]).date() != day), None)
        day_rise, rise_day = 0, None
        for j, bar in enumerate(up):
            current_day = timestamp(bar["t"]).date()
            if current_day != day:
                day, baseline = current_day, up[j - 1]["m20"]
            change = (bar["m20"] / baseline - 1) * 100 if baseline else 0
            if change > day_rise:
                day_rise, rise_day = change, day.isoformat()
        if day_rise + 1e-9 < rule["minRiseDayPercent"]:
            continue
        return {"peakTime": rows[peak]["t"], "setupTime": rows[left]["t"], "risePercent": rise,
                "fallPercent": fall, "riseBars": rise_bars, "fallBars": fall_bars,
                "riseDayPercent": day_rise, "riseDay": rise_day,
                "strongRise": day_rise + 1e-9 >= rule["strongRiseDayPercent"]}
    return None


def detect_pyramid_recovery(rows, rule):
    events, used_peaks = [], set()
    segment_start, dip, episode = 0, None, None
    for i, row in enumerate(rows):
        previous = rows[i - 1] if i else None
        missing = previous and timestamp(row["t"]).date() == timestamp(previous["t"]).date() \
            and timestamp(row["t"]) - timestamp(previous["t"]) != dt.timedelta(minutes=30)
        if not valid(row):
            segment_start, dip, episode = i + 1, None, None
            continue
        if missing or previous and not valid(previous):
            segment_start, dip, episode = i, None, None
        if i == segment_start:
            continue
        trend = rows[max(segment_start, i - rule["trendComparisons"]):i + 1]
        rising60 = len(trend) == rule["trendComparisons"] + 1 \
            and all(b["m60"] > a["m60"] for a, b in zip(trend, trend[1:]))
        if previous["m3"] >= previous["m60"] and row["m3"] < row["m60"]:
            dip = row["t"]
        if i - segment_start < 2:
            continue
        cross = previous["m3"] <= previous["m60"] and row["m3"] > row["m60"] and row["m3"] > previous["m3"]
        stage, simultaneous = None, False
        if episode:
            if not rising60 or row["m3"] < previous["m3"] or i - episode["index"] > rule["confirmationMaxBars"]:
                episode = None
            elif cross:
                stage = "confirmation"
        turning_up = previous["m3"] <= rows[i - 2]["m3"] and row["m3"] > previous["m3"]
        if not episode and dip and rising60 and turning_up and previous["m3"] < previous["m60"]:
            setup = pyramid_setup(rows, segment_start, i, rule)
            if setup and setup["peakTime"] not in used_peaks:
                episode = {**setup, "index": i, "time": row["t"], "dipTime": dip}
                used_peaks.add(setup["peakTime"])
                stage = "confirmation" if cross else "primary"
                simultaneous = cross
        if stage:
            events.append({"id": f"{rule['id']}|{episode['time']}|{stage}", "ruleId": rule["id"],
                           "ruleVersion": rule["version"], "stage": stage, "signalType": "ma20Pyramid",
                           "stageLabel": "MA60 아래 MA3 상승 전환 · 1차" if stage == "primary" else "MA3·MA60 상향 돌파 · 확인",
                           "time": row["t"], "completedAt": (timestamp(row["t"]) + dt.timedelta(minutes=30)).isoformat(timespec="minutes"),
                           "episodeTime": episode["time"], "price": row["c"], "simultaneous": simultaneous,
                           **{key: episode[key] for key in ("setupTime", "peakTime", "dipTime", "risePercent", "fallPercent", "riseBars", "fallBars", "riseDayPercent", "riseDay", "strongRise")},
                           **{key: row[key] for key in KEYS}})
            if stage == "confirmation":
                episode = None
        if row["m3"] >= row["m60"]:
            dip = None
    return events


def detect_ma60_near_rebound(rows, rule):
    events, history, episode = [], [], None
    limit = rule["maxGapPercent"]
    watch_bars = rule.get("watchBelowBars", 0)
    dip, below_window, watched = None, [], False
    for row in rows:
        valid_row = all(isinstance(row.get(key), (int, float)) and not isinstance(row[key], bool)
                        and math.isfinite(row[key]) and row[key] > 0 for key in ("m3", "m60", "c"))
        if not valid_row:
            history, episode = [], None
            dip, below_window, watched = None, [], False
            continue
        previous = history[-1] if history else None
        # Native aftermarket charts omit no-trade slots; do not fabricate bars or discard their MA continuity.
        missing = previous and timestamp(row["t"]).date() == timestamp(previous["t"]).date() \
            and timestamp(previous["t"]).time() < dt.time(15, 30) \
            and timestamp(row["t"]) - timestamp(previous["t"]) != dt.timedelta(minutes=30)
        if missing:
            history, episode, previous = [], None, None
            dip, below_window, watched = None, [], False
        gap = (row["m3"] / row["m60"] - 1) * 100
        near = abs(gap) <= limit + 1e-9
        stage, simultaneous = None, False
        cross = previous and previous["m3"] <= previous["m60"] and row["m3"] > row["m60"] \
            and row["m3"] > previous["m3"]
        if episode:
            if not near or row["m3"] < previous["m3"]:
                episode = None
            elif cross:
                stage = "confirmation"
        turning_up = len(history) >= 2 and previous["m3"] <= history[-2]["m3"] and row["m3"] > previous["m3"]
        previous_gap = (previous["m3"] / previous["m60"] - 1) * 100 if previous else None
        if not episode and turning_up and near \
                and previous["m3"] < previous["m60"]:
            episode = {"time": row["t"], "setupTime": previous["t"], "setupGapPercent": previous_gap}
            stage = "confirmation" if cross else "primary"
            simultaneous = bool(cross)
        if stage:
            events.append({"id": f"{rule['id']}|{episode['time']}|{stage}", "ruleId": rule["id"],
                           "ruleVersion": rule["version"], "stage": stage, "signalType": "ma3Ma60Near",
                           "stageLabel": "피라미드형3MA반등 · 1차" if stage == "primary" else "MA3·MA60 상향 돌파 · 확인",
                           "time": row["t"], "completedAt": (timestamp(row["t"]) + dt.timedelta(minutes=30)).isoformat(timespec="minutes"),
                           "episodeTime": episode["time"], "setupTime": episode["setupTime"],
                           "setupGapPercent": episode["setupGapPercent"], "gapPercent": gap, "maxGapPercent": limit,
                           "price": row["c"], "m3": row["m3"], "m60": row["m60"], "simultaneous": simultaneous})
            if stage == "confirmation" or row["m3"] > row["m60"]:
                episode = None
        if row["m3"] >= row["m60"]:
            dip, below_window, watched = None, [], False
        elif previous and previous["m3"] >= previous["m60"]:
            dip, below_window, watched = row["t"], [], False
        if watch_bars and dip:
            if near:
                below_window.append(row)
                below_window = below_window[-watch_bars:]
            else:
                below_window = []
            if len(below_window) == watch_bars and not watched:
                watched = True
                # A rebound/crossover notification on this candle already supersedes the early watch.
                if not stage:
                    first = below_window[0]
                    events.append({"id": f"{rule['id']}|{dip}|watch", "ruleId": rule["id"],
                                   "ruleVersion": rule["version"], "stage": "watch", "signalType": "ma3Ma60Near",
                                   "stageLabel": "피라미드형 상승가능성 검토요청",
                                   "time": row["t"], "completedAt": (timestamp(row["t"]) + dt.timedelta(minutes=30)).isoformat(timespec="minutes"),
                                   "episodeTime": dip, "downCrossTime": dip, "setupTime": first["t"],
                                   "setupGapPercent": (first["m3"] / first["m60"] - 1) * 100,
                                   "gapPercent": gap, "maxGapPercent": limit, "nearBelowBars": watch_bars,
                                   "watchBars": [{"time": bar["t"], "m3": bar["m3"], "m60": bar["m60"],
                                                  "gapPercent": (bar["m3"] / bar["m60"] - 1) * 100} for bar in below_window],
                                   "price": row["c"], "m3": row["m3"], "m60": row["m60"], "simultaneous": False})
        history.append(row)
        history = history[-2:]
    return events


def detect(rows, rule):
    if rule["type"] == "ma3_ma60_near_rebound":
        return detect_ma60_near_rebound(rows, rule)
    if rule["type"] == "ma20_pyramid_ma60_recovery":
        return detect_pyramid_recovery(rows, rule)
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
    kind = "imageMa60NearWatch" if event.get("signalType") == "ma3Ma60Near" and event.get("stage") == "watch" else \
        "imageMa60NearRebound" if event.get("signalType") == "ma3Ma60Near" else \
        "imagePyramidRecovery" if event.get("signalType") == "ma20Pyramid" else \
        "imageMa120Recovery" if event.get("signalType") == "ma3Ma120" else \
        "imageMa3Rise" if event.get("signalType") == "ma3Rise" else \
        "imageMa3Breakout" if event["stage"] == "primary" else "imageMa10Breakout"
    send(topic, candidate, payload, row, event, kind, current)


def process(payload, current, topic="", no_notify=True, board=None, rules=None, sender=send_alert):
    current = current.astimezone(KST)
    rules = rules if rules is not None else json.loads(RULES_PATH.read_text(encoding="utf-8"))["rules"]
    before = json.dumps(payload.get("imageSearchNotificationState", {}), sort_keys=True)
    previous_events = {(event.get("code"), event.get("id")): event for event in payload.get("imageSearch", {}).get("events", [])}
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
        for key in ("thumbnail", "stageThumbnails"):
            if rule.get(key):
                summary[key] = rule[key]
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
                quote = candidate.get("displayCharts", {}).get("quote", {})
                change = day_change_percent(event["price"], event["time"], quote)
                if change is None:
                    saved_event = previous_events.get((candidate["code"], event["id"]), {})
                    quote = {"time": saved_event.get("time"), "previousClose": saved_event.get("previousClose")}
                    change = day_change_percent(event["price"], event["time"], quote)
                event["changePct"] = round(change, 6) if change is not None else None
                event["previousClose"] = quote.get("previousClose") if change is not None else None
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
                if event.get("signalType") in ("ma3Rise", "ma3Ma120", "ma20Pyramid", "ma3Ma60Near"):
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
