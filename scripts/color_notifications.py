"""Forward-only completed-bar observation alerts, independent of menu group."""
import datetime as dt
import copy
from email.header import Header
import functools
import http.server
import json
import math
from pathlib import Path
import threading
import urllib.parse
import urllib.request


def states(bars):
    result = []
    confirmed = False
    convergence_armed = False
    for i, b in enumerate(bars):
        p = bars[i - 1] if i else None
        values = [b.get(k) for k in ("m3", "m10", "m20", "m40", "m60")]
        valid = all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values)
        purple = False
        converging = False
        breakout = False
        if valid and p and all(isinstance(p.get(k), (int, float)) and math.isfinite(p[k]) and p[k] > 0 for k in ("m3", "m40", "m60")):
            breakout = convergence_armed and b["m60"] > p["m60"] and p["m3"] <= p["m40"] and b["m3"] > b["m40"]
            if breakout or b["m60"] <= p["m60"]:
                convergence_armed = False
        else:
            convergence_armed = False
        if not valid or not p or not all(isinstance(p.get(k), (int, float)) for k in ("m40", "m60")):
            confirmed = False
        elif b["m60"] <= p["m60"] or b["m3"] >= b["m20"]:
            confirmed = False
        else:
            rising40 = b["m40"] > p["m40"]
            if not confirmed and not rising40:
                confirmed = False
            else:
                if b["m20"] < b["m40"]:
                    confirmed = True
                gap = abs(b["m40"] - b["m60"]) / b["m60"]
                prior_gap = abs(p["m40"] - p["m60"]) / p["m60"]
                converging = confirmed and not rising40 and gap <= .015 + 1e-12 and gap < prior_gap
                near60 = b["m3"] < b["m60"] and (b["m60"] - b["m3"]) / b["m60"] <= .01 + 1e-12
                purple = confirmed and (near60 or converging)
                if converging and b["m3"] <= b["m40"]:
                    convergence_armed = True
        spread = (max(values) / min(values) - 1) * 100 if valid else None
        # The agreed alert uses all five averages, not the four-line pink palette.
        compact = valid and spread <= .8 + 1e-12
        result.append({"purple": purple, "compact": compact, "spread": spread, "convergenceBreakout": breakout})
    return result


def chart_image(candidate, payload):
    from playwright.sync_api import sync_playwright
    root = Path(__file__).resolve().parents[1]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 720, "height": 1400}, device_scale_factor=1.5)
            page.route("**/data/candidate-monitor.json?*", lambda route: route.fulfill(json={**payload, "history": [candidate]}))
            page.goto(f"http://127.0.0.1:{server.server_port}/monitor.html?historyChart=1&stock={candidate['code']}")
            page.wait_for_function("document.querySelector('.detail-panel canvas')?.width > 0")
            page.locator('[data-chart-days="5"]').click()
            page.locator('[data-daily-bars="60"]').click()
            page.wait_for_timeout(300)
            image = page.locator(".detail-panel").screenshot()
            browser.close()
            return image
    finally:
        server.shutdown()


def send(topic, candidate, payload, row, state, kind, current):
    daily = candidate.get("displayCharts", {}).get("daily", {}).get("history", [])
    # One low per trading date; missing sessions must not masquerade as complete coverage.
    lows = {r["d"]: r["l"] for r in daily if r.get("d", "") < row["t"][:10] and isinstance(r.get("l"), (int, float)) and r["l"] > 0}
    dates = sorted(lows)[-3:]
    stop = min(lows[d] for d in dates) if len(dates) == 3 else None
    label = {"purple": "진한 보라색 조정 관찰", "convergenceBreakout": "MA40·60 밀집 후 MA3 상향돌파 매수 후보"}.get(kind, "전체 5개 MA 밀착")
    message = f"{candidate['name']}({candidate['code']})\n- 전체 5개 MA 최대간격 {state['spread']:.2f}%\n- 포착기준매수가 {row['c']:,.0f}원"
    if stop is not None and stop < row["c"]:
        message += f"\n- 손절기준 {(stop / row['c'] - 1) * 100:+.2f}% {stop:,.0f}원"
    else:
        message += "\n- 손절기준 산정 불가 · 매수 판단 보류"
    message += f"\n- 확인시간 {current:%Y-%m-%d %H:%M}분\n- 기준봉 {row['t']}\n조건 관찰 알림 · 실제 주문 아님"
    snapshot = copy.deepcopy(candidate)
    chart = snapshot["displayCharts"]["intraday"]
    chart["series"] = [b for b in chart["series"] if b["t"] <= row["t"]]
    chart["history"] = [b for b in chart.get("history", []) if b["t"] <= row["t"]]
    snapshot["displayCharts"]["daily"]["history"] = [b for b in daily if b["d"] <= row["t"][:10]]
    image = chart_image(snapshot, payload)
    request = urllib.request.Request("https://ntfy.sh/" + topic + "?" + urllib.parse.urlencode({"message": message}), data=image, headers={
        "Content-Type": "image/png", "Filename": f"{candidate['code']}-chart.png",
        "Title": Header(f"{candidate['name']} {label} 포착", "utf-8", maxlinelen=10000).encode(),
        "Click": f"https://leesgab-ctrl.github.io/korea-stock-ma-screener/monitor.html?stock={candidate['code']}",
        "Priority": "5",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        if not json.load(response).get("attachment"):
            raise RuntimeError("Attachment not confirmed")


def notify(payload, current, topic, no_notify=False, sender=send):
    if no_notify or not topic:
        return 0, False
    tracking = payload.setdefault("colorNotificationState", {})
    changed = False
    count = 0
    for candidate in payload.get("candidates", []):
        registered = candidate.get("registeredAt")
        if not registered:
            continue
        bars = [b for b in candidate.get("displayCharts", {}).get("intraday", {}).get("series", []) if b.get("complete")]
        if not bars:
            continue
        flags = states(bars)
        key = candidate["code"] + "|" + registered
        saved = tracking.get(key)
        if saved is None:
            # First deployment establishes a baseline and never replays old signals.
            tracking[key] = {"last": bars[-1]["t"], **flags[-1]}
            changed = True
            continue
        for b, state in zip(bars, flags):
            if b["t"] <= saved["last"] or b["t"] < registered:
                continue
            completion = dt.datetime.fromisoformat(b["t"]) + dt.timedelta(minutes=1 if b["t"][11:16] == "15:30" else 30)
            if completion > current:
                continue
            kinds = [kind for kind in ("compact", "purple", "convergenceBreakout") if state[kind] and not saved.get(kind)]
            if kinds and dt.timedelta(0) <= current - completion <= dt.timedelta(minutes=90):
                try:
                    # Simultaneous conditions produce one notification, not two.
                    kind = "convergenceBreakout" if "convergenceBreakout" in kinds else "purple" if "purple" in kinds else "compact"
                    sender(topic, candidate, payload, b, state, kind, current)
                except Exception as exc:
                    payload.setdefault("colorNotificationErrors", []).append(str(exc))
                    break
                count += 1
            saved = {"last": b["t"], **state}
            tracking[key] = saved
            changed = True
    return count, changed
