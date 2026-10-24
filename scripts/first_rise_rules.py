"""First-day observation entries, using only information available at each close."""
import datetime as dt
import math

KST = dt.timezone(dt.timedelta(hours=9))
VERSION = "first-rise-daily-ma3-cross10-v3"
CONFIRM_TIME = dt.time(20, 30)
WINDOW_DAYS = 10
PERIODS = (5, 10, 20, 40, 60)


def after_close(day, current):
    current = current.astimezone(KST)
    return current >= dt.datetime.combine(dt.date.fromisoformat(day), CONFIRM_TIME, KST)


def closing_confirmation(daily, day, current, quote=None):
    if not after_close(day, current):
        return None
    rows = sorted({r["d"]: r for r in daily if r["d"] <= day}.values(), key=lambda r: r["d"])
    if len(rows) < 11 or rows[-1]["d"] != day:
        return None
    if any(not isinstance(r.get("c"), (int, float)) or not math.isfinite(r["c"]) or r["c"] <= 0 for r in rows[-11:]):
        return None
    ma3 = sum(r["c"] for r in rows[-3:]) / 3
    ma10 = sum(r["c"] for r in rows[-10:]) / 10
    prior3 = sum(r["c"] for r in rows[-4:-1]) / 3
    prior10 = sum(r["c"] for r in rows[-11:-1]) / 10
    close, previous = rows[-1].get("c"), rows[-2].get("c")
    reference_source = "native_daily_history"
    if quote and quote.get("dataStatus") == "ok" and quote.get("time", "")[:10] == day:
        previous = quote.get("previousClose")
        reference_source = "naver_quote_previous_close"
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in (close, previous)):
        return None
    change = 100 * (close / previous - 1)
    return {"ruleVersion": VERSION, "day": day, "qualified": prior3 <= prior10 and ma3 > ma10,
            "ma3": ma3, "ma10": ma10, "previousMa3": prior3, "previousMa10": prior10,
            "close": close, "previousClose": previous, "previousCloseSource": reference_source, "changePct": round(change, 6),
            "confirmedAt": current.astimezone(KST).isoformat(timespec="seconds")}


def confirmed(item):
    close = item.get("closeConfirmation", {})
    return close.get("ruleVersion") == VERSION and close.get("qualified") is True


def daily_context(history, day):
    rows = sorted({r["d"]: r for r in history if r["d"] < day}.values(), key=lambda r: r["d"])
    if len(rows) < 25:
        return None
    if any(not isinstance(r.get(k), (int, float)) or not math.isfinite(r[k]) or r[k] < 0
           for r in rows[-25:] for k in ("c", "v")) or any(r["c"] <= 0 for r in rows[-25:]):
        return None
    ma20 = sum(r["c"] for r in rows[-20:]) / 20
    old20 = sum(r["c"] for r in rows[-25:-5]) / 20
    changes = [100 * (rows[i]["c"] / rows[i-1]["c"] - 1) for i in range(len(rows)-3, len(rows))]
    prior_volume = sum(r["v"] for r in rows[-19:])
    return {"qualified": ma20 < old20 and max(changes) < 8,
            "ma20ChangePct": 100 * (ma20 / old20 - 1), "recent3MaxRisePct": max(changes),
            "previousClose": rows[-1]["c"], "prior19Volume": prior_volume}


def detect(history, daily, day, current):
    context = daily_context(daily, day)
    if not context or not context["qualified"]:
        return []
    rows = sorted({r["t"]: r for r in history
                   if dt.datetime.fromisoformat(r["t"]) + dt.timedelta(minutes=30) <= current
                   and r["t"][:10] <= day}.values(), key=lambda r: r["t"])
    sums = [0.0]
    for row in rows:
        if any(not isinstance(row.get(k), (int, float)) or not math.isfinite(row[k]) or row[k] < 0
               for k in ("c", "o", "v")) or row["c"] <= 0 or row["o"] <= 0:
            return []
        sums.append(sums[-1] + row["c"])
    def ma(n, i):
        return (sums[i+1] - sums[i+1-n]) / n
    found, cumulative = [], 0
    for i, row in enumerate(rows):
        if row["t"][:10] != day:
            continue
        cumulative += row["v"]
        if i < 65:
            continue
        upper = max(ma(n, i-1) for n in PERIODS)
        if not rows[i-1]["c"] <= upper < row["c"]:
            continue
        spreads = []
        for j in range(i-6, i):
            values = [ma(n, j) for n in PERIODS]
            spreads.append(100 * (max(values) / min(values) - 1))
        volume20 = sum(r["v"] for r in rows[i-20:i]) / 20
        daily_volume20 = (context["prior19Volume"] + cumulative) / 20
        change = 100 * (row["c"] / context["previousClose"] - 1)
        if (max(spreads) > 4 + 1e-9 or change < 3 - 1e-9 or row["c"] <= row["o"]
                or ma(3, i) <= ma(3, i-1) or volume20 <= 0 or daily_volume20 <= 0
                or row["v"] < 3 * volume20 or cumulative < 3 * daily_volume20):
            continue
        found.append({"barTime": row["t"], "completedAt": (dt.datetime.fromisoformat(row["t"])
                      + dt.timedelta(minutes=30)).isoformat(timespec="seconds"), "price": row["c"],
                      "changePct": round(change, 6), "clusterMaxPct": round(max(spreads), 6),
                      "barVolumeMultiple": round(row["v"] / volume20, 6),
                      "dailyVolumeMultiple": round(cumulative / daily_volume20, 6),
                      "cumulativeVolume": cumulative, "dailyMa20ChangePct": round(context["ma20ChangePct"], 6),
                      "recent3MaxRisePct": round(context["recent3MaxRisePct"], 6), "ruleVersion": VERSION})
    return found


def remaining_days(day, calendar):
    return max(0, WINDOW_DAYS - sum(date >= day for date in calendar) + 1)


def registration_message(item):
    signal = item["firstRiseSignal"]
    close = item["closeConfirmation"]
    return (f"[장마감 첫날상승 확정 · 관찰대상] {item['name']}({item['code']})\n"
            f"- 기준일 {close['day']} · 확정·등록시각 {item['registeredAt']}\n"
            f"- 당일 종가 {close['close']:,.0f}원 · 전일대비 {close['changePct']:+.2f}%\n"
            f"- 일봉 MA3 {close['ma3']:,.2f} > MA10 {close['ma10']:,.2f} · 직전 거래일 MA3 ≤ MA10 확인\n"
            f"- 장중 형태 확인봉 완성 {signal['completedAt']} (장중 알림 아님)\n"
            f"- 직전 6봉 MA5~60 최대 간격 {signal['clusterMaxPct']:.2f}%\n"
            f"- 30분봉 거래량 {signal['barVolumeMultiple']:.2f}배 / 당일 누적 거래량 {signal['dailyVolumeMultiple']:.2f}배\n"
            "종가 기준 일봉 MA3의 MA10 상향 돌파를 확인한 관찰대상입니다. 매수 신호가 아니며 다음 거래일부터 조정·재상승 이미지조건을 감시합니다.\n"
            f"https://leesgab-ctrl.github.io/korea-stock-ma-screener/monitor.html?view=firstRise&stock={item['code']}")
