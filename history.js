const state = { history: [], keyword: "", page: 0, excludedCodes: new Set() };
const PAGE_SIZE = 50;
const referenceHistoryMode = new URLSearchParams(window.location.search).get('type') === 'reference';
if (referenceHistoryMode) {
  document.body.classList.add('reference-history-page');
  document.title = '상승조정형이력';
  document.querySelector('h1').textContent = '상승조정형이력';
}
const formatter = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });
const percentFormatter = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
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
    if (referenceHistoryMode) {
      const referenceResponse = await fetch(`data/reference-history.json?t=${Date.now()}`, {cache: 'no-store'});
      if (!referenceResponse.ok) throw new Error(`HTTP ${referenceResponse.status}`);
      const reference = await referenceResponse.json();
      const originals = new Map(state.history.map(item => [item.id || item.code + '|' + item.registeredAt, item]));
      const chartExcluded = new Set((payload.manualExclusions || []).filter(row => row.category === 'chart_shape'
        || (!row.category && /차트\s*형태/.test(row.reason || ''))).map(row => row.code));
      state.history = reference.records.filter(row => originals.has(row.id) && !chartExcluded.has(row.code)).map(row => {
        const item = originals.get(row.id);
        const daily = [...new Map([...(item.displayCharts?.daily?.history || []), ...(item.displayCharts?.daily?.series || [])].map(bar => [bar.d, bar])).values()]
          .filter(bar => bar.complete !== false).sort((a, b) => a.d.localeCompare(b.d));
        const days = daily.filter(bar => bar.d > row.registeredAt.slice(0, 10)).slice(0, 10);
        return {...item, registeredAt: row.registeredAt, registrationPrice: row.registrationPrice,
          verificationEndDate: days.at(-1)?.d || row.registeredAt.slice(0, 10), referenceEntry: true,
          referenceEntryVerified: row.firstEntryVerified === true};
      });
    }
    renderSummary();
    renderHistory();
    elements.historyStatus.textContent = referenceHistoryMode ? '상승조정형 이력 정상' : "종료 후보 이력 정상";
    elements.historyUpdatedAt.textContent = payload.generatedAt
      ? `마지막 갱신 ${formatDateTime(payload.generatedAt)}`
      : "갱신 기록 없음";
    elements.historyNotice.classList.remove("error");
  } catch (error) {
    elements.historyStatus.textContent = "검증 이력을 불러오지 못했습니다";
    elements.historyUpdatedAt.textContent = String(error);
    elements.historyList.innerHTML = '<div class="empty-list">잠시 후 다시 시도해 주세요.</div>';
    elements.historyNotice.classList.add("error");
  }
}

function historyEvaluation(item) {
  if (item.referenceEntry && !item.referenceEntryVerified) return {valid: false, registered: null,
    end: null, baseline: null, finalPrice: null, entryUnknown: true};
  const registered = item.registeredAt?.slice(0, 10);
  const end = (item.verificationEndDate || item.archivedAt || item.outcome?.finalDate || "").slice(0, 10);
  const chart = item.displayCharts?.daily;
  const daily = [...new Map([...(chart?.history || []), ...(chart?.series || [])].map(row => [row.d, row])).values()]
    .filter(row => row.complete !== false && row.c > 0).sort((a, b) => a.d.localeCompare(b.d));
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
    return evaluated.length ? `${reached.length}종목 ${percentFormatter.format(100 * reached.length / evaluated.length)}%` : "평가 대기";
  };
  elements.completedCount.textContent = formatter.format(state.history.length);
  elements.evaluationNote.textContent = "관리기간 완료 종목만 표시 · 등록 다음 거래일부터 일봉 종가 기준 · 자료 부족은 해당 기간 집계 제외";
  if (referenceHistoryMode) elements.evaluationNote.textContent = `검증이력 중 상승조정형 확인 종목 · 최초 편입일 확인 ${state.history.filter(item => item.referenceEntryVerified).length}종목 / 미확인 ${state.history.filter(item => !item.referenceEntryVerified).length}종목 · 편입일 미확인은 수익률 집계 제외`;
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
  const pageCount = Math.max(1, Math.ceil(records.length / PAGE_SIZE));
  state.page = Math.min(state.page, pageCount - 1);
  const pageStart = state.page * PAGE_SIZE;
  records.slice(pageStart, pageStart + PAGE_SIZE).forEach((item, index) => {
    const result = historyEvaluation(item);
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${pageStart + index + 1}</td>
      <td><strong>${escapeHtml(item.name || "-")}</strong><span>${escapeHtml(item.code || "")}</span>${item.archiveReason === "manual_excluded" ? '<span>사용자 선정 제외</span>' : ""}</td>
      <td title="${escapeHtml(result.entryUnknown ? '등록 당시 30분봉 부족으로 최초 편입일 복원 불가' : result.registered || "")}">${escapeHtml(result.entryUnknown ? '편입일 미확인' : result.registered?.slice(5) || "기록 없음")}</td>
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
      frame.src = `monitor.html?historyChart=1&stock=${encodeURIComponent(item.code)}`
        + (referenceHistoryMode ? `&referenceStart=${encodeURIComponent(item.registeredAt)}&referenceEnd=${encodeURIComponent(item.verificationEndDate)}` : '');
      cell.append(frame); detail.append(cell); row.after(detail);
      frame.addEventListener("load", () => {
        const resize = () => {
          const panel = frame.contentDocument?.querySelector(".detail-panel");
          if (panel) frame.style.height = `${Math.ceil(panel.getBoundingClientRect().height)}px`;
        };
        const observer = new ResizeObserver(resize);
        const panel = frame.contentDocument?.querySelector(".detail-panel");
        if (panel) observer.observe(panel);
        resize();
      });
      button.setAttribute("aria-expanded", "true");
    });
  });
  elements.historyList.append(table);
  if (pageCount > 1) {
    const navigation = document.createElement("nav");
    navigation.className = "history-pagination";
    navigation.setAttribute("aria-label", "이력리스트 페이지");
    const position = document.createElement("span");
    position.textContent = `${state.page + 1} / ${pageCount}`;
    const changePage = delta => {
      state.page += delta;
      renderHistory();
      elements.historyList.scrollIntoView({ block: "start" });
    };
    const previous = document.createElement("button");
    previous.type = "button";
    previous.textContent = "이전 페이지";
    previous.disabled = state.page === 0;
    previous.addEventListener("click", () => changePage(-1));
    const next = document.createElement("button");
    next.type = "button";
    next.textContent = "다음 페이지";
    next.disabled = state.page === pageCount - 1;
    next.addEventListener("click", () => changePage(1));
    navigation.append(previous, position, next);
    elements.historyList.append(navigation);
  }
}

function returnClass(value) {
  if (value == null || value === "" || !Number.isFinite(Number(value))) return "";
  return Number(value) >= 0 ? "positive" : "negative";
}

function formatReturn(value) {
  return value != null && value !== "" && Number.isFinite(Number(value))
    ? `${Number(value) >= 0 ? "+" : ""}${percentFormatter.format(Number(value))}%`
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

elements.historyKeyword?.addEventListener("input", (event) => {
  state.keyword = event.target.value;
  state.page = 0;
  renderHistory();
});

loadHistory();
