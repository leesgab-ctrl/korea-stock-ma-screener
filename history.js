const state = { history: [], keyword: "", excludedCodes: new Set() };
const formatter = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });
const elements = {
  completedCount: document.querySelector("#completedCount"),
  evaluatedCount: document.querySelector("#evaluatedCount"),
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
    state.history = Array.isArray(payload.history) ? payload.history : [];
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

function renderSummary() {
  const evaluated = state.history.filter((item) => !state.excludedCodes.has(item.code) && item.archiveReason !== "manual_excluded" && item.outcome?.dataStatus === "ok");
  const reached = evaluated.filter((item) => item.outcome?.reached5Pct);
  const rate = evaluated.length ? (100 * reached.length) / evaluated.length : 0;
  elements.completedCount.textContent = formatter.format(state.history.length);
  elements.evaluatedCount.textContent = formatter.format(evaluated.length);
  elements.reachedCount.textContent = formatter.format(reached.length);
  elements.reachedRate.textContent = `${formatter.format(rate)}%`;
}

function renderHistory() {
  const keyword = state.keyword.trim().toLowerCase();
  const records = state.history.filter((item) => {
    if (!keyword) return true;
    return String(item.name || "").toLowerCase().includes(keyword) || String(item.code || "").includes(keyword);
  }).sort((a, b) => String(b.dailySignalDate || "").localeCompare(String(a.dailySignalDate || ""))
    || String(a.name || a.code).localeCompare(String(b.name || b.code), "ko"));
  elements.historyMeta.textContent = keyword
    ? `전체 ${state.history.length}종목 중 ${records.length}종목`
    : `종료 후보 ${state.history.length}종목 · 사용자 선정 제외 ${state.excludedCodes.size}종목 (달성률 집계 제외)`;
  elements.historyList.innerHTML = "";
  if (!records.length) {
    elements.historyList.innerHTML = `<div class="empty-list">${state.history.length ? "검색 결과가 없습니다." : "10거래일 관리가 끝난 종목부터 이력이 쌓입니다."}</div>`;
    return;
  }

  const table = document.createElement("table");
  table.className = "history-table";
  table.innerHTML = "<thead><tr><th>종목</th><th>등급</th><th>A-G 발생일</th><th>화면 등록일·기준가</th><th>매수포착일</th><th>포착가</th><th>목표달성일</th><th>달성가</th><th>등록 후 기간</th><th>최고수익률</th><th>최종수익률</th><th>종료일</th></tr></thead><tbody></tbody>";
  const body = table.querySelector("tbody");
  records.forEach((item) => {
    const outcome = item.outcome || {};
    const row = document.createElement("tr");
    const tier = item.candidateTier === "expanded" ? "확대" : "핵심";
    const targetDate = outcome.reached5Pct ? outcome.reached5PctDate : null;
    const basisDate = outcome.basisDate;
    const targetRange = basisDate && targetDate
      ? `<span class="history-subline">(${formatElapsedDays(basisDate, targetDate)})</span>`
      : "";
    const signalDate = item.signalTime ? String(item.signalTime).slice(0, 10) : null;
    const afterSignal = signalDate && targetDate && targetDate >= signalDate
      ? `<span class="history-subline">포착 후 (${formatElapsedDays(signalDate, targetDate)})</span>`
      : targetDate && signalDate ? '<span class="history-subline">포착 전 달성</span>' : "";
    row.innerHTML = `
      <td><strong>${escapeHtml(item.name || "-")}</strong><span>${escapeHtml(item.code || "")}</span>${item.archiveReason === "manual_excluded" ? '<span>사용자 선정 제외</span>' : ""}</td>
      <td><span class="history-tier ${item.candidateTier === "expanded" ? "expanded" : ""}">${tier}</span></td>
      <td>${escapeHtml(item.dailySignalDate || "-")}</td>
      <td>${escapeHtml(item.registeredAt ? String(item.registeredAt).slice(0, 10) : "기록 없음")}<span class="history-subline">${formatPrice(item.registrationPrice)}</span></td>
      <td>${item.signalTime ? escapeHtml(formatDateTime(item.signalTime)) : "없음"}${item.signalTarget ? `<span class="history-subline">${escapeHtml(item.signalTarget)} 돌파</span>` : ""}</td>
      <td>${formatPrice(item.signalPrice)}</td>
      <td>${targetDate ? `<span class="history-result hit">${escapeHtml(targetDate)}</span>${targetRange}${afterSignal}` : `<span class="history-result miss">${outcome.dataStatus === "ok" ? "미달" : "자료 부족"}</span>`}</td>
      <td>${targetDate ? formatPrice(outcome.targetPrice) : "-"}</td>
      <td>${targetDate ? formatDuration(outcome.reached5PctTradingDays) : "-"}</td>
      <td class="${returnClass(outcome.peakReturnPct)}">${formatReturn(outcome.peakReturnPct)}</td>
      <td class="${returnClass(outcome.finalReturnPct)}">${formatReturn(outcome.finalReturnPct)}</td>
      <td>${escapeHtml(item.archivedAt || outcome.finalDate || "-")}</td>`;
    body.append(row);
    const name = row.querySelector("strong");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-stock-button";
    button.textContent = item.name || item.code;
    button.setAttribute("aria-expanded", "false");
    name.replaceWith(button);
    button.addEventListener("click", () => {
      const existing = row.nextElementSibling;
      if (existing?.classList.contains("history-chart-row")) {
        existing.remove(); button.setAttribute("aria-expanded", "false"); return;
      }
      const detail = document.createElement("tr");
      detail.className = "history-chart-row";
      const cell = document.createElement("td"); cell.colSpan = 12;
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
elements.historyKeyword.addEventListener("input", (event) => {
  state.keyword = event.target.value;
  renderHistory();
});

loadHistory();
