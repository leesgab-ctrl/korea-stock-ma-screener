"""Keep alert prices and their day-over-day changes on the same basis."""
import datetime as dt
import math


def day_change_percent(price, timestamp, quote):
    change = None
    try:
        kst = dt.timezone(dt.timedelta(hours=9))
        signal_time = dt.datetime.fromisoformat(timestamp)
        quote_time = dt.datetime.fromisoformat(quote.get("time", ""))
        signal_day = signal_time.astimezone(kst).date() if signal_time.tzinfo else signal_time.date()
        quote_day = quote_time.astimezone(kst).date() if quote_time.tzinfo else quote_time.date()
        previous = quote.get("previousClose")
        if signal_day == quote_day and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                                           and math.isfinite(value) and value > 0 for value in (price, previous)):
            change = 100 * (price / previous - 1)
    except (ValueError, TypeError, AttributeError):
        pass
    return change


def price_with_day_change(price, timestamp, quote):
    change = day_change_percent(price, timestamp, quote)
    label = f"전일 대비 {change:+.2f}%" if change is not None else "전일 대비 확인 불가"
    return f"{price:,.0f}원 · {label}"
