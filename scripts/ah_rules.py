"""Independent AH daily selection and completed-bar review rules."""
import datetime as dt
import math

KST = dt.timezone(dt.timedelta(hours=9))
VERSION = "ah-ma4-60__ma120-180-v1"
ADMISSION_VERSION = "ah-final-recent-cross-ma20-v2"
CROSS_LOOKBACK_TRADING_DAYS = 2
WINDOW_DAYS = 5
MODEL = {"kind": "quarter_open_three_quarter_close", "barsPerDay": 22}


def daily_series(history, day):
    rows = sorted((row for row in history if row["d"] <= day), key=lambda row: row["d"])
    sums = [0.0]
    result = []
    for row in rows:
        sums.append(sums[-1] + row["c"])
        end = len(sums) - 1
        result.append({**row, **{f"m{n}": (sums[end] - sums[end - n]) / n if end >= n else None
                                 for n in (4, 60)}})
    return result


def daily_checks(rows):
    if len(rows) < 105:
        return None
    def trend(offset, count, rising):
        end = len(rows) - offset
        selected = rows[end - count - 1:end]
        return all((b["m60"] > a["m60"]) if rising else (b["m60"] < a["m60"])
                   for a, b in zip(selected, selected[1:]))
    return {
        "A": trend(25, 20, False),
        "B": trend(0, 2, True),
        "C": all(row["m4"] > row["m60"] for row in rows[-13:-10]),
        "D": all(row["m4"] <= row["m60"] for row in rows[-6:-4]),
        "E": all(row["m4"] >= row["m60"] for row in rows[-2:]),
        "H": (rows[-2]["m60"] / rows[-2]["m4"] - 1) * 100 <= 1 + 1e-10,
    }


def daily_match(history, day, current_price=None):
    selected = [dict(row) for row in history if row["d"] <= day]
    if current_price is not None:
        selected = [row for row in selected if row["d"] < day]
        selected.append({"d": day, "c": current_price})
    checks = daily_checks(daily_series(selected, day))
    return checks is not None and all(checks.values())


def potential_match(history, day):
    # Only the D0 portions of B/E depend on the current quote. Keep all fixed tests.
    rows = daily_series([row for row in history if row["d"] < day] + [{"d": day, "c": 1}], day)
    checks = daily_checks(rows)
    return bool(checks and all(checks[key] for key in ("A", "C", "D", "H"))
                and rows[-2]["m60"] > rows[-3]["m60"] and rows[-2]["m4"] >= rows[-2]["m60"])


def hybrid_rows(native, daily, cutoff):
    native = sorted({row["t"]: row for row in native
                     if dt.datetime.fromisoformat(row["t"]) <= cutoff}.values(), key=lambda row: row["t"])
    if not native:
        return []
    first = native[0]["t"][:10]
    prefix = []
    for row in sorted(daily, key=lambda row: row["d"]):
        if row["d"] < first and row.get("v", 0) > 0:
            prefix.extend([0.25 * row.get("o", row["c"]) + 0.75 * row["c"]] * MODEL["barsPerDay"])
    prefix = prefix[-180:]
    closes = prefix + [row["c"] for row in native]
    sums = [0.0]
    for close in closes:
        sums.append(sums[-1] + close)
    result = []
    for index, row in enumerate(native):
        end = len(prefix) + index + 1
        values = {f"m{n}": (sums[end] - sums[end - n]) / n if end >= n else None
                  for n in (3, 10, 20, 40, 60, 120, 180)}
        complete_at = dt.datetime.fromisoformat(row["t"]) + dt.timedelta(minutes=30)
        result.append({**row, **values, "complete": complete_at <= cutoff,
                       "estimated10Bars": max(0, 10 - index - 1),
                       "estimated20Bars": max(0, 20 - index - 1),
                       "estimated120Bars": max(0, 120 - index - 1),
                       "estimated180Bars": max(0, 180 - index - 1)})
    return result


def completed_at(row):
    return dt.datetime.fromisoformat(row["t"]) + dt.timedelta(minutes=30)


def above(row):
    return (all(row.get(key) is not None for key in ("m10", "m120", "m180"))
            and row.get("estimated10Bars", 1) == 0
            and row["m10"] > row["m120"] > row["m180"])


def final_admission(rows, calendar, day, now):
    """Admission is separate from the existing MA10 review-notification rule."""
    complete = sorted((row for row in rows if row.get("complete") is True
                       and completed_at(row) <= now and row["t"][:10] <= day), key=lambda row: row["t"])
    if day not in calendar or len(complete) < 2:
        return None
    def valid(row, keys):
        return all(isinstance(row.get(key), (int, float)) and not isinstance(row[key], bool)
                   and math.isfinite(row[key]) and row[key] > 0 for key in keys)
    latest = complete[-1]
    if (not valid(latest, ("m20", "m120", "m180")) or latest.get("estimated20Bars", 1) != 0
            or not (latest["m20"] > latest["m120"] > latest["m180"])):
        return None
    for index in range(len(complete) - 1, 0, -1):
        previous, row = complete[index - 1:index + 1]
        if not all(valid(bar, ("m120", "m180")) for bar in (previous, row)):
            continue
        if previous["m120"] <= previous["m180"] and row["m120"] > row["m180"]:
            cross_day = row["t"][:10]
            if cross_day not in calendar or not 0 <= calendar.index(day) - calendar.index(cross_day) <= CROSS_LOOKBACK_TRADING_DAYS:
                continue
            if not all(valid(bar, ("m120", "m180")) and bar["m120"] > bar["m180"] for bar in complete[index:]):
                continue
            return {"version": ADMISSION_VERSION, "crossBarTime": row["t"], "checkedBarTime": latest["t"],
                    "ma20": latest["m20"], "ma120": latest["m120"], "ma180": latest["m180"],
                    "approximate": latest.get("estimated120Bars", 0) > 0 or latest.get("estimated180Bars", 0) > 0
                    or row.get("estimated120Bars", 0) > 0 or row.get("estimated180Bars", 0) > 0}
    return None


def crossover_events(rows):
    complete = [row for row in rows if row["complete"]]
    events = []
    for previous, row in zip(complete, complete[1:]):
        if (previous.get("m120") is not None and previous.get("m180") is not None
                and previous["m120"] <= previous["m180"] and above(row)):
            events.append(row)
    return events


def review_alerts(candidate, rows, calendar, now, enrollment_pending=False):
    complete = [row for row in rows if row["complete"]]
    events = crossover_events(rows)
    registered = dt.datetime.fromisoformat(candidate["registeredAt"])
    cursor = dt.datetime.fromisoformat(candidate.get("alertCursor", candidate["registeredAt"]))
    fresh = [row for row in events if completed_at(row) >= registered and completed_at(row) > cursor
             and dt.timedelta(0) <= now - completed_at(row) <= dt.timedelta(minutes=90)]
    if fresh:
        return [{"kind": "cross", "bar": row, "cross": row,
                 "key": f"{VERSION}:{candidate['code']}:cross:{row['t']}"} for row in fresh]
    if not enrollment_pending or not complete:
        return []
    day = registered.date().isoformat()
    if day not in calendar:
        return []
    for row in reversed(events):
        cross_day = row["t"][:10]
        if cross_day not in calendar or completed_at(row) > registered:
            continue
        if not 0 <= calendar.index(day) - calendar.index(cross_day) <= 1:
            continue
        maintained = [bar for bar in complete if completed_at(row) <= completed_at(bar)]
        if maintained and all(above(bar) for bar in maintained):
            return [{"kind": "enrollment", "bar": complete[-1], "cross": row,
                     "key": f"{VERSION}:{candidate['code']}:enrollment:{candidate['registeredAt']}"}]
    return []


def remaining_days(signal_day, calendar):
    return max(0, WINDOW_DAYS - sum(day >= signal_day for day in calendar) + 1)


def message(candidate, alert, previous_close):
    row = alert["bar"]
    approx = row["estimated120Bars"] > 0 or row["estimated180Bars"] > 0
    title = "AH 매수검토" + ("·편입" if alert["kind"] == "enrollment" else "") + ("·근사" if approx else "")
    rate = f"{(row['c'] / previous_close - 1) * 100:+.2f}%" if previous_close else "자료 없음"
    return (f"[{title}] {candidate['name']}({candidate['code']})\n"
            f"MA120이 MA180 상향돌파 · MA10 > MA120\n"
            f"돌파봉 {alert['cross']['t']}\n"
            f"기준봉 {row['t']} · 종가 {row['c']:,.0f}원 · 전일대비 {rate}\n"
            + ("최근 돌파 후 조건 유지 · 신규 편입 시 1회 안내\n" if alert["kind"] == "enrollment" else "")
            + ("장기선 일부는 일봉 보완 근사입니다.\n" if approx else "")
            + f"https://leesgab-ctrl.github.io/korea-stock-ma-screener/monitor.html?view=ah&stock={candidate['code']}")
