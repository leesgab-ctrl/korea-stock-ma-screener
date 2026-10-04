from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from update_candidate_monitor import KST, ROOT, USER_AGENT, aggregate_30m, fetch_minute_rows, now_kst


DEFAULT_PATH = ROOT / "data" / "positions.json"


def load_positions(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "positions": [], "notifiedEvents": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_positions(path: Path, payload: dict[str, Any], current: dt.datetime) -> None:
    payload["updatedAt"] = current.isoformat(timespec="seconds")
    payload["summary"] = {
        "open": sum(item.get("status") == "open" for item in payload.get("positions", [])),
        "closed": sum(item.get("status") == "closed" for item in payload.get("positions", [])),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def candidate_default_stop(code: str, path: Path) -> int | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    for candidate in payload.get("candidates", []):
        if candidate.get("code") == code:
            value = candidate.get("daily", {}).get("preSpikeClose")
            return int(value) if value else None
    return None


def register_position(
    payload: dict[str, Any], code: str, name: str, buy_price: int, stop_price: int | None,
    target_pct: float, stop_pct: float, current: dt.datetime, stop_source: str | None = None,
) -> None:
    if not code.isdigit() or len(code) != 6:
        raise ValueError("종목코드는 숫자 6자리여야 합니다.")
    if buy_price <= 0:
        raise ValueError("매수가는 0보다 커야 합니다.")
    if not 0 < stop_pct < 100:
        raise ValueError("기본 손절률은 0% 초과 100% 미만이어야 합니다.")
    stop_source = stop_source or ("agreed_price" if stop_price is not None else "default_pct")
    if stop_price is None:
        stop_price = round(buy_price * (1 - stop_pct / 100))
    if stop_price <= 0:
        raise ValueError("손절가는 0보다 커야 합니다.")
    if stop_price >= buy_price:
        raise ValueError("손절가는 매수가보다 낮아야 합니다.")
    if not 0 < target_pct <= 100:
        raise ValueError("목표수익률은 0% 초과 100% 이하여야 합니다.")
    positions = payload.setdefault("positions", [])
    positions[:] = [item for item in positions if not (item.get("code") == code and item.get("status") == "open")]
    positions.append(
        {
            "code": code,
            "name": name.strip() or code,
            "buyPrice": buy_price,
            "stopPrice": stop_price,
            "stopPct": round(100 * (1 - stop_price / buy_price), 2),
            "stopSource": stop_source,
            "riskWarning": 100 * (1 - stop_price / buy_price) > target_pct,
            "targetPct": target_pct,
            "targetPrice": round(buy_price * (1 + target_pct / 100)),
            "openedAt": current.isoformat(timespec="minutes"),
            "status": "open",
            "naverUrl": f"https://stock.naver.com/domestic/stock/{code}/price",
        }
    )


def close_position(payload: dict[str, Any], code: str, current: dt.datetime) -> None:
    for item in payload.get("positions", []):
        if item.get("code") == code and item.get("status") == "open":
            item["status"] = "closed"
            item["closedAt"] = current.isoformat(timespec="minutes")
            return
    raise ValueError(f"열려 있는 보유종목 {code}을 찾지 못했습니다.")


def latest_regular_price(rows: list[dict[str, Any]]) -> tuple[int | None, dt.datetime | None]:
    regular = [row for row in rows if dt.time(9, 0) <= row["time"].time() <= dt.time(15, 30)]
    if not regular:
        return None, None
    latest = regular[-1]
    return latest["price"], latest["time"]


def trend_is_weak(bars: list[dict[str, Any]]) -> bool:
    usable = [bar for bar in bars if bar.get("ma20") is not None]
    if len(usable) < 4:
        return False
    recent = usable[-4:]
    ma_falling = all(recent[index]["ma20"] < recent[index - 1]["ma20"] for index in range(1, 4))
    return ma_falling and recent[-1]["close"] < recent[-1]["ma20"]


def send_alert(topic: str, position: dict[str, Any], event: str) -> None:
    labels = {
        "target": ("목표수익 도달", "설정한 목표수익률에 도달했습니다."),
        "stop": ("손절가 도달", "설정한 손절가에 도달했습니다."),
        "weak": ("추세약화 확인", "30분봉 종가가 MA20 아래이고 MA20이 3봉 연속 하락했습니다."),
    }
    title, reason = labels[event]
    message = (
        f"{position['name']}({position['code']})\n"
        f"현재 확인가격: {position['lastPrice']:,}원\n"
        f"매수가: {position['buyPrice']:,}원 / 수익률: {position['returnPct']:+.2f}%\n"
        f"손절가: {position['stopPrice']:,}원 / 목표가: {position['targetPrice']:,}원\n"
        f"{reason}\n자동주문이 아닌 매도 검토 알림입니다."
    )
    body = json.dumps(
        {
            "topic": topic,
            "title": f"🔴 {position['name']} {title}",
            "message": message,
            "priority": 4,
            "tags": ["warning"],
            "click": position["naverUrl"],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://ntfy.sh", data=body, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status >= 300:
            raise RuntimeError(f"ntfy 전송 실패: HTTP {response.status}")


def monitor_positions(payload: dict[str, Any], current: dt.datetime, count: int, no_notify: bool) -> int:
    topic = os.getenv("NTFY_TOPIC", "").strip()
    notified = set(payload.get("notifiedEvents", []))
    new_alerts = 0
    for position in payload.get("positions", []):
        if position.get("status") != "open":
            continue
        rows = fetch_minute_rows(position["code"], count)
        price, price_time = latest_regular_price(rows)
        if price is None or price_time is None:
            position["dataStatus"] = "no_data"
            continue
        bars = aggregate_30m(rows, current)
        position["lastPrice"] = price
        position["lastPriceTime"] = price_time.isoformat(timespec="minutes")
        position["returnPct"] = round(100 * (price / position["buyPrice"] - 1), 2)
        position["trendWeak"] = trend_is_weak(bars)
        position["dataStatus"] = "ok"
        events = []
        if price >= position["targetPrice"]:
            events.append("target")
        if price <= position["stopPrice"]:
            events.append("stop")
        if position["trendWeak"]:
            events.append("weak")
        for event in events:
            signature = f"{position['code']}|{position['openedAt']}|{event}"
            if signature in notified:
                continue
            if topic and not no_notify:
                send_alert(topic, position, event)
                notified.add(signature)
                new_alerts += 1
    payload["notifiedEvents"] = sorted(notified)[-200:]
    payload["pushConfigured"] = bool(topic)
    return new_alerts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="보유종목 목표가·손절가·추세약화 알림을 관리합니다.")
    parser.add_argument("--action", choices=("buy", "sold", "monitor"), required=True)
    parser.add_argument("--code")
    parser.add_argument("--name", default="")
    parser.add_argument("--buy-price", type=int)
    parser.add_argument("--stop-price", type=int)
    parser.add_argument("--stop-pct", type=float, default=3.0)
    parser.add_argument("--target-pct", type=float, default=5.0)
    parser.add_argument("--data", default=str(DEFAULT_PATH))
    parser.add_argument("--candidate-data", default=str(ROOT / "data" / "candidate-monitor.json"))
    parser.add_argument("--minute-count", type=int, default=5000)
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--now")
    parser.add_argument("--github-output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.data)
    payload = load_positions(path)
    current = dt.datetime.fromisoformat(args.now).astimezone(KST) if args.now else now_kst()
    alerts = 0
    if args.action == "buy":
        if args.code is None or args.buy_price is None:
            raise ValueError("매수 등록에는 종목코드와 매수가가 필요합니다.")
        stop_source = None
        stop_price = args.stop_price
        if stop_price is None:
            stop_price = candidate_default_stop(args.code, Path(args.candidate_data))
            if stop_price is not None and stop_price < args.buy_price:
                stop_source = "large_volume_previous_close"
            else:
                stop_price = None
        register_position(
            payload, args.code, args.name, args.buy_price, stop_price,
            args.target_pct, args.stop_pct, current, stop_source,
        )
    elif args.action == "sold":
        if args.code is None:
            raise ValueError("매도 완료 처리에는 종목코드가 필요합니다.")
        close_position(payload, args.code, current)
    else:
        alerts = monitor_positions(payload, current, args.minute_count, args.no_notify)
    save_positions(path, payload, current)
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"alerts={alerts}\n")
    print(json.dumps({"action": args.action, "alerts": alerts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
