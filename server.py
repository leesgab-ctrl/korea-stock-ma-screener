from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
import json
import mimetypes
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
NAVER = "https://finance.naver.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}
CACHE_TTL = 60 * 20
LIST_CACHE: dict[str, tuple[float, list[dict]]] = {}
PRICE_CACHE: dict[str, tuple[float, list[dict]]] = {}
STATUS_CACHE: dict[str, tuple[float, dict]] = {}
CURRENT_STATUS_LABELS = ("거래정지", "매매거래정지", "관리종목", "투자주의환기", "정리매매", "상장폐지")
STATUS_KEYWORDS = (
    "거래정지",
    "매매거래정지",
    "주권매매거래정지",
    "관리종목",
    "투자주의환기",
    "정리매매",
    "상장폐지",
    "개선기간",
    "상장적격성",
)


@dataclass
class Settings:
    ma_window: int
    recent_days: int
    decline_days: int
    tolerance: int
    strict: bool
    exclude_halted: bool


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href") or ""
        if "code=" in href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._href:
            return
        text = " ".join(piece.strip() for piece in self._text if piece.strip())
        match = re.search(r"code=(\d{6})", self._href)
        if match and text:
            self.links.append({"code": match.group(1), "name": text})
        self._href = None
        self._text = []


class SiseDayParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._cell is not None and self._row is not None:
            value = " ".join(piece.strip() for piece in self._cell if piece.strip())
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=12) as response:
        raw = response.read()
    return decode_naver_text(raw)


def decode_naver_text(raw: bytes) -> str:
    candidates = []
    for charset in ("utf-8", "euc-kr", "cp949"):
        text = raw.decode(charset, errors="ignore")
        score = sum(1 for char in text[:20000] if "\uac00" <= char <= "\ud7a3")
        candidates.append((score, text))
    best_score, best_text = max(candidates, key=lambda item: item[0])
    if best_score > 0:
        return best_text

    head = raw[:1000].decode("ascii", errors="ignore").lower()
    match = re.search(r"charset=['\"]?([\w-]+)", head)
    if match:
        return raw.decode(match.group(1), errors="ignore")
    if b"encoding=\"EUC-KR\"" in raw[:200] or b"encoding='EUC-KR'" in raw[:200]:
        return raw.decode("euc-kr", errors="ignore")
    return raw.decode("euc-kr", errors="ignore")


def market_pages(market: str) -> list[tuple[str, int]]:
    if market == "kospi":
        markets = [("KOSPI", 0)]
    elif market == "kosdaq":
        markets = [("KOSDAQ", 1)]
    else:
        markets = [("KOSPI", 0), ("KOSDAQ", 1)]
    return markets


def fetch_stock_list(market: str) -> list[dict]:
    cached = LIST_CACHE.get(market)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    stocks = fetch_stock_list_json(market)
    if stocks:
        LIST_CACHE[market] = (time.time(), stocks)
        return stocks

    stocks = []
    seen: set[str] = set()

    for market_name, sosok in market_pages(market):
        page = 1
        while page <= 120:
            url = f"{NAVER}/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
            parser = LinkParser()
            parser.feed(fetch_text(url))
            page_items = 0
            for item in parser.links:
                code = item["code"]
                if code in seen:
                    continue
                seen.add(code)
                stocks.append({**item, "market": market_name})
                page_items += 1
            if page_items == 0:
                break
            page += 1

    LIST_CACHE[market] = (time.time(), stocks)
    return stocks


def fetch_stock_list_json(market: str) -> list[dict]:
    stocks: list[dict] = []
    seen: set[str] = set()
    for market_name, _sosok in market_pages(market):
        page = 1
        total = 1
        while len([item for item in stocks if item["market"] == market_name]) < total:
            url = (
                "https://m.stock.naver.com/api/stocks/marketValue/"
                f"{market_name}?page={page}&pageSize=100"
            )
            try:
                request = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(request, timeout=12) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (json.JSONDecodeError, OSError, TimeoutError, urllib.error.URLError):
                return []
            total = int(payload.get("totalCount", 0))
            page_items = payload.get("stocks", [])
            if not page_items:
                break
            for item in page_items:
                code = item.get("itemCode", "")
                name = item.get("stockName", "")
                is_preferred = bool(re.search(r"(?:\d+우[BC]?|우[BC]?)$", name))
                if (
                    not re.fullmatch(r"\d{6}", code)
                    or not name
                    or code in seen
                    or item.get("stockEndType") != "stock"
                    or is_preferred
                ):
                    continue
                seen.add(code)
                stocks.append({"code": code, "name": name, "market": market_name})
            page += 1
    return stocks


def fetch_prices(code: str, needed: int) -> list[dict]:
    cached = PRICE_CACHE.get(code)
    if cached and time.time() - cached[0] < CACHE_TTL and len(cached[1]) >= needed:
        return cached[1]

    count = max(needed + 40, 300)
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0"
    text = fetch_text(url)
    prices: list[dict] = []

    for match in re.finditer(r'<item data="(\d{8})\|(\d+)\|(\d+)\|(\d+)\|(\d+)\|(\d+)"', text):
        raw_date, _open, _high, _low, close, volume = match.groups()
        prices.append(
            {
                "date": f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}",
                "close": int(close),
                "volume": int(volume),
            }
        )

    prices.sort(key=lambda item: item["date"])
    PRICE_CACHE[code] = (time.time(), prices)
    return prices


def fetch_trading_status(code: str) -> dict:
    cached = STATUS_CACHE.get(code)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    url = f"{NAVER}/item/main.naver?code={code}"
    text = fetch_text(url)
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=365)
    reasons: list[str] = []

    for label in CURRENT_STATUS_LABELS:
        pattern = rf'<span class="blind">\s*{re.escape(label)}\s*</span>'
        if re.search(pattern, text):
            reasons.append(f"현재 {label}")

    plain = html.unescape(re.sub(r"<[^>]+>", " ", text))
    plain = re.sub(r"\s+", " ", plain)
    for keyword in STATUS_KEYWORDS:
        for match in re.finditer(re.escape(keyword), plain):
            snippet = plain[max(0, match.start() - 100) : match.end() + 100]
            for found_date in extract_recent_dates(snippet, today, cutoff):
                reasons.append(f"{found_date.isoformat()} {keyword}")

    unique_reasons = sorted(set(reasons))
    result = {
        "excluded": bool(unique_reasons),
        "reasons": unique_reasons[:5],
    }
    STATUS_CACHE[code] = (time.time(), result)
    return result


def extract_recent_dates(text: str, today: dt.date, cutoff: dt.date) -> list[dt.date]:
    dates: list[dt.date] = []
    for year, month, day in re.findall(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", text):
        try:
            parsed = dt.date(int(year), int(month), int(day))
        except ValueError:
            continue
        if cutoff <= parsed <= today:
            dates.append(parsed)

    for month, day in re.findall(r"(?<!\d)(\d{1,2})/(\d{1,2})(?:\s+\d{1,2}:\d{2})?(?!\d)", text):
        try:
            parsed = dt.date(today.year, int(month), int(day))
        except ValueError:
            continue
        if parsed > today:
            parsed = dt.date(today.year - 1, parsed.month, parsed.day)
        if cutoff <= parsed <= today:
            dates.append(parsed)
    return dates


def moving_average(prices: list[dict], window: int) -> list[dict]:
    series: list[dict] = []
    total = 0.0
    for index, point in enumerate(prices):
        total += point["close"]
        if index >= window:
            total -= prices[index - window]["close"]
        series.append(
            {
                "date": point["date"],
                "close": point["close"],
                "ma": total / window if index >= window - 1 else None,
            }
        )
    return series


def slope_stats(series: list[dict], start: int, end: int) -> dict:
    up = down = flat = 0
    for index in range(start + 1, end + 1):
        prev = series[index - 1]["ma"]
        next_value = series[index]["ma"]
        if prev is None or next_value is None:
            continue
        if next_value > prev:
            up += 1
        elif next_value < prev:
            down += 1
        else:
            flat += 1
    return {"up": up, "down": down, "flat": flat, "total": up + down + flat}


def analyze(stock: dict, settings: Settings) -> dict | None:
    needed = settings.ma_window + settings.recent_days + settings.decline_days + 8
    prices = fetch_prices(stock["code"], needed)
    if len(prices) < needed - 8:
        return None

    series = moving_average(prices, settings.ma_window)
    last = len(series) - 1
    decline_end = last - settings.recent_days
    decline_start = decline_end - settings.decline_days
    if decline_start < settings.ma_window - 1:
        return None

    recent = slope_stats(series, decline_end, last)
    previous = slope_stats(series, decline_start, decline_end)
    if settings.strict:
        recent_pass = recent["down"] == 0 and recent["up"] == recent["total"]
        previous_pass = previous["up"] == 0 and previous["down"] == previous["total"]
    else:
        recent_pass = recent["down"] <= settings.tolerance and recent["up"] > recent["down"]
        previous_pass = previous["up"] <= settings.tolerance and previous["down"] > previous["up"]

    if not (recent_pass and previous_pass):
        return None

    recent_change = ((series[last]["ma"] - series[decline_end]["ma"]) / series[decline_end]["ma"]) * 100
    previous_change = ((series[decline_end]["ma"] - series[decline_start]["ma"]) / series[decline_start]["ma"]) * 100
    score = recent_change - previous_change + previous["down"] / max(previous["total"], 1)

    return {
        "code": stock["code"],
        "name": stock["name"],
        "market": stock["market"],
        "lastClose": prices[-1]["close"],
        "lastDate": prices[-1]["date"],
        "ma": series[last]["ma"],
        "recent": recent,
        "previous": previous,
        "recentChange": recent_change,
        "previousChange": previous_change,
        "score": score,
        "series": series[-220:],
    }


def search(params: dict[str, list[str]]) -> dict:
    settings = Settings(
        ma_window=clamp_int(params.get("ma", ["120"])[0], 20, 240),
        recent_days=clamp_int(params.get("recent", ["20"])[0], 5, 60),
        decline_days=clamp_int(params.get("decline", ["60"])[0], 20, 120),
        tolerance=clamp_int(params.get("tolerance", ["4"])[0], 0, 20),
        strict=params.get("strict", ["0"])[0] == "1",
        exclude_halted=params.get("excludeHalted", ["1"])[0] == "1",
    )
    market = params.get("market", ["all"])[0]
    keyword = params.get("q", [""])[0].strip().lower()
    needed = settings.ma_window + settings.recent_days + settings.decline_days + 8

    stocks = fetch_stock_list(market)
    if keyword:
        stocks = [
            stock
            for stock in stocks
            if keyword in stock["name"].lower() or keyword in stock["code"]
        ]

    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(analyze, stock, settings) for stock in stocks]
        for future in concurrent.futures.as_completed(futures):
            try:
                item = future.result()
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
            if item:
                results.append(item)

    excluded_status = 0
    if settings.exclude_halted and results:
        checked_results: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            status_futures = {
                executor.submit(fetch_trading_status, item["code"]): item
                for item in results
            }
            for future in concurrent.futures.as_completed(status_futures):
                item = status_futures[future]
                try:
                    status = future.result()
                except (urllib.error.URLError, TimeoutError, OSError):
                    status = {"excluded": False, "reasons": []}
                if status["excluded"]:
                    excluded_status += 1
                    continue
                checked_results.append(item)
        results = checked_results

    results.sort(key=lambda item: item["score"], reverse=True)
    return {
        "source": "네이버 금융",
        "matches": results,
        "scanned": len(stocks),
        "excludedStatus": excluded_status,
        "neededDays": needed,
        "asOf": max((item["lastDate"] for item in results), default=""),
    }


def clamp_int(value: str, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        parsed = low
    return max(low, min(high, parsed))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/search":
            self.handle_search(parsed.query)
            return
        self.serve_file(parsed.path)

    def handle_search(self, query: str) -> None:
        try:
            payload = search(urllib.parse.parse_qs(query))
            self.send_json(payload)
        except Exception as exc:
            self.send_json({"error": f"네이버 데이터를 가져오지 못했습니다: {exc}"}, status=502)

    def serve_file(self, path: str) -> None:
        target = ROOT / (path.lstrip("/") or "index.html")
        target = target.resolve()
        if ROOT not in target.parents and target != ROOT:
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    host = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Local: http://127.0.0.1:{port}")
    print(f"Wi-Fi: http://{local_ip()}:{port}")
    server.serve_forever()


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


if __name__ == "__main__":
    main()
