"""Send one explicitly labelled image test, without recording a trading signal."""
import functools
import http.server
import json
import os
from pathlib import Path
import threading
import urllib.request
from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "data/candidate-monitor.json").read_text(encoding="utf-8"))
    candidate = next(c for c in payload["candidates"] if c["code"] == "145720")
    bar = next(b for b in reversed(candidate["displayCharts"]["intraday"]["series"]) if b.get("complete"))
    daily = candidate["displayCharts"]["daily"]["history"]
    prior = sorted((b for b in daily if b["d"] < bar["t"][:10]), key=lambda b: b["d"])[-3:]
    loss = (min(b["l"] for b in prior) / bar["c"] - 1) * 100 if len(prior) == 3 else None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 720, "height": 1400}, device_scale_factor=1.5)
            page.goto(f"http://127.0.0.1:{server.server_port}/monitor.html?historyChart=1&stock=145720")
            page.wait_for_function("document.querySelector('.detail-panel canvas')?.width > 0")
            page.wait_for_timeout(1500)
            panel = page.locator(".detail-panel")
            panel.evaluate("el => { const banner = document.createElement('div'); banner.textContent = '[테스트 · 실제 매수 알림 아님] 덴티움 저장 그래프'; banner.style.cssText = 'padding:12px;background:#fff1ba;font-weight:bold'; el.prepend(banner); }")
            image = panel.screenshot()
            browser.close()
    finally:
        server.shutdown()
    message = f"TEST ONLY - historical chart, not a live buy signal. Dentium 145720. Reference price KRW {bar['c']:,}."
    if loss is not None:
        message += f" Stop reference {loss:+.2f}% (previous 3 trading days low)."
    message += f" Bar: {bar['t']}."
    request = urllib.request.Request("https://ntfy.sh/" + os.environ["NTFY_TOPIC"].strip(), data=image, headers={
        "Content-Type": "image/png", "Filename": "dentium-chart-test.png",
        "Title": "[TEST] Dentium chart attachment", "Message": message,
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
