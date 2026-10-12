"""One opening-only MA3 alert per previous-session core stock and trading date."""
import datetime as dt
import json
from pathlib import Path
import subprocess

from color_notifications import send

KST = dt.timezone(dt.timedelta(hours=9))


def opening_checks(payload, today):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("core_morning_signals.cjs")), today],
        input=json.dumps(payload, ensure_ascii=False), text=True, encoding="utf-8",
        capture_output=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def notify(payload, current, topic, no_notify=False, sender=send, evaluator=opening_checks):
    current = current.astimezone(KST)
    # The 10:00 collection may arrive late. Retry only that fixed opening snapshot,
    # never count any subsequent candles or send the opening alert after 11:00.
    if no_notify or not topic or current.weekday() >= 5 or current.hour != 10:
        return 0, False
    today = current.date().isoformat()
    try:
        checks = evaluator(payload, today)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        payload.setdefault("coreMorningNotificationErrors", []).append(str(exc))
        return 0, True
    tracking = payload.setdefault("coreMorningNotificationState", {})
    day = tracking.setdefault(today, {})
    changed = False
    count = 0
    candidates = {item["code"]: item for item in payload.get("candidates", [])}
    for check in checks:
        code = check["code"]
        if code not in candidates or not check["ready"] or day.get(code, {}).get("status") in ("sent", "not_rising"):
            continue
        record = {key: check[key] for key in ("previousDate", "previousMa3", "firstMa3", "secondMa3")}
        record["evaluatedAt"] = current.isoformat(timespec="seconds")
        if not check["matched"]:
            day[code] = {**record, "status": "not_rising"}
            changed = True
            continue
        try:
            sender(topic, candidates[code], payload, check["row"], check, "coreMorningMa3", current)
        except Exception as exc:
            day[code] = {**record, "status": "retry", "error": str(exc)}
            changed = True
            continue
        day[code] = {**record, "status": "sent", "sentAt": current.isoformat(timespec="seconds")}
        count += 1
        changed = True
    for date in sorted(tracking)[:-20]:
        del tracking[date]
        changed = True
    return count, changed
