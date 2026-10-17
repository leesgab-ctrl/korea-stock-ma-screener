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
from recovery_rules import recovery_states
from notification_prices import price_with_day_change


def states(bars):
    result = []
    recovery = recovery_states(bars)
    confirmed = False
    convergence_armed = False
    convergence_confirmed = False
    convergence40_rose = False
    for i, b in enumerate(bars):
        p = bars[i - 1] if i else None
        values = [b.get(k) for k in ("m3", "m10", "m20", "m40", "m60")]
        valid = all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values)
        purple = False
        converging = False
        breakout = False
        valid_convergence = valid and p and all(isinstance(p.get(k), (int, float)) and math.isfinite(p[k]) and p[k] > 0 for k in ("m20", "m40", "m60")) and b["m60"] > p["m60"]
        if not valid_convergence:
            convergence_confirmed = False
            convergence40_rose = False
        if valid_convergence and b["m40"] > p["m40"]:
            convergence40_rose = True
        ordered_convergence = valid and all(b["m60"] <= b[k] <= b["m40"] for k in ("m3", "m10", "m20"))
        if valid_convergence and convergence_confirmed and convergence40_rose and ordered_convergence and b["m40"] < p["m40"]:
            converging = any(abs(b["m20"] - b[k]) / b[k] <= .016 + 1e-12
                             and abs(b["m20"] - b[k]) / b[k] < abs(p["m20"] - p[k]) / p[k]
                             for k in ("m40", "m60"))
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
                    convergence_confirmed = True
                near60 = b["m3"] < b["m60"] and (b["m60"] - b["m3"]) / b["m60"] <= .01 + 1e-12
                purple = confirmed and near60
        purple = purple or converging
        if converging and b["m3"] <= b["m40"]:
            convergence_armed = True
        spread = (max(values) / min(values) - 1) * 100 if valid else None
        # The agreed alert uses all five averages, not the four-line pink palette.
        compact = valid and spread <= .8 + 1e-12
        result.append({"purple": purple, "compact": compact, "spread": spread, "convergenceBreakout": breakout,
                       "recoveryMa10Breakout": recovery[i]["recoveryMa10Breakout"]})
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
    price_text = price_with_day_change(row["c"], row["t"], candidate.get("displayCharts", {}).get("quote", {}))
    daily = candidate.get("displayCharts", {}).get("daily", {}).get("history", [])
    # One low per trading date; missing sessions must not masquerade as complete coverage.
    lows = {r["d"]: r["l"] for r in daily if r.get("d", "") < row["t"][:10] and isinstance(r.get("l"), (int, float)) and r["l"] > 0}
    dates = sorted(lows)[-3:]
    stop = min(lows[d] for d in dates) if len(dates) == 3 else None
    label = {"purple": "진한 보라색 조정 관찰", "convergenceBreakout": "MA40·60 밀집 후 MA3 상향돌파 매수 후보", "coreMorningMa3": "핵심종목 아침 MA3 상승", "recoveryMa10Breakout": "조정 후 MA10·MA40 상향돌파"}.get(kind, "전체 5개 MA 밀착")
    if kind == "imageMa60NearWatch":
        label = "피라미드형 상승가능성 검토요청"
        message = (f"[피라미드형 상승가능성 검토요청] {candidate['name']}({candidate['code']})\n"
                   f"- MA3가 MA60 아래로 하락한 뒤, MA60 아래에서 두 선의 간격이 {state['maxGapPercent']:g}% 이내인 완성 30분봉이 {state['nearBelowBars']}개 연속 나왔습니다.\n"
                   "- 아직 상승 전환이나 MA60 돌파가 확인된 것은 아닙니다. 다음 움직임을 미리 검토해 주세요.\n"
                   f"- 현재 MA3·MA60 간격 {state['gapPercent']:+.2f}%\n"
                   f"- MA3 {row['m3']:,.2f} / MA60 {row['m60']:,.2f}\n"
                   f"- 기준봉 종가 {price_text}\n- 봉 완성 시각 {state['completedAt']}\n"
                   "사전 관찰 알림이며, 다음 날 상승이나 수익을 보장하지 않습니다.")
    elif kind == "imageMa60NearRebound":
        primary = state["stage"] == "primary"
        stage = "피라미드형3MA반등" if primary else "MA60 돌파 확인"
        label = f"이미지조건 {stage}"
        reason = "MA60 부근에서 MA3가 하락·수평 후 상승 전환했습니다." if primary else "MA60 부근에서 반등한 MA3가 MA60을 실제 상향 돌파했습니다."
        simultaneous = "- 상승 전환과 MA60 돌파가 같은 봉에서 발생해 확인 알림으로 합쳤습니다.\n" if state.get("simultaneous") else ""
        message = (f"[이미지조건 · {stage}] {candidate['name']}({candidate['code']})\n"
                   f"- 유형: {state['ruleName']}\n- {reason}\n{simultaneous}"
                   f"- 반등 직전 MA3·MA60 간격 {state['setupGapPercent']:+.2f}% / 현재 {state['gapPercent']:+.2f}%\n"
                   f"- 반등 확인봉 기준 MA60 대비 ±{state['maxGapPercent']:g}% 이내\n"
                   f"- MA3 {row['m3']:,.2f} / MA60 {row['m60']:,.2f}\n"
                   f"- 기준봉 종가 {price_text}\n- 봉 완성 시각 {state['completedAt']}\n"
                   "조건 관찰 신호이며, 이후 상승이나 수익을 보장하지 않습니다.")
    elif kind == "imagePyramidRecovery":
        primary = state["stage"] == "primary"
        stage = "1차 반등 포착" if primary else "MA60 돌파 확인"
        label = f"이미지조건 피라미드 {stage}"
        reason = "MA60 아래로 내려갔던 MA3가 상승을 시작했습니다." if primary else "MA3가 MA60을 상향 돌파했습니다."
        simultaneous = "- 상승 전환과 MA60 돌파가 같은 봉에서 발생해 확인 알림으로 합쳤습니다.\n" if state.get("simultaneous") else ""
        strength = " · 강한 상승 이력" if state.get("strongRise") is True else ""
        message = (f"[이미지조건 · {stage}] {candidate['name']}({candidate['code']})\n"
                   f"- 유형: {state['ruleName']}\n- MA20 급상승 후 급하락하는 피라미드형 조정\n"
                   f"- MA20 하루 상승 {state['riseDayPercent']:.2f}% ({state['riseDay']}, 전일 마지막 MA20 대비){strength}\n"
                   f"- MA20 상승 {state['risePercent']:.2f}% / 하락 {state['fallPercent']:.2f}%\n"
                   f"- {reason}\n{simultaneous}"
                   f"- MA3 {row['m3']:,.2f} / MA60 {row['m60']:,.2f}\n"
                   f"- 기준봉 종가 {price_text}\n- 봉 완성 시각 {state['completedAt']}\n"
                   "조건 관찰 신호이며, 이후 상승이나 수익을 보장하지 않습니다.")
    elif kind == "imageMa120Recovery":
        primary = state["stage"] == "primary"
        stage = "1차 포착" if primary else "2차 유지 확인"
        label = f"이미지조건 MA120 {stage}"
        reason = "최근 1~5봉 중 MA3가 MA120 아래였고, 현재 완성된 30분봉에서 MA120을 상향 돌파했습니다." if primary else \
            "돌파 직후 다음 완성봉도 MA3가 MA120 이상을 유지했습니다. MA3의 추가 상승 여부와는 별개입니다."
        message = (f"[이미지조건 · {stage}] {candidate['name']}({candidate['code']})\n"
                   f"- 유형: {state['ruleName']}\n- {reason}\n"
                   f"- 배경: MA120 상승 중 · MA60 > MA120\n"
                   f"- MA3 {row['m3']:,.2f} / MA60 {row['m60']:,.2f} / MA120 {row['m120']:,.2f}\n"
                   f"- 기준봉 종가 {price_text}\n- 봉 완성 시각 {state['completedAt']}\n"
                   "조건 관찰 신호이며, 이후 상승이나 수익을 보장하지 않습니다.")
    elif kind == "imageMa3Rise":
        stage = "1차 포착" if state["stage"] == "primary" else "2차 포착"
        label = f"이미지조건 {stage}"
        recovery = f"있음 (MA60을 {state['recoveryBars']}봉 만에 회복)" if state.get("fastRecovery") else "없음"
        lines = "·".join(str(value) for value in state["clusterLines"])
        message = (f"[이미지조건 · {stage}] {candidate['name']}({candidate['code']})\n"
                   f"- 유형: {state['ruleName']}\n"
                   f"- 상승추세의 눌림·밀집 구간에서 MA3가 {state['riseCount']}봉 연속 상승했습니다.\n"
                   f"- {len(state['clusterLines'])}선 밀집: MA{lines} 최대간격 {state['clusterSpreadPercent']:.2f}% (1% 미만)\n"
                   f"- 빠른 회복 참고: {recovery} · 필수 조건 아님\n"
                   f"- MA3 {row['m3']:,.2f} / MA10 {row['m10']:,.2f} / MA40 {row['m40']:,.2f} / MA60 {row['m60']:,.2f}\n"
                   f"- 기준봉 종가 {price_text}\n"
                   f"- 봉 완성 시각 {state['completedAt']}\n"
                   "조건 관찰 신호이며, 이후 상승이나 수익을 보장하지 않습니다.")
    elif kind in ("imageMa3Breakout", "imageMa10Breakout"):
        stage = "1차 포착" if kind == "imageMa3Breakout" else "확인 포착"
        line = "MA3" if kind == "imageMa3Breakout" else "MA10"
        label = f"이미지조건 {stage}"
        message = (f"[이미지조건 · {stage}] {candidate['name']}({candidate['code']})\n"
                   f"- 유형: {state['ruleName']}\n"
                   f"- 하락 역배열 후 {line}이 MA40을 상향 돌파했습니다.\n"
                   f"- MA3 {row['m3']:,.2f} / MA10 {row['m10']:,.2f} / MA40 {row['m40']:,.2f}\n"
                   f"- 기준봉 종가 {price_text}\n"
                   f"- 봉 완성 시각 {state['completedAt']}\n"
                   "상승을 보장하는 신호가 아니며, 실제 매수 전 현재가와 추세를 확인해 주세요.")
    elif kind == "coreMorningMa3":
        message = (f"핵심종목 {candidate['name']} 가 전일대비 MA 3이 2회이상 상승하였습니다.\n"
                   "매수를 고려한다면 MA3이 급히 상승시 하락안정을 고려하여 판단하기 바랍니다.\n"
                   f"- 전일 최종 / 09:30 / 10:00 확정 MA3: {state['previousMa3']:,.2f} / {state['firstMa3']:,.2f} / {state['secondMa3']:,.2f}\n"
                   f"- 기준봉 종가 {price_text}")
    elif kind == "recoveryMa10Breakout":
        message = (f"하락 중이던 {candidate['name']}의 MA10이 MA40을 상향 돌파했습니다.\n"
                   "매수를 고려한다면 MA3이 MA60 아래에 있는지 확인하여 판단하시기 바랍니다.\n"
                   f"- MA10 {row['m10']:,.2f} / MA40 {row['m40']:,.2f}\n"
                   f"- MA3 {row['m3']:,.2f} / MA60 {row['m60']:,.2f}\n"
                   f"- 기준봉 종가 {price_text}")
    else:
        message = f"{candidate['name']}({candidate['code']})\n- 전체 5개 MA 최대간격 {state['spread']:.2f}%\n- 포착기준매수가 {price_text}"
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


def initialize_recovery_baselines(payload):
    """Seed the new channel without sending or advancing existing alert cursors."""
    tracking = payload.get("colorNotificationState", {})
    changed = False
    for candidate in payload.get("candidates", []):
        registered = candidate.get("registeredAt")
        if not registered:
            continue
        bars = [b for b in candidate.get("displayCharts", {}).get("intraday", {}).get("series", []) if b.get("complete")]
        if not bars:
            continue
        key = candidate["code"] + "|" + registered
        saved = tracking.get(key)
        if saved and saved.get("recoveryRuleVersion") == 1:
            continue
        if saved is None:
            saved = {"last": bars[-1]["t"], **states(bars)[-1]}
        tracking[key] = {**saved, "recoveryRuleVersion": 1, "recoveryBaseline": bars[-1]["t"]}
        changed = True
    if changed:
        payload["colorNotificationState"] = tracking
    return changed


def notify(payload, current, topic, no_notify=False, sender=send):
    if no_notify or not topic:
        return 0, False
    changed = initialize_recovery_baselines(payload)
    tracking = payload.setdefault("colorNotificationState", {})
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
        saved = tracking[key]
        for b, state in zip(bars, flags):
            if b["t"] <= saved["last"] or b["t"] < registered:
                continue
            completion = dt.datetime.fromisoformat(b["t"]) + dt.timedelta(minutes=1 if b["t"][11:16] == "15:30" else 30)
            if completion > current:
                continue
            kinds = [kind for kind in ("compact", "purple", "convergenceBreakout", "recoveryMa10Breakout")
                     if state[kind] and not saved.get(kind)
                     and (kind != "recoveryMa10Breakout" or b["t"] > saved["recoveryBaseline"])]
            if kinds and dt.timedelta(0) <= current - completion <= dt.timedelta(minutes=90):
                try:
                    # Simultaneous conditions produce one notification, not two.
                    kind = next(kind for kind in ("recoveryMa10Breakout", "convergenceBreakout", "purple", "compact") if kind in kinds)
                    sender(topic, candidate, payload, b, state, kind, current)
                except Exception as exc:
                    payload.setdefault("colorNotificationErrors", []).append(str(exc))
                    break
                count += 1
            saved = {**saved, "last": b["t"], **state}
            tracking[key] = saved
            changed = True
    return count, changed
