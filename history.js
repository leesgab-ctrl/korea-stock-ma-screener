const state = { history: [], keyword: "", excludedCodes: new Set() };
const formatter = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });
const elements = {
  completedCount: document.querySelector("#completedCount"),
  evaluationNote: document.querySelector("#evaluationNote"),
  reachedCount: document.querySelector("#reachedCount"),
  reachedRate: document.querySelector("#reachedRate"),
  historyNotice: document.querySelector("#historyNotice"),
  historyStatus: document.querySelector("#historyStatus"),
  historyUpdatedAt: document.querySelector("#historyUpdatedAt"),
  historyMeta: document.querySelector("#historyMeta"),
  historyList: document.querySelector("#historyList"),
  historyKeyword: document.querySelector("#historyKeyword"),
  refreshButton: document.querySelector("#refreshButton"),
};

async function loadHistory() {
  elements.refreshButton.disabled = true;
  try {
    const response = await fetch(`data/candidate-monitor.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const activeCodes = new Set((payload.candidates || []).map(item => item.code));
    state.history = (Array.isArray(payload.history) ? payload.history : []).filter(item => {
      const endedManual = item.archiveReason === "manual_excluded" && item.verificationEndDate
        && Date.parse(payload.generatedAt) >= Date.parse(`${item.verificationEndDate}T20:00:00+09:00`);
      return (item.archiveReason === "window_completed" || item.verificationStatus === "completed" || endedManual) && !activeCodes.has(item.code);
    });
    state.excludedCodes = new Set((payload.manualExclusions || []).map((r) => r.code));
    renderSummary();
    renderHistory();
    elements.historyStatus.textContent = "종료 후보 이력 정상";
    elements.historyUpdatedAt.textContent = payload.generatedAt
      ? `마지막 갱신 ${formatDateTime(payload.generatedAt)}`
      : "갱신 기록 없음";
    elements.historyNotice.classList.remove("error");
  } catch (error) {
    elements.historyStatus.textContent = "검증 이력을 불러오지 못했습니다";
    elements.historyUpdatedAt.textContent = String(error);
    elements.historyList.innerHTML = '<div class="empty-list">잠시 후 다시 시도해 주세요.</div>';
    elements.historyNotice.classList.add("error");
  } finally {
    elements.refreshButton.disabled = false;
  }
}

function historyEvaluation(item) {
  const registered = item.registeredAt?.slice(0, 10);
  const end = (item.verificationEndDate || item.archivedAt || item.outcome?.finalDate || "").slice(0, 10);
  const chart = item.displayCharts?.daily;
  const daily = (chart?.history?.length ? chart.history : chart?.series || []).filter(row => row.complete !== false && row.c > 0).sort((a, b) => a.d.localeCompare(b.d));
  const baseline = Number.isFinite(item.registrationPrice) && item.registrationPrice > 0 ? item.registrationPrice : null;
  const prices = [
    ...(item.displayCharts?.intraday?.series || []).map(row => ({time: row.t, price: row.c})),
    {time: item.intraday?.quoteTime, price: item.intraday?.quotePrice},
    {time: item.lastPriceTime, price: item.lastPrice},
    ...daily.map(row => ({time: `${row.d}T15:30:00+09:00`, price: row.c})),
  ].filter(row => row.price > 0 && Date.parse(row.time) >= Date.parse(item.registeredAt) && row.time.slice(0, 10) <= end)
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
  const finalPrice = prices.at(-1)?.price ?? null;
  const rows = daily.filter(row => Date.parse(`${row.d}T15:30:00+09:00`) >= Date.parse(item.registeredAt) && row.d <= end);
  if (!baseline || !end || !rows.length) return {valid: false, registered, end, baseline, finalPrice};
  const hit = rows.find(row => row.c >= baseline * 1.05);
  const windowRows = rows.filter(row => row.d > registered).slice(0, 10);
  const fiveRows = windowRows.slice(0, 5);
  const peak = windowRows.length === 10 ? Math.max(...windowRows.map(row => row.c)) : null;
  const fiveReturn = fiveRows.length === 5 ? (Math.max(...fiveRows.map(row => row.c)) / baseline - 1) * 100 : null;
  return {valid: true, registered, end, baseline, finalPrice, peak, fiveReturn, peakReturn: peak == null ? null : (peak / baseline - 1) * 100,
    targetDate: hit?.d, duration: hit ? new Set([registered, ...daily.filter(row => row.d >= registered && row.d <= hit.d).map(row => row.d)]).size - 1 : null};
}

function renderSummary() {
  const results = state.history.map(historyEvaluation);
  const summaryFor = key => {
    const evaluated = results.filter(result => Number.isFinite(result[key]));
    const reached = evaluated.filter(result => result[key] >= 5);
    return evaluated.length ? `${reached.length}종목 ${formatter.format(100 * reached.length / evaluated.length)}%` : "평가 대기";
  };
  elements.completedCount.textContent = formatter.format(state.history.length);
  elements.evaluationNote.textContent = "관리기간 완료 종목만 표시 · 등록 다음 거래일부터 일봉 종가 기준 · 자료 부족은 해당 기간 집계 제외";
  elements.reachedCount.textContent = summaryFor("fiveReturn");
  elements.reachedRate.textContent = summaryFor("peakReturn");
}

function renderHistory() {
  const keyword = state.keyword.trim().toLowerCase();
  const records = state.history.filter((item) => {
    if (!keyword) return true;
    return String(item.name || "").toLowerCase().includes(keyword) || String(item.code || "").includes(keyword);
  }).sort((a, b) => String(b.registeredAt || "").localeCompare(String(a.registeredAt || ""))
    || String(a.name || a.code).localeCompare(String(b.name || b.code), "ko"));
  elements.historyList.innerHTML = "";
  elements.historyMeta.textContent = `저장 이력 ${records.length}종목`;
  if (!records.length) {
    elements.historyList.innerHTML = `<div class="empty-list">${state.history.length ? "검색 결과가 없습니다." : "10거래일 관리가 끝난 종목부터 이력이 쌓입니다."}</div>`;
    return;
  }

  const table = document.createElement("table");
  table.className = "history-table";
  table.innerHTML = "<thead><tr><th>No</th><th>종목명</th><th>등록일</th><th>등록가<br>최종가</th><th>5일<br>수익률</th><th>달성<br>기간</th><th>10일최고<br>수익률</th><th>종료일</th></tr></thead><tbody></tbody>";
  const body = table.querySelector("tbody");
  table.querySelectorAll("th").forEach((cell, index) => {
    cell.title = cell.textContent;
  });
  records.forEach((item, index) => {
    const result = historyEvaluation(item);
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${index + 1}</td>
      <td><strong>${escapeHtml(item.name || "-")}</strong><span>${escapeHtml(item.code || "")}</span>${item.archiveReason === "manual_excluded" ? '<span>사용자 선정 제외</span>' : ""}</td>
      <td title="${escapeHtml(result.registered || "")}">${escapeHtml(result.registered?.slice(5) || "기록 없음")}</td>
      <td class="history-prices"><span title="등록가">${formatPrice(result.baseline)}</span><span title="최종가">${formatPrice(result.finalPrice)}</span></td>
      <td title="등록 다음 거래일부터 5거래일 일봉 종가 중 최고수익률" class="${returnClass(result.fiveReturn)} ${result.fiveReturn >= 5 ? "target-hit" : ""}">${formatReturn(result.fiveReturn)}</td>
      <td title="목표 +5% 달성기간">${result.duration == null ? "-" : result.duration === 0 ? "당일" : `${result.duration}일`}</td>
      <td class="${returnClass(result.peakReturn)} ${result.peakReturn >= 5 ? "target-hit" : ""}">${formatReturn(result.peakReturn)}</td>
      <td title="${escapeHtml(result.end || "")}">${escapeHtml(result.end?.slice(5) || "-")}</td>`;
    body.append(row);
    row.querySelectorAll("td").forEach((cell, index) => {
      cell.dataset.label = table.querySelectorAll("th")[index].textContent;
    });
    const name = row.querySelector("strong");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-stock-button";
    button.textContent = item.name || item.code;
    button.title = item.name || item.code;
    button.setAttribute("aria-expanded", "false");
    name.replaceWith(button);
    button.addEventListener("click", () => {
      const existing = row.nextElementSibling;
      if (existing?.classList.contains("history-chart-row")) {
        existing.remove(); button.setAttribute("aria-expanded", "false"); return;
      }
      const detail = document.createElement("tr");
      detail.className = "history-chart-row";
      const cell = document.createElement("td"); cell.colSpan = 8;
      const frame = document.createElement("iframe");
      frame.title = `${item.name || item.code} 30분봉과 일봉 전체 이력`;
      frame.src = `monitor.html?historyChart=1&stock=${encodeURIComponent(item.code)}`;
      cell.append(frame); detail.append(cell); row.after(detail);
      frame.addEventListener("load", () => {
        const resize = () => { frame.style.height = `${frame.contentDocument?.body.scrollHeight || 1100}px`; };
        const observer = new ResizeObserver(resize);
        if (frame.contentDocument?.body) observer.observe(frame.contentDocument.body);
        resize();
      });
      button.setAttribute("aria-expanded", "true");
    });
  });
  elements.historyList.append(table);
}

function returnClass(value) {
  if (value == null || value === "" || !Number.isFinite(Number(value))) return "";
  return Number(value) >= 0 ? "positive" : "negative";
}

function formatReturn(value) {
  return value != null && value !== "" && Number.isFinite(Number(value))
    ? `${Number(value) >= 0 ? "+" : ""}${formatter.format(Number(value))}%`
    : "-";
}

function formatPrice(value) {
  return value != null && value !== "" && Number.isFinite(Number(value)) ? `${formatter.format(Number(value))}원` : "-";
}

function formatDuration(value) {
  if (value == null || value === "" || !Number.isFinite(Number(value))) return "-";
  return Number(value) === 0 ? "당일" : `${formatter.format(Number(value))}거래일`;
}

function formatElapsedDays(startDate, endDate) {
  const start = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "-";
  return `${Math.round((end - start) / 86400000)}일`;
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "-");
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

elements.refreshButton.addEventListener("click", loadHistory);
elements.historyKeyword?.addEventListener("input", (event) => {
  state.keyword = event.target.value;
  renderHistory();
});

loadHistory();
