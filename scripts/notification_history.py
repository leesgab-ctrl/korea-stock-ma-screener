"""Delivery receipts for chart annotations; detected-only signals never enter this log."""
import datetime as dt


def record_delivery(payload, code, name, bar_time, sent_at, kind, label, *, signature=None, stage="", source="ntfy"):
    try:
        bar = dt.datetime.fromisoformat(bar_time)
        sent = dt.datetime.fromisoformat(sent_at)
        if bar.tzinfo is None or sent.tzinfo is None or bar > sent:
            return False
    except (TypeError, ValueError):
        return False
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        return False
    key = signature or f"{code}|{kind}|{bar.isoformat()}|{stage}"
    records = payload.setdefault("notificationHistory", [])
    if any(item.get("id") == key for item in records):
        return False
    records.append({"id": key, "code": code, "name": name, "barTime": bar.isoformat(timespec="minutes"),
                    "sentAt": sent.isoformat(timespec="seconds"), "kind": kind, "label": label,
                    "stage": stage, "source": source})
    records.sort(key=lambda item: item["sentAt"])
    del records[:-5000]
    return True


def restore_delivery_receipts(payload):
    """Only historical states containing an explicit successful-delivery timestamp qualify."""
    changed = False
    events = payload.get("imageSearch", {}).get("events", [])
    names = {item["code"]: item.get("name", item["code"])
             for item in payload.get("candidates", []) + payload.get("history", [])}
    for saved in payload.get("imageSearchNotificationState", {}).values():
        for signature, sent_at in saved.get("sent", {}).items():
            parts = signature.split("|")
            if len(parts) < 4:
                continue
            code, _, bar_time, stage, *signal = parts
            matching = [event for event in events if event.get("code") == code and event.get("time") == bar_time
                        and event.get("stage") == stage and (not signal or event.get("signalType") == signal[0])]
            label = " / ".join(dict.fromkeys(event.get("ruleName", "이미지조건") for event in matching)) or "이미지조건 알림"
            changed = record_delivery(payload, code, names.get(code, code), bar_time, sent_at,
                                      "image", label, signature=signature, stage=stage,
                                      source="saved_delivery_receipt") or changed
    for day, saved in payload.get("coreMorningNotificationState", {}).items():
        for code, record in saved.items():
            if record.get("status") == "sent":
                changed = record_delivery(payload, code, names.get(code, code), f"{day}T09:30:00+09:00",
                                          record.get("sentAt"), "coreMorningMa3", "핵심종목 아침 MA3 상승") or changed
    return changed
