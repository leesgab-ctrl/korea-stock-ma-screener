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
from notification_history import record_delivery
import notification_snapshot as delivery


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
    notification_title = None
    price_text = price_with_day_change(row["c"], row["t"], candidate.get("displayCharts", {}).get("quote", {}))
    daily = candidate.get("displayCharts", {}).get("daily", {}).get("history", [])
    # One low per trading date; missing sessions must not masquerade as complete coverage.
    lows = {r["d"]: r["l"] for r in daily if r.get("d", "") < row["t"][:10] and isinstance(r.get("l"), (int, float)) and r["l"] > 0}
    dates = sorted(lows)[-3:]
    stop = min(lows[d] for d in dates) if len(dates) == 3 else None
    label = {"purple": "진한 보라색 조정 관찰", "convergenceBreakout": "MA40·60 밀집 후 MA3 상향돌파 매수 후보", "coreMorningMa3": "핵심종목 아침 MA3 상승", "recoveryMa10Breakout": "조정 후 MA10·MA40 상향돌파"}.get(kind, "전체 5개 MA 밀착")
    if kind == "imageEnrollment":
        family = "첫날상승" if candidate.get("candidateTier") == "first_rise" else "AH"
        label = f"{family} 편입 전 이미지조건 포착 안내"
        stages = {"primary": "1차 포착", "confirmation": "확인 포착", "watch": "검토필요"}
        signals = "\n".join(f"- {item['ruleName']} · {item['completedAt']} · {stages.get(item['stage'], item['stage'])}"
                            + (f" · 전일대비 {item['changePct']:+.2f}%" if item.get("changePct") is not None else "")
                            for item in state["enrollmentSignals"])
        quote = candidate.get("displayCharts", {}).get("quote", {})
        current_price = price_with_day_change(quote.get("price"), quote.get("time", ""), quote) if quote.get("price") else "자료 없음"
        message = (f"[{family} 신규 편입 · 당일 선행 포착 안내] {candidate['name']}({candidate['code']})\n"
                   f"- 편입시각 {state['enrollmentAt']}\n{signals}\n"
                   f"- 편입 전 기준봉 종가 {price_text}\n- 최신 수집가 {current_price}\n"
                   "편입 전에 발생한 신호를 알려드립니다. 현재 새 돌파가 발생했다는 뜻은 아닙니다.")
    elif kind == "imageFirstRisePullback":
        label = "첫날상승 MA10·MA60 접근 매수검토"
        reference = state.get("dailyReference")
        daily_note = "- 일봉 참고: 전일 양봉 자료 없음 · 50% 조정은 필수조건 아님\n"
        if reference:
            daily_note = (f"- 일봉 참고({reference['day']}): 전일 양봉 몸통 중간 {reference['midpoint']:,.2f}원\n"
                          f"- 기준봉 종가의 전일 양봉 몸통 되돌림 {reference['retracementPercent']:.1f}% · 참고만, 자동 제외 아님\n")
        near = "±0.5% 이내 근접" if state["closeApproach"] else "±1% 이내 접근"
        message = (f"[첫날상승 · 매수검토 접근 안내] {candidate['name']}({candidate['code']})\n"
                   f"- 첫날상승 선정일 {state['selectionDay']}\n"
                   "- 상승했던 30분봉 MA10이 하락하여 MA60 부근에 접근했습니다.\n"
                   f"- MA10·MA60 간격 {state['gapPercent']:+.2f}% ({near})\n"
                   f"- MA10 {row['m10']:,.2f} / MA60 {row['m60']:,.2f}\n"
                   f"- 기준봉 종가 {price_text}\n{daily_note}"
                   f"- 봉 완성 시각 {state['completedAt']}\n"
                   "MA3 재상승은 아직 확인하지 않은 눌림 관찰 알림입니다. 추가 하락할 수 있으며 즉시 매수 신호가 아닙니다.")
    elif kind == "imageMa60NearWatch":
        label = "피라미드형 1차 검토필요"
        message = (f"[피라미드형 1차 검토필요 · 상승가능성 검토요청] {candidate['name']}({candidate['code']})\n"
                   f"- 선행 MA20 피라미드: 하루 상승 +{state['pyramid']['riseDayPercent']:.2f}% / 고점 후 하락 -{state['pyramid']['fallPercent']:.2f}%\n"
                   f"- MA3가 MA60 아래로 하락한 뒤, MA60 아래에서 두 선의 간격이 {state['maxGapPercent']:g}% 이내인 완성 30분봉이 {state['nearBelowBars']}개 연속 나왔습니다.\n"
                   "- 아직 상승 전환이나 MA60 돌파가 확인된 것은 아닙니다. 다음 움직임을 미리 검토해 주세요.\n"
                   f"- 현재 MA3·MA60 간격 {state['gapPercent']:+.2f}%\n"
                   f"- MA3 {row['m3']:,.2f} / MA60 {row['m60']:,.2f}\n"
                   f"- 기준봉 종가 {price_text}\n- 봉 완성 시각 {state['completedAt']}\n"
                   "사전 관찰 알림이며, 다음 날 상승이나 수익을 보장하지 않습니다.")
    elif kind == "imageMa60NearRebound":
        stage = "MA60 돌파 확인"
        label = f"이미지조건 {stage}"
        reason = "피라미드형 1차 검토 조건 성립 후 MA3가 MA60을 실제 상향 돌파했습니다."
        message = (f"[이미지조건 · {stage}] {candidate['name']}({candidate['code']})\n"
                   f"- 유형: {state['ruleName']}\n- {reason}\n"
                   f"- 선행 MA20 피라미드: 하루 상승 +{state['pyramid']['riseDayPercent']:.2f}% / 고점 후 하락 -{state['pyramid']['fallPercent']:.2f}%\n"
                   f"- 1차 기준: MA60 아래 {state['maxGapPercent']:g}% 이내 {state['nearBelowBars']}봉 유지\n"
                   f"- 현재 MA3·MA60 간격 {state['gapPercent']:+.2f}%\n"
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
        if primary and "declining_ma40_breakout" in state.get("triggerPaths", []):
            ma40_reason = "MA40 < MA60이고 두 선이 직전 봉보다 하락 중인 상태에서 MA3가 MA40을 상향 돌파했습니다."
            reason = reason + "\n- " + ma40_reason if "ma120_recross" in state["triggerPaths"] else ma40_reason
        ma40_required = primary and state.get("requireMa40AboveMa120ForPrimary") is True
        background = "MA120 상승 중 · MA40 > MA120 · MA60 > MA120" if ma40_required else "MA120 상승 중 · MA60 > MA120"
        ma40_note = f" / MA40 {row['m40']:,.2f}" if ma40_required else ""
        message = (f"[이미지조건 · {stage}] {candidate['name']}({candidate['code']})\n"
                   f"- 유형: {state['ruleName']}\n- {reason}\n"
                   f"- 배경: {background}\n"
                   f"- MA3 {row['m3']:,.2f}{ma40_note} / MA60 {row['m60']:,.2f} / MA120 {row['m120']:,.2f}\n"
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
        ma120_note = (f"- 발송 추가조건: MA120 상승 {state['m120Previous']:,.2f} → {state['m120']:,.2f}\n"
                      if kind == "imageMa10Breakout" and state.get("requireRisingMa120ForConfirmation") else "")
        if kind == "imageMa10Breakout" and state.get("requireMa60AboveMa120ForConfirmation"):
            ma120_note += f"- 정배열 확인: MA60 {row['m60']:,.2f} > MA120 {state['m120']:,.2f}\n"
        if kind == "imageMa10Breakout" and state.get("requireRisingMa120ForConfirmation"):
            notification_title = "하락중인 MA10이 MA40 돌파 MA120은 상승중"
        label = f"이미지조건 {stage}"
        heading = notification_title or f"이미지조건 · {stage}"
        intro = (f"{candidate['name']}({candidate['code']})\n[{heading}]\n" if notification_title else
                 f"[{heading}] {candidate['name']}({candidate['code']})\n")
        message = (intro +
                   f"- 유형: {state['ruleName']}\n"
                   f"- 하락 역배열 후 {line}이 MA40을 상향 돌파했습니다.\n{ma120_note}"
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
    message += f"\n- 조건 검사시각 {current:%Y-%m-%d %H:%M}분\n- 기준봉 {row['t']}\n조건 관찰 알림 · 실제 주문 아님"
    snapshot = copy.deepcopy(candidate)
    chart = snapshot["displayCharts"]["intraday"]
    chart["series"] = [b for b in chart["series"] if b["t"] <= row["t"]]
    chart["history"] = [b for b in chart.get("history", []) if b["t"] <= row["t"]]
    snapshot["displayCharts"]["daily"]["history"] = [b for b in daily if b["d"] <= row["t"][:10]]
    image = chart_image(snapshot, payload)
    price_snapshot = delivery.capture(candidate["code"])
    message += delivery.message(price_snapshot)
    request = urllib.request.Request("https://ntfy.sh/" + topic + "?" + urllib.parse.urlencode({"message": message}), data=image, headers={
        "Content-Type": "image/png", "Filename": f"{candidate['code']}-chart.png",
        "Title": Header(notification_title or f"{candidate['name']} {label} 포착", "utf-8", maxlinelen=10000).encode(),
        "Click": f"https://leesgab-ctrl.github.io/korea-stock-ma-screener/monitor.html?stock={candidate['code']}",
        "Priority": "5",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        if not json.load(response).get("attachment"):
            raise RuntimeError("Attachment not confirmed")
    # Keep the receipt only after ntfy acknowledges the attachment.
    signature = None
    if kind.startswith("image"):
        registered = state.get("membershipRegisteredAt") or candidate.get("registeredAt") or candidate.get("dailySignalDate")
        signature = f"{candidate['code']}|{registered}|{row['t']}|{state.get('stage', '')}"
        if state.get("signalType") in ("ma3Rise", "ma3Ma120", "ma20Pyramid", "ma3Ma60Near", "firstRiseMa10Ma60"):
            signature += "|" + state["signalType"]
    record_delivery(payload, candidate["code"], candidate["name"], row["t"], delivery.now().isoformat(), kind,
                    state.get("ruleName") or label, signature=signature, stage=state.get("stage", ""),
                    price_snapshot=price_snapshot, signal_price=row["c"], condition={
                        key: state[key] for key in ("ruleId", "ruleVersion", "signalType", "requireRisingMa120ForConfirmation", "requireMa60AboveMa120ForConfirmation", "requireMa40AboveMa120ForPrimary", "triggerPaths", "m120Previous", "m120", "m40Previous", "m60Previous", "m40", "m60")
                        if key in state})


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
