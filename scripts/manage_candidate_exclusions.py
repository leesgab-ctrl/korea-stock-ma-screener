import argparse
import json
import os

from update_candidate_monitor import ROOT, archive_candidate, now_kst, update_validation_summary


def manage(payload, registry, action, code, reason, current, calendar=None):
    exclusions = registry.setdefault("excluded", [])
    if action == "exclude":
        candidate = next((c for c in payload.get("candidates", []) if c["code"] == code), None)
        if not candidate:
            raise ValueError("Candidate not found; refresh the page first")
        exclusions[:] = [r for r in exclusions if r["code"] != code]
        exclusions.append({"code": code, "name": candidate["name"], "reason": reason,
                           "excludedAt": current.isoformat(timespec="seconds"), "candidate": candidate})
        record = archive_candidate(candidate, current.date().isoformat(), "manual_excluded")
        record["excludeReason"] = reason
        payload.setdefault("history", [])[:] = [r for r in payload.get("history", []) if r["id"] != record["id"]]
        payload["history"].append(record)
        payload["candidates"] = [c for c in payload.get("candidates", []) if c["code"] != code]
    else:
        entry = next((r for r in exclusions if r["code"] == code), None)
        if not entry:
            raise ValueError("Excluded candidate not found")
        candidate = entry["candidate"]
        age = sum(candidate["dailySignalDate"] < d <= current.date().isoformat() for d in (calendar or []))
        if age < 10 and not any(c["code"] == code for c in payload.get("candidates", [])):
            candidate["tradingDayAge"] = age if calendar else candidate.get("tradingDayAge", 0)
            candidate["tradingDaysRemaining"] = 10 - candidate["tradingDayAge"]
            payload.setdefault("candidates", []).append(candidate)
        entry_id = f"{code}|{candidate['dailySignalDate']}"
        if age < 10:
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
    calendar = json.loads((ROOT / "data/stock-data.json").read_text(encoding="utf-8")).get("dates", []) if args.action == "restore" else None
    manage(payload, registry, args.action, code, os.getenv("REASON", "차트 형태 부적합")[:300], now_kst(), calendar)
    for target, value in [(path, payload), (registry_path, registry)]:
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
