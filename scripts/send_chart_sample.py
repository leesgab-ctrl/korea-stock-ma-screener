"""Send one explicitly labelled image test, without recording a trading signal."""
import functools
import datetime as dt
from email.header import Header
import http.server
import json
import os
from pathlib import Path
import threading
import urllib.request
import urllib.parse
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "data/candidate-monitor.json").read_text(encoding="utf-8"))
    candidate = next(c for c in payload["candidates"] if c["code"] == "145720")
    bar = next(b for b in reversed(candidate["displayCharts"]["intraday"]["series"]) if b.get("complete"))
    daily = candidate["displayCharts"]["daily"]["history"]
    prior = sorted((b for b in daily if b["d"] < bar["t"][:10]), key=lambda b: b["d"])[-3:]
    stop = min(b["l"] for b in prior) if len(prior) == 3 else None
    loss = (stop / bar["c"] - 1) * 100 if stop is not None else None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 720, "height": 1400}, device_scale_factor=1.5)
            preview = {**payload, "history": [candidate]}
            page.route("**/data/candidate-monitor.json?*", lambda route: route.fulfill(json=preview))
            page.goto(f"http://127.0.0.1:{server.server_port}/monitor.html?historyChart=1&stock=145720")
            page.wait_for_function("document.querySelector('.detail-panel canvas')?.width > 0")
            page.wait_for_timeout(1500)
            page.locator('[data-daily-bars="60"]').click()
            page.wait_for_timeout(300)
            panel = page.locator(".detail-panel")
            panel.evaluate("el => { const banner = document.createElement('div'); banner.textContent = '최종 알림 형식 확인용 · 과거 봉 기준'; banner.style.cssText = 'padding:12px;background:#f1f3f5;font-weight:bold'; el.prepend(banner); }")
            image = panel.screenshot()
            browser.close()
    finally:
        server.shutdown()
    values = [bar[k] for k in ("m3", "m10", "m20", "m40", "m60")]
    spread = (max(values) / min(values) - 1) * 100
    message = f"덴티움(145720)\n- 전체 5개 MA 최대간격 {spread:.2f}%\n- 포착기준매수가 {bar['c']:,.0f}원"
    if loss is not None:
        message += f"\n- 손절기준 {loss:+.2f}% {stop:,.0f}원"
    checked = dt.datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
    message += f"\n- 확인시간 {checked}분\n형식 확인용 · 과거 기준봉 {bar['t']}"
    query = urllib.parse.urlencode({"message": message})
    request = urllib.request.Request("https://ntfy.sh/" + os.environ["NTFY_TOPIC"].strip() + "?" + query, data=image, headers={
        "Content-Type": "image/png", "Filename": "dentium-chart-test.png",
        "Title": Header("덴티움 이평선 밀착 포착 · 형식 확인용", "utf-8", maxlinelen=10000).encode(),
        "Click": "https://leesgab-ctrl.github.io/korea-stock-ma-screener/monitor.html?stock=145720",
        "Priority": "3",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
        if not result.get("attachment"):
            raise RuntimeError("Server did not confirm image attachment")
        print("Test image notification accepted; id:", result["id"])


if __name__ == "__main__":
    main()
