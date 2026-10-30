"""Capture a separately timestamped last-trade quote immediately before delivery."""
import datetime as dt
import math

KST = dt.timezone(dt.timedelta(hours=9))


def now():
    return dt.datetime.now(KST)


def capture(code, fetcher=None, clock=None):
    clock = clock or now
    requested = clock()
    try:
        if fetcher is None:
            from update_candidate_monitor import fetch_display_quote
            fetcher = fetch_display_quote
        quote = fetcher(code, requested)
        observed = clock()
        price, previous = quote.get("price"), quote.get("previousClose")
        quote_time = dt.datetime.fromisoformat(quote.get("time", ""))
        if (quote.get("dataStatus") != "ok" or quote_time.tzinfo is None
                or quote_time > observed + dt.timedelta(minutes=5)
                or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                           and math.isfinite(value) and value > 0 for value in (price, previous))):
            raise ValueError("Invalid delivery quote")
        age = max(0, (observed - quote_time).total_seconds())
        return {"version": 1, "status": "ok", "price": price, "previousClose": previous,
                "changePct": (price / previous - 1) * 100, "quoteTime": quote_time.astimezone(KST).isoformat(timespec="seconds"),
                "requestedAt": requested.isoformat(timespec="seconds"), "observedAt": observed.isoformat(timespec="seconds"),
                "source": quote.get("source", "naver_quote"), "ageSeconds": round(age, 1), "stale": age > 300}
    except Exception as exc:
        return {"version": 1, "status": "error", "price": None, "quoteTime": None,
                "requestedAt": requested.isoformat(timespec="seconds"), "observedAt": clock().isoformat(timespec="seconds"),
                "source": "naver_quote", "errorType": type(exc).__name__}


def message(snapshot):
    if snapshot.get("status") != "ok":
        return "\n- 알림 직전 현재가: 조회 실패 · 기준봉 종가로 대체하지 않음"
    stale = " · 최근 체결이 오래된 시세" if snapshot["stale"] else ""
    return (f"\n- 알림 직전 현재가: {snapshot['price']:,.0f}원 · 전일 대비 {snapshot['changePct']:+.2f}%"
            f"\n- 시세 시각: {snapshot['quoteTime']}{stale}\n- 조회 시각: {snapshot['observedAt']}")
