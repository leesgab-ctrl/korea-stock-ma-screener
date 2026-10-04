from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import sys
import time
from pathlib import Path
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def main() -> None:
    args = parse_args()
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)

    stocks = server.fetch_stock_list(args.market)
    if args.limit:
        stocks = stocks[: args.limit]

    needed_days = args.max_ma + args.max_recent + args.max_decline + 8
    collected: list[dict] = []
    started = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(fetch_stock_payload, stock, needed_days, args.include_status): stock
            for stock in stocks
        }
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            item = future.result()
            if item:
                collected.append(item)
            if index % 200 == 0:
                print(f"collected {index}/{len(stocks)} stocks")

    all_dates = sorted({date for item in collected for date, _close, _volume in item["prices"]})
    date_index = {date: index for index, date in enumerate(all_dates)}
    compact_stocks = [compact_stock(item, date_index) for item in collected]

    payload = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "네이버 금융",
        "market": args.market,
        "stockCount": len(compact_stocks),
        "asOf": max(all_dates, default=""),
        "dates": all_dates,
        "stocks": compact_stocks,
        "limits": {
            "maxMa": args.max_ma,
            "maxRecent": args.max_recent,
            "maxDecline": args.max_decline,
            "neededDays": needed_days,
        },
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {output} ({len(compact_stocks)} stocks, {time.time() - started:.1f}s)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/stock-data.json")
    parser.add_argument("--market", default="all", choices=["all", "kospi", "kosdaq"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--max-ma", type=int, default=240)
    parser.add_argument("--max-recent", type=int, default=60)
    parser.add_argument("--max-decline", type=int, default=120)
    parser.add_argument("--include-status", action="store_true", default=False)
    parser.add_argument("--skip-status", dest="include_status", action="store_false")
    return parser.parse_args()


def fetch_stock_payload(stock: dict, needed_days: int, include_status: bool) -> dict | None:
    try:
        prices = server.fetch_prices(stock["code"], needed_days)
        if len(prices) < needed_days - 8:
            return None
        status = server.fetch_trading_status(stock["code"]) if include_status else derive_trading_status(prices)
    except (OSError, TimeoutError, URLError):
        return None

    return {
        "code": stock["code"],
        "name": stock["name"],
        "market": stock["market"],
        "prices": [(point["date"], point["close"], point["volume"]) for point in prices],
        "halted": bool(status["excluded"]),
        "statusReasons": status.get("reasons", []),
    }


def compact_stock(item: dict, date_index: dict[str, int]) -> dict:
    indexes = [date_index[date] for date, _close, _volume in item["prices"]]
    closes = [close for _date, close, _volume in item["prices"]]
    volumes = [volume for _date, _close, volume in item["prices"]]
    compact = {
        "c": item["code"],
        "n": item["name"],
        "m": item["market"],
        "p": closes,
        "v": volumes,
        "h": item["halted"],
    }
    if item["statusReasons"]:
        compact["r"] = item["statusReasons"]
    if indexes and indexes == list(range(indexes[0], indexes[0] + len(indexes))):
        compact["s"] = indexes[0]
    else:
        compact["i"] = indexes
    return compact


def derive_trading_status(prices: list[dict]) -> dict:
    recent = prices[-260:]
    zero_volume_run = 0
    same_close_run = 0
    max_zero_volume_run = 0
    max_same_close_run = 0
    previous_close = None

    for point in recent:
        volume = point.get("volume", 1)
        if volume == 0:
            zero_volume_run += 1
        else:
            zero_volume_run = 0
        max_zero_volume_run = max(max_zero_volume_run, zero_volume_run)

        if previous_close is not None and point["close"] == previous_close:
            same_close_run += 1
        else:
            same_close_run = 0
        max_same_close_run = max(max_same_close_run, same_close_run)
        previous_close = point["close"]

    reasons = []
    if max_zero_volume_run >= 5:
        reasons.append(f"최근 1년 내 거래량 0 연속 {max_zero_volume_run}거래일")
    elif max_same_close_run >= 20:
        reasons.append(f"최근 1년 내 종가 동일 연속 {max_same_close_run + 1}거래일")

    return {
        "excluded": bool(reasons),
        "reasons": reasons,
    }


if __name__ == "__main__":
    main()
