const state = { history: [], keyword: "" };
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
  const evaluated = state.history.filter((item) => item.outcome?.dataStatus === "ok");
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
  });
  elements.historyMeta.textContent = keyword
    ? `전체 ${state.history.length}종목 중 ${records.length}종목`
    : `종료 후보 ${state.history.length}종목`;
  elements.historyList.innerHTML = "";
  if (!records.length) {
    elements.historyList.innerHTML = `<div class="empty-list">${state.history.length ? "검색 결과가 없습니다." : "10거래일 관리가 끝난 종목부터 이력이 쌓입니다."}</div>`;
    return;
  }

  const table = document.createElement("table");
  table.className = "history-table";
  table.innerHTML = "<thead><tr><th>종목</th><th>등급</th><th>A-G 발생</th><th>매수 포착</th><th>포착가</th><th>최고수익률</th><th>최종수익률</th><th>+5%</th><th>종료일</th></tr></thead><tbody></tbody>";
  const body = table.querySelector("tbody");
  records.forEach((item) => {
    const outcome = item.outcome || {};
    const row = document.createElement("tr");
    const tier = item.candidateTier === "expanded" ? "확대 A-G" : "핵심 A-G";
    row.innerHTML = `
      <td><strong>${escapeHtml(item.name || "-")}</strong><span>${escapeHtml(item.code || "")}</span></td>
      <td><span class="history-tier ${item.candidateTier === "expanded" ? "expanded" : ""}">${tier}</span></td>
      <td>${escapeHtml(item.dailySignalDate || "-")}</td>
      <td>${item.signalTime ? escapeHtml(formatDateTime(item.signalTime)) : "없음"}${item.signalTarget ? `<br>${escapeHtml(item.signalTarget)}` : ""}</td>
      <td>${formatPrice(item.signalPrice)}</td>
      <td class="${returnClass(outcome.peakReturnPct)}">${formatReturn(outcome.peakReturnPct)}</td>
      <td class="${returnClass(outcome.finalReturnPct)}">${formatReturn(outcome.finalReturnPct)}</td>
      <td><span class="history-result ${outcome.reached5Pct ? "hit" : "miss"}">${outcome.reached5Pct ? `달성${outcome.reached5PctDate ? `<br>${escapeHtml(outcome.reached5PctDate)}` : ""}` : "미달"}</span></td>
      <td>${escapeHtml(item.archivedAt || outcome.finalDate || "-")}</td>`;
    body.append(row);
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
