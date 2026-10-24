import argparse
import json
import os

from update_candidate_monitor import EXPANDED_TIER, ROOT, archive_candidate, now_kst, update_validation_summary


def manage(payload, registry, action, code, reason, current, calendar=None, category=None):
    exclusions = registry.setdefault("excluded", [])
    if action == "exclude":
        category = category if category in ("chart_shape", "other") else ("chart_shape" if "차트 형태" in reason else "other")
        candidate = next((c for c in payload.get("candidates", []) + payload.get("analysisCandidates", []) if c["code"] == code), None)
        if not candidate:
            raise ValueError("Candidate not found; refresh the page first")
        exclusions[:] = [r for r in exclusions if r["code"] != code]
        exclusions.append({"code": code, "name": candidate["name"], "reason": reason, "category": category,
                           "excludedAt": current.isoformat(timespec="seconds"), "candidate": candidate})
        record = archive_candidate(candidate, current.date().isoformat(), "manual_excluded")
        record["excludeReason"] = reason
        record["excludeCategory"] = category
        payload.setdefault("history", [])[:] = [r for r in payload.get("history", []) if r["id"] != record["id"]]
        payload["history"].append(record)
        payload["candidates"] = [c for c in payload.get("candidates", []) if c["code"] != code]
        payload["analysisCandidates"] = [c for c in payload.get("analysisCandidates", []) if c["code"] != code]
    else:
        entry = next((r for r in exclusions if r["code"] == code), None)
        if not entry:
            raise ValueError("Excluded candidate not found")
        candidate = entry["candidate"]
        can_restore = candidate.get("candidateTier") != EXPANDED_TIER
        age = sum(candidate["dailySignalDate"] < d <= current.date().isoformat() for d in (calendar or []))
        if can_restore and age < 10 and not any(c["code"] == code for c in payload.get("candidates", [])):
            candidate["tradingDayAge"] = age if calendar else candidate.get("tradingDayAge", 0)
            candidate["tradingDaysRemaining"] = 10 - candidate["tradingDayAge"]
            destination = "analysisCandidates" if candidate.get("candidateTier") == "first_rise" else "candidates"
            payload.setdefault(destination, []).append(candidate)
        entry_id = f"{code}|{candidate['dailySignalDate']}"
        if can_restore and age < 10:
            payload["history"] = [r for r in payload.get("history", []) if not (r.get("id") == entry_id and r.get("archiveReason") == "manual_excluded")]
        exclusions[:] = [r for r in exclusions if r["code"] != code]
    payload["manualExclusions"] = [{k: v for k, v in r.items() if k != "candidate"} for r in exclusions]
    update_validation_summary(payload)
    candidates = payload.get("candidates", [])
    summary = payload.setdefault("summary", {})
    summary["total"] = len(candidates)
    summary["active"] = sum(c.get("status") not in ("excluded", "ineligible") for c in candidates)
    for key, status in {"signals": "signal", "signalHistory": "signaled", "setup": "setup", "rising": "rising", "watching": "watching", "insufficient": "insufficient", "ineligible": "ineligible", "excluded": "excluded", "waiting60": "waiting60"}.items():
        summary[key] = sum(c.get("status") == status for c in candidates)
    for tier in ("core", "expanded"):
        summary[f"{tier}Candidates"] = sum(c.get("candidateTier", "core") == tier and c.get("status") not in ("excluded", "ineligible") for c in candidates)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["exclude", "restore"], required=True)
    args = parser.parse_args()
    code = os.environ["CODE"]
    if len(code) != 6 or not code.isdigit():
        raise ValueError("Invalid stock code")
    path = ROOT / "data/candidate-monitor.json"
    registry_path = ROOT / "data/candidate-exclusions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    previous_entry = next((r for r in registry.get("excluded", []) if r["code"] == code), None)
    calendar = json.loads((ROOT / "data/stock-data.json").read_text(encoding="utf-8")).get("dates", []) if args.action == "restore" else None
    manage(payload, registry, args.action, code, os.getenv("REASON", "차트 형태 부적합")[:300], now_kst(), calendar, os.getenv("EXCLUSION_CATEGORY"))
    first_path = ROOT / "data/first-rise-monitor.json"
    if first_path.exists():
        first = json.loads(first_path.read_text(encoding="utf-8"))
        if args.action == "exclude":
            removed = [item for item in first.get("candidates", []) if item["code"] == code]
            first["candidates"] = [item for item in first.get("candidates", []) if item["code"] != code]
            first.setdefault("history", []).extend({**item, "archivedAt": now_kst().isoformat(), "archiveReason": "manual_excluded"} for item in removed)
        elif previous_entry and previous_entry["candidate"].get("candidateTier") == "first_rise":
            restored = next((item for item in payload.get("analysisCandidates", []) if item["code"] == code), None)
            if restored and not any(item["code"] == code for item in first.get("candidates", [])):
                first.setdefault("candidates", []).append(restored)
        first.setdefault("summary", {})["active"] = len(first.get("candidates", []))
        first_path.write_text(json.dumps(first, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for target, value in [(path, payload), (registry_path, registry)]:
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
