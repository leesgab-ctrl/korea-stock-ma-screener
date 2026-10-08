const requestedCode = new URLSearchParams(window.location.search).get("stock");
const PENDING_EXCLUSION_KEY = "koreaStockMonitor.pendingExclusions";
function readPendingExclusions() {
  try {
    return new Map(JSON.parse(sessionStorage.getItem(PENDING_EXCLUSION_KEY) || "[]")
      .filter(([code, entry]) => /^\d{6}$/.test(code) && ["exclude", "restore"].includes(entry.action) && Number.isFinite(entry.at)));
  } catch { return new Map(); }
}
let exclusionPollTimer;
const state = {
  pendingExclusions: readPendingExclusions(),
  exclusionMessage: "",
  payload: null,
  lastLoadedAt: 0,
  positions: null,
  view: "target",
  keyword: "",
  selectedCode: requestedCode && /^\d{6}$/.test(requestedCode) ? requestedCode : null,
  selectedPositionCode: null,
  deepLinkPending: Boolean(requestedCode),
};
const GITHUB_TOKEN_KEY = "koreaStockMonitor.githubToken";
const WORKFLOW_DISPATCH_URL = "https://api.github.com/repos/leesgab-ctrl/korea-stock-ma-screener/actions/workflows/manage-position.yml/dispatches";
const MAX_STOP_PCT = 5;

const elements = {
  activeCount: document.querySelector("#activeCount"),
  setupCount: document.querySelector("#setupCount"),
  signalCount: document.querySelector("#signalCount"),
  signaledCount: document.querySelector("#signaledCount"),
  risingCount: document.querySelector("#risingCount"),
  positionCount: document.querySelector("#positionCount"),
  detectedCount: document.querySelector("#detectedCount"),
  targetRate: document.querySelector("#targetRate"),
  asOf: document.querySelector("#asOf"),
  runStatus: document.querySelector("#runStatus"),
  updatedAt: document.querySelector("#updatedAt"),
  pushState: document.querySelector("#pushState"),
  candidateMeta: document.querySelector("#candidateMeta"),
  candidateList: document.querySelector("#candidateList"),
  positionList: document.querySelector("#positionList"),
  template: document.querySelector("#candidateTemplate"),
  keyword: document.querySelector("#keyword"),
  refreshButton: document.querySelector("#refreshButton"),
  criteriaButton: document.querySelector("#criteriaButton"),
  criteriaDialog: document.querySelector("#criteriaDialog"),
  criteriaDialogClose: document.querySelector("#criteriaDialogClose"),
  detailPanel: document.querySelector(".detail-panel"),
  detailName: document.querySelector("#detailName"),
  detailMeta: document.querySelector("#detailMeta"),
  naverLink: document.querySelector("#naverLink"),
  emptyDetail: document.querySelector("#emptyDetail"),
  detailContent: document.querySelector("#detailContent"),
  detailBadge: document.querySelector("#detailBadge"),
  detailProgress: document.querySelector("#detailProgress"),
  dailySignal: document.querySelector("#dailySignal"),
  remainingDays: document.querySelector("#remainingDays"),
  signalMessage: document.querySelector("#signalMessage"),
  chart: document.querySelector("#maChart"),
  dailyChart: document.querySelector("#dailyChart"),
  dailyChartMeta: document.querySelector("#dailyChartMeta"),
  positionManagerButton: document.querySelector("#positionManagerButton"),
  positionDialog: document.querySelector("#positionDialog"),
  positionDialogClose: document.querySelector("#positionDialogClose"),
  positionCancelButton: document.querySelector("#positionCancelButton"),
  positionForm: document.querySelector("#positionForm"),
  positionFormSubtitle: document.querySelector("#positionFormSubtitle"),
  positionCode: document.querySelector("#positionCode"),
  positionName: document.querySelector("#positionName"),
  positionBuyPrice: document.querySelector("#positionBuyPrice"),
  positionQuantity: document.querySelector("#positionQuantity"),
  positionSellPrice: document.querySelector("#positionSellPrice"),
  positionStopPrice: document.querySelector("#positionStopPrice"),
  stopPolicyNote: document.querySelector("#stopPolicyNote"),
  positionTargetPct: document.querySelector("#positionTargetPct"),
  githubConnection: document.querySelector("#githubConnection"),
  githubToken: document.querySelector("#githubToken"),
  clearGithubToken: document.querySelector("#clearGithubToken"),
  connectionState: document.querySelector("#connectionState"),
  positionFormStatus: document.querySelector("#positionFormStatus"),
  positionSubmitButton: document.querySelector("#positionSubmitButton"),
};

const detailPanelHome = elements.detailPanel.parentNode;
const detailPanelAnchor = document.createComment("detail-panel-home");
detailPanelHome.insertBefore(detailPanelAnchor, elements.detailPanel);

const statusLabels = {
  signal: "매수 검토",
  signaled: "매수포착 완료",
  setup: "매수 준비",
  rising: "상승 진행",
  watching: "관찰 중",
  insufficient: "기준자료 부족",
  ineligible: "초기조건 제외",
  excluded: "구조적 약세",
  waiting60: "MA3 돌파 대기",
};
const statusPriority = { signal: 0, rising: 1, waiting60: 2, setup: 3, signaled: 4, watching: 5, insufficient: 6, ineligible: 7, excluded: 8 };
const tierLabels = { core: "핵심 A-G", expanded: "확대 A-G" };
const viewLabels = { target: "조정회복형", reference: "상승조정형", positions: "보유종목", operations: "운영관리" };
const positionKey = item => item?.id || item?.code;

function chartGroup(item) {
  if (item.paperStrategy?.excludedReason) return "excluded";
  if (item.status === "insufficient") return "insufficient";
  const chart = item.displayCharts?.intraday;
  if (chart?.dataStatus !== "ok") return "insufficient";
  const last = chart.series?.filter((row) => row.complete !== false).at(-1);
  if (!last || !item.registeredAt || Date.parse(last.t) < Date.parse(item.registeredAt)
      || ![last.m20, last.m40].every(Number.isFinite)) return "insufficient";
  const rows = chart.series.filter(row => row.complete !== false && Date.parse(row.t) >= Date.parse(item.registeredAt));
  const recent = rows.slice(-4);
  if (recent.length === 4 && recent.every(row => row.m20 > row.m40)
      && recent.slice(1).every((row, i) => row.m40 > recent[i].m40)) return "reference";
  const crossed = rows.some((row, i) => i > 0 && rows[i - 1].m20 >= rows[i - 1].m40 && row.m20 < row.m40);
  if (crossed && last.m20 < last.m40) return "target";
  return "unclassified";
}
const formatter = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });

async function loadData() {
  if (elements.refreshButton.disabled) return;
  clearTimeout(exclusionPollTimer);
  elements.refreshButton.disabled = true;
  try {
    const stamp = Date.now();
    const [response, positionsResponse] = await Promise.all([
      fetch(`data/candidate-monitor.json?t=${stamp}`, { cache: "no-store" }),
      fetch(`data/positions.json?t=${stamp}`, { cache: "no-store" }),
    ]);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.payload = await response.json();
    applyPendingExclusions();
    state.positions = positionsResponse.ok ? await positionsResponse.json() : { positions: [] };
    state.positions.positions = [...(state.positions.positions || []), ...(state.payload.paperTrading?.positions || [])];
    state.lastLoadedAt = Date.now();
    const candidates = state.payload.candidates || [];
    if (state.selectedCode && !candidates.some((item) => item.code === state.selectedCode)) {
      state.selectedCode = null;
    }
    render();
    renderManualExclusions();
  } catch (error) {
    elements.runStatus.textContent = "감시 데이터를 불러오지 못했습니다";
    elements.updatedAt.textContent = String(error);
    elements.candidateList.innerHTML = '<div class="empty-list">잠시 후 다시 시도해 주세요.</div>';
  } finally {
    elements.refreshButton.disabled = false;
    if (state.pendingExclusions.size) exclusionPollTimer = setTimeout(loadData, 10000);
  }
}

function render() {
  restoreDetailPanel();
  const { summary = {}, validationSummary = {}, candidates = [], asOf, generatedAt } = state.payload;
  const activeCandidates = candidates;
  if (state.deepLinkPending) {
    const requested = candidates.find((item) => item.code === state.selectedCode);
    if (requested) {
      const group = chartGroup(requested);
      state.view = ["target", "reference"].includes(group) ? group : "operations";
    }
  }
  elements.activeCount.textContent = summary.active ?? activeCandidates.length;
  elements.setupCount.textContent = (summary.setup ?? 0) + (summary.waiting60 ?? 0);
  elements.signalCount.textContent = summary.signals ?? 0;
  elements.signaledCount.textContent = summary.signalHistory ?? 0;
  elements.risingCount.textContent = summary.rising ?? 0;
  const openPositions = (state.positions?.positions || []).filter((item) => item.status === "open");
  if (!openPositions.some((item) => positionKey(item) === state.selectedPositionCode)) {
    state.selectedPositionCode = positionKey(openPositions[0]) || null;
  }
  elements.positionCount.textContent = openPositions.length;
  elements.detectedCount.textContent = validationSummary.totalDetected ?? candidates.length;
  elements.targetRate.textContent = `${formatter.format(validationSummary.reached5PctRate || 0)}%`;
  elements.asOf.textContent = asOf || "-";
  elements.runStatus.textContent = state.pendingExclusions.size ? "제외·복원 요청 접수 · 서버 반영 확인 중" : state.exclusionMessage || (summary.dataErrors ? `분봉 오류 ${summary.dataErrors}건` : "클라우드 감시 정상");
  elements.updatedAt.textContent = generatedAt ? `마지막 갱신 ${formatDateTime(generatedAt)}` : "갱신 기록 없음";
  elements.pushState.textContent = summary.pushConfigured ? "휴대폰 푸시 연결" : "푸시 연결 대기";
  elements.pushState.className = `status-chip${summary.pushConfigured ? "" : " rising"}`;
  renderViewCounts(activeCandidates, openPositions);
  const candidateView = ["target", "reference"].includes(state.view);
  document.querySelector(".workspace").classList.toggle("hidden", !candidateView);
  document.querySelector(".toolbar").classList.toggle("hidden", !candidateView);
  document.querySelector(".positions-panel").classList.toggle("hidden", state.view !== "positions");
  document.querySelector("#operationsPanel").classList.toggle("hidden", state.view !== "operations");
  document.querySelector("#viewTitle").textContent = viewLabels[state.view];
  renderOperations(activeCandidates);
  if (candidateView) renderCandidates(activeCandidates);
  const closedPositions = (state.positions?.positions || []).filter((item) => item.status === "closed");
  renderPositions(openPositions, closedPositions);
  placeDetailPanel();
  renderSelectedDetail();
  if (state.deepLinkPending && state.selectedCode && candidateView) {
    state.deepLinkPending = false;
    requestAnimationFrame(() => elements.detailPanel.scrollIntoView({ behavior: "smooth", block: "start" }));
  }
}

function selectedDetailItem() {
  if (state.view !== "positions") return state.payload?.candidates?.find((item) => item.code === state.selectedCode);
  const position = state.positions?.positions?.find((item) => item.status === "open" && positionKey(item) === state.selectedPositionCode);
  if (!position) return null;
  const candidate = [...(state.payload?.candidates || []), ...(state.payload?.history || [])].find((item) => item.code === position.code);
  const quote = displayQuote(position);
  return { ...(candidate || {}), ...position,
    lastPrice: quote.price, lastPriceTime: quote.time,
    returnPct: quote.price && position.buyPrice ? 100 * (quote.price / position.buyPrice - 1) : position.returnPct,
    displayCharts: state.payload?.holdingCharts?.[position.code]?.displayCharts || candidate?.displayCharts,
    positionDetail: true };
}

function renderSelectedDetail() {
  renderDetail(selectedDetailItem());
}

function renderViewCounts(candidates, positions) {
  document.querySelectorAll(".view-tab").forEach((button) => {
    const view = button.dataset.view;
    const count = view === "positions" ? positions.length : candidates.filter((item) => chartGroup(item) === view).length;
    button.textContent = `${viewLabels[view]}${view === "operations" ? "" : ` (${count})`}`;
    button.classList.toggle("active", state.view === view);
    button.setAttribute("aria-selected", String(state.view === view));
  });
}

function renderOperations(candidates) {
  let examples = document.querySelector("#paperExamples");
  if (!examples) {
    examples = document.createElement("section");
    examples.id = "paperExamples";
    document.querySelector("#operationsPanel").append(examples);
  }
  examples.replaceChildren();
  const title = document.createElement("h3");
  title.textContent = "가상매매 · 과거 검토 사례";
  examples.append(title);
  for (const event of state.payload.paperTrading?.historicalExamples || []) {
    const button = document.createElement("button");
    button.type = "button";
    const candidate = state.payload.candidates.find(c => c.code === event.code);
    const beforeRegistration = !candidate?.registeredAt || Date.parse(event.time) < Date.parse(candidate.registeredAt);
    button.textContent = `${event.name} · ${event.type === "recovery" ? "조정회복형" : "상승조정형"} · ${formatDateTime(event.time)} · ${formatter.format(event.price)}원 · 저점 ${formatter.format(event.low)}원${beforeRegistration ? " · 등록 전 형태참고(매수신호 아님)" : " · 과거 검토"}`;
    button.addEventListener("click", () => {
      state.selectedCode = event.code;
      const item = state.payload.candidates.find(c => c.code === event.code);
      if (!item) return;
      examples.append(elements.detailPanel);
      elements.detailPanel.classList.remove("hidden");
      renderSelectedDetail();
      const series = chartViewport.series;
      const index = series.findIndex(row => row.t === event.time);
      chartViewport.count = Math.min(series.length, 50);
      chartViewport.start = Math.max(0, index - 35);
      clampChartWindow();
      redrawChartWindow();
      elements.detailPanel.scrollIntoView({block: "start"});
    });
    examples.append(button);
  }
  const insufficient = candidates.filter((item) => chartGroup(item) === "insufficient");
  const unclassified = candidates.filter((item) => chartGroup(item) === "unclassified");
  document.querySelector("#unclassifiedMeta").textContent = `자료부족 ${insufficient.length}종목 · 배열 확인 ${unclassified.length}종목`;
  const list = document.querySelector("#dataReviewList");
  list.replaceChildren();
  for (const item of [...insufficient, ...unclassified]) {
    const row = document.createElement("li");
    row.textContent = `${item.name} (${item.code}) · ${chartGroup(item) === "insufficient" ? (item.status === "insufficient" ? "A-G 기준자료 부족" : "차트 자료 부족·갱신 대기") : "정배열·조정 분류 확인"}`;
    list.append(row);
  }
  const select = document.querySelector("#operationStock");
  const prior = select.value;
  select.replaceChildren();
  for (const item of candidates) {
    const option = document.createElement("option");
    option.value = item.code;
    option.textContent = `${item.name} (${item.code})`;
    select.append(option);
  }
  if (candidates.some((item) => item.code === prior)) select.value = prior;
  document.querySelector("#operationExclude").disabled = !candidates.length;
}

function displayQuote(item) {
  const candidate = state.payload?.candidates?.find((entry) => entry.code === item.code) || item;
  const intraday = candidate.intraday || {};
  const charts = state.payload?.holdingCharts?.[item.code]?.displayCharts || candidate.displayCharts || {};
  const last = charts.intraday?.series?.at(-1);
  const samples = [
    { price: intraday.quotePrice, time: intraday.quoteTime },
    { price: item.lastPrice, time: item.lastPriceTime },
    { price: last?.c, time: last?.t },
    { price: intraday.lastPrice, time: intraday.lastBarTime },
  ].filter((quote) => Number.isFinite(quote.price) && quote.price > 0 && quote.time)
    .sort((a, b) => String(b.time).localeCompare(String(a.time)));
  const quote = samples[0] || { price: item.lastPrice || intraday.lastPrice, time: null };
  const date = quote.time?.slice(0, 10);
  const daily = charts.daily?.series?.length ? charts.daily.series : candidate.dailyChart?.series || [];
  const previous = daily.filter((row) => date && row.d < date).at(-1)?.c;
  const baseline = previous ?? (date === intraday.quoteTime?.slice(0, 10) ? intraday.quotePreviousClose : null);
  const change = baseline ? 100 * (quote.price / baseline - 1) : null;
  return { ...quote, change, direction: change == null ? "" : change > 0 ? "up" : change < 0 ? "down" : "flat" };
}

function quoteText(quote) {
  return quote.price ? `${formatter.format(quote.price)}원 (${quote.change == null ? "등락률 대기" : `${quote.change > 0 ? "+" : ""}${formatter.format(quote.change)}%`})` : "가격 대기";
}

function renderPositions(positions, closedPositions = []) {
  elements.positionList.innerHTML = "";
  if (!positions.length && !closedPositions.length) {
    elements.positionList.innerHTML = '<div class="empty-list">등록된 보유종목이 없습니다. “보유 등록·수정”에서 추가하세요.</div>';
    return;
  }
  if (positions.length) appendPositionGroupTitle("보유 중", `${positions.length}종목`);
  for (const item of positions) {
    const article = document.createElement("article");
    const quote = displayQuote(item);
    const returnPct = quote.price && item.buyPrice ? 100 * (quote.price / item.buyPrice - 1) : item.returnPct;
    const returnClass = returnPct == null ? "" : returnPct >= 0 ? "positive" : "negative";
    const warning = item.riskWarning
      ? "손절폭이 목표수익률보다 큽니다. 매수·비중 재검토"
      : item.trendWeak ? "30분봉 추세약화 감지" : "목표가·손절가 감시 중";
    article.className = "position-item";
    article.classList.toggle("selected", positionKey(item) === state.selectedPositionCode);
    article.dataset.code = item.code;
    article.innerHTML = `
      <header><button class="position-select" type="button"><strong></strong><span class="position-code"></span><span class="position-quote"></span></button></header>
      <div class="position-values"><span class="position-price"></span><span class="position-return ${returnClass}"></span></div>
      <div class="position-values"><span class="position-quantity"></span><span class="position-value"></span></div>
      <div class="position-values"><span class="position-stop"></span><span class="position-target"></span></div>
      <div class="position-warning"></div>
      <div class="position-item-actions"><button class="position-edit" type="button">수정</button><button class="position-close" type="button">매도완료</button></div>`;
    article.querySelector("strong").textContent = `${item.name} · ${item.mode === "virtual" ? "가상" : "실제"}`;
    if (item.mode === "virtual") {
      article.querySelector(".position-item-actions").hidden = true;
    }
    article.querySelector(".position-code").textContent = item.code;
    article.querySelector(".position-quote").textContent = quoteText(quote);
    article.querySelector(".position-select").dataset.direction = quote.direction;
    article.querySelector(".position-quote").title = quote.time ? `${formatDateTime(quote.time)} 수집 기준` : "수집 대기";
    const selectButton = article.querySelector(".position-select");
    selectButton.setAttribute("aria-pressed", String(positionKey(item) === state.selectedPositionCode));
    selectButton.addEventListener("click", () => {
      state.selectedPositionCode = positionKey(item);
      render();
      requestAnimationFrame(() => elements.detailPanel.scrollIntoView({ block: "start" }));
    });
    article.querySelector(".position-price").textContent = `매수 ${formatter.format(item.buyPrice)}원`;
    article.querySelector(".position-return").textContent = returnPct == null ? "-" : `보유수익 ${returnPct >= 0 ? "+" : ""}${formatter.format(returnPct)}%`;
    article.querySelector(".position-quantity").textContent = item.quantity ? `${formatter.format(item.quantity)}주` : "수량 미입력";
    const investedAmount = item.investedAmount ?? (item.quantity ? item.buyPrice * item.quantity : null);
    const currentValue = item.quantity && quote.price ? item.quantity * quote.price : null;
    article.querySelector(".position-value").textContent = currentValue == null
      ? `매수금액 ${investedAmount == null ? "-" : `${formatter.format(investedAmount)}원`}`
      : `평가금액 ${formatter.format(currentValue)}원`;
    const stopLabel = item.stopSource === "large_volume_previous_close"
      ? "대량거래 전일종가"
      : item.stopSource === "max_loss_pct" ? "최대 -5% 손절" : "손절";
    article.querySelector(".position-stop").textContent = `${stopLabel} ${formatter.format(item.stopPrice)}원`;
    article.querySelector(".position-target").textContent = `목표 ${formatter.format(item.targetPrice)}원`;
    article.querySelector(".position-warning").textContent = warning;
    article.querySelector(".position-edit").addEventListener("click", () => {
      const candidate = state.payload?.candidates?.find((entry) => entry.code === item.code) || null;
      openPositionDialog(candidate, item, "buy");
    });
    article.querySelector(".position-close").addEventListener("click", () => {
      const candidate = state.payload?.candidates?.find((entry) => entry.code === item.code) || null;
      openPositionDialog(candidate, item, "sold");
    });
    elements.positionList.append(article);
  }
  if (closedPositions.length) {
    const recentClosed = [...closedPositions]
      .sort((left, right) => String(right.closedAt || "").localeCompare(String(left.closedAt || "")))
      .slice(0, 20);
    appendPositionGroupTitle("매도 이력", `최근 ${recentClosed.length}건`);
    for (const item of recentClosed) renderClosedPosition(item);
  }
}

function appendPositionGroupTitle(title, meta) {
  const heading = document.createElement("div");
  heading.className = "position-group-title";
  heading.innerHTML = "<strong></strong><span></span>";
  heading.querySelector("strong").textContent = title;
  heading.querySelector("span").textContent = meta;
  elements.positionList.append(heading);
}

function renderClosedPosition(item) {
  const article = document.createElement("article");
  const returnPct = item.realizedReturnPct;
  const returnClass = returnPct == null ? "" : returnPct >= 0 ? "positive" : "negative";
  article.className = "position-item closed-position";
  article.innerHTML = `
    <header><strong></strong><span></span></header>
    <div class="position-values"><span class="position-price"></span><span class="position-return ${returnClass}"></span></div>
    <div class="position-values"><span class="position-quantity"></span><span class="position-profit"></span></div>
    <div class="position-values"><span class="position-cost-note">세금·수수료 전</span><span class="position-date"></span></div>
    <div class="position-item-actions"><button class="position-sell-edit" type="button"></button></div>`;
  article.querySelector("strong").textContent = `${item.name} · ${item.mode === "virtual" ? "가상" : "실제"}${item.exitReason === "ambiguous" ? " · 봉내 순서불명" : ""}`;
  article.querySelector("header span").textContent = item.code;
  const sellPrice = item.sellPrice == null ? "-" : `${formatter.format(item.sellPrice)}원`;
  article.querySelector(".position-price").textContent = `매수 ${formatter.format(item.buyPrice)}원 → 매도 ${sellPrice}`;
  article.querySelector(".position-return").textContent = returnPct == null ? "수익률 -" : `${returnPct >= 0 ? "+" : ""}${formatter.format(returnPct)}%`;
  article.querySelector(".position-quantity").textContent = item.quantity ? `${formatter.format(item.quantity)}주` : "수량 미입력";
  const profit = item.realizedProfitTotal;
  article.querySelector(".position-profit").textContent = profit == null ? "총손익 -" : `총손익 ${profit >= 0 ? "+" : ""}${formatter.format(profit)}원`;
  article.querySelector(".position-date").textContent = item.closedAt ? `매도 ${formatDateTime(item.closedAt)}` : "매도일 -";
  const sellEdit = article.querySelector(".position-sell-edit");
  sellEdit.textContent = item.sellPrice == null ? "매도가 입력" : "매도가 수정";
  sellEdit.addEventListener("click", () => openPositionDialog(null, item, "sold"));
  elements.positionList.append(article);
}

function filteredCandidates(candidates) {
  const keyword = state.keyword.trim().toLowerCase();
  return candidates.filter((item) => {
    const statusMatch = chartGroup(item) === state.view;
    const keywordMatch = !keyword || item.name.toLowerCase().includes(keyword) || item.code.includes(keyword);
    return statusMatch && keywordMatch;
  });
}

function renderCandidates(candidates) {
  restoreDetailPanel();
  const visible = filteredCandidates(candidates).sort((a, b) =>
    (statusPriority[a.status] ?? 99) - (statusPriority[b.status] ?? 99)
    || a.name.localeCompare(b.name, "ko")
  );
  elements.candidateMeta.textContent = `${visible.length}종목 · ${state.view === "target" ? "등록 이후 MA20 < MA40" : "등록 이후 MA20 > MA40"}`;
  elements.candidateList.innerHTML = "";
  if (!visible.length) {
    elements.candidateList.innerHTML = '<div class="empty-list">현재 조건에 해당하는 후보가 없습니다.</div>';
    return;
  }
  if (!visible.some((item) => item.code === state.selectedCode)) {
    state.selectedCode = visible[0].code;
  }
  for (const item of visible) {
    const node = elements.template.content.firstElementChild.cloneNode(true);
    const intraday = item.intraday || {};
    const rise = Math.min(intraday.riseCount || 0, 5);
    node.dataset.status = item.status;
    node.dataset.tier = item.candidateTier || "core";
    node.dataset.code = item.code;
    node.classList.toggle("selected", item.code === state.selectedCode);
    node.querySelector(".candidate-name").textContent = item.name;
    const quote = displayQuote(item);
    const quotePrice = quote.price;
    const quoteTime = quote.time;
    const direction = quote.direction;
    node.querySelector(".candidate-title").dataset.direction = direction;
    node.querySelector(".candidate-quote").textContent = quoteText(quote);
    node.querySelector(".candidate-quote").title = quote.time ? `${formatDateTime(quote.time)} 수집 기준` : "수집 대기";
    node.querySelector(".candidate-status").textContent = statusLabels[item.status] || "확인 필요";
    if (intraday.sessionRecovery?.matched) {
      node.querySelector(".candidate-status").textContent = "정배열 조정·회복";
      node.style.backgroundColor = "#fff8d4";
    }
    node.querySelector(".candidate-code").textContent = `${item.code} · ${item.market}`;
    const nativeChart = item.displayCharts?.intraday;
    const nativeLast = nativeChart?.series?.filter((row) => row.complete !== false).at(-1);
    const rebound = nativeChart?.referenceRebounds?.filter((event) => event.start.slice(0, 10) >= (item.registeredAt || item.dailySignalDate).slice(0, 10)).at(-1);
    if (nativeLast?.referencePullback) {
      node.querySelector(".candidate-status").textContent = "MA10 조정 관찰";
      node.style.backgroundColor = "#fff7d1";
    } else if (rebound && rebound.time.slice(0, 10) === nativeLast?.t.slice(0, 10)) {
      node.querySelector(".candidate-status").textContent = `밀착 후 재상승 ${formatDateTime(rebound.time)}`;
    }
    node.querySelector(".candidate-tier").textContent = tierLabels[item.candidateTier || "core"];
    node.querySelector(".candidate-progress i").style.width = `${(rise / 5) * 100}%`;
    node.querySelector(".candidate-rise").textContent = `${rise} / 5`;
    node.querySelector(".candidate-days").textContent = `A-G ${item.dailySignalDate} · ${item.tradingDaysRemaining}일 남음`;
    node.querySelector(".candidate-price").textContent = quoteTime ? `${quoteTime.slice(5, 10)} ${quoteTime.slice(11, 16)} ${intraday.quoteTime ? "수집가" : "완성봉"}` : "분봉 대기";
    const technicalStop = item.daily?.preSpikeClose;
    const maximumLossStop = intraday.lastPrice ? Math.round(intraday.lastPrice * 0.95) : null;
    const stopPrice = technicalStop && maximumLossStop ? Math.max(technicalStop, maximumLossStop) : technicalStop;
    const capped = technicalStop && maximumLossStop && technicalStop < maximumLossStop;
    node.querySelector(".candidate-stop").textContent = stopPrice
      ? `기본 손절 ${formatter.format(stopPrice)}원${capped ? " (-5% 제한)" : ""}`
      : "손절가 확인 필요";
    node.querySelector(".candidate-select").addEventListener("click", () => {
      state.selectedCode = item.code;
      const url = new URL(window.location.href);
      url.searchParams.set("stock", item.code);
      window.history.replaceState(null, "", url);
      render();
      requestAnimationFrame(() => elements.detailPanel.scrollIntoView({ block: "start" }));
    });
    node.querySelector(".candidate-register").addEventListener("click", () => {
      const position = state.positions?.positions?.find((entry) => entry.code === item.code && entry.status === "open") || null;
      openPositionDialog(item, position, "buy");
    });
    node.querySelector(".candidate-exclude").addEventListener("click", () => openExclusionDialog(item, "exclude"));
    elements.candidateList.append(node);
  }
}

function restoreDetailPanel() {
  if (elements.detailPanel.parentNode !== detailPanelHome) {
    detailPanelHome.insertBefore(elements.detailPanel, detailPanelAnchor.nextSibling);
  }
}

function placeDetailPanel() {
  if (state.view === "positions") {
    document.querySelector(".positions-panel").append(elements.detailPanel);
    return;
  }
  if (!window.matchMedia("(max-width: 850px)").matches) {
    restoreDetailPanel();
    return;
  }
  const selected = elements.candidateList.querySelector(`.candidate[data-code="${state.selectedCode}"]`);
  if (selected) selected.after(elements.detailPanel);
}

function applyPendingExclusions() {
  const serverExcluded = new Set((state.payload.manualExclusions || []).map((entry) => entry.code));
  for (const [code, entry] of state.pendingExclusions) {
    const confirmed = entry.action === "exclude" ? serverExcluded.has(code) : !serverExcluded.has(code);
    if (confirmed) {
      state.pendingExclusions.delete(code);
      state.exclusionMessage = "제외·복원 요청이 서버에 반영되었습니다.";
    } else if (Date.now() - entry.at > 600000) {
      state.pendingExclusions.delete(code);
      state.exclusionMessage = "서버 반영 확인이 지연되어 현재 서버 목록을 표시합니다. 수동 제외 목록을 확인해 주세요.";
    }
  }
  try { sessionStorage.setItem(PENDING_EXCLUSION_KEY, JSON.stringify([...state.pendingExclusions])); } catch {}
  if (!state.pendingExclusions.size) return;
  const hidden = new Set([...state.pendingExclusions].filter(([, entry]) => entry.action === "exclude").map(([code]) => code));
  state.payload.candidates = (state.payload.candidates || []).filter((entry) => !hidden.has(entry.code));
  const candidates = state.payload.candidates;
  const summary = state.payload.summary ||= {};
  summary.active = candidates.filter((entry) => !["excluded", "ineligible"].includes(entry.status)).length;
  for (const [key, status] of Object.entries({signals: "signal", signalHistory: "signaled", setup: "setup", rising: "rising", waiting60: "waiting60"})) {
    summary[key] = candidates.filter((entry) => entry.status === status).length;
  }
  if (hidden.has(state.selectedCode)) state.selectedCode = null;
}

function renderManualExclusions() {
  const entries = state.payload.manualExclusions || [];
  document.querySelector("#manualExclusionsButton").textContent = `수동 제외 (${entries.length})`;
  const list = document.querySelector("#excludedList");
  list.replaceChildren();
  if (!entries.length) list.textContent = "수동 제외한 종목이 없습니다.";
  for (const entry of entries) {
    const row = document.createElement("div");
    row.className = "manual-exclusion-row";
    const label = document.createElement("span");
    label.textContent = `${entry.name} (${entry.code}) · ${entry.excludedAt?.slice(0, 10) || ""} · ${entry.reason || "차트 형태 부적합"}`;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "복원";
    button.addEventListener("click", () => {
      document.querySelector("#excludedListDialog").close();
      openExclusionDialog(entry, "restore");
    });
    row.append(label, button);
    list.append(row);
  }
}

function openExclusionDialog(item, action) {
  document.querySelector("#exclusionTitle").textContent = `${item.name} · ${action === "exclude" ? "감시 제외" : "감시 복원"}`;
  document.querySelector("#exclusionCode").value = item.code;
  document.querySelector("#exclusionAction").value = action;
  document.querySelector("#exclusionReason").value = item.reason || "차트 형태 부적합";
  document.querySelector("#exclusionReason").disabled = action === "restore";
  document.querySelector("#exclusionToken").value = storedGithubToken();
  document.querySelector("#exclusionStatus").textContent = action === "exclude" ? "분석·알림·달성률 계산에서 제외됩니다. 보유 기록은 유지됩니다." : "후보 기간이 남아 있으면 다시 감시합니다.";
  document.querySelector("#exclusionSubmit").textContent = action === "exclude" ? "제외" : "복원";
  document.querySelector("#exclusionDialog").showModal();
}

document.querySelector("#manualExclusionsButton").addEventListener("click", () => document.querySelector("#excludedListDialog").showModal());
document.querySelector("#excludedListClose").addEventListener("click", () => document.querySelector("#excludedListDialog").close());
document.querySelector("#exclusionCancel").addEventListener("click", () => document.querySelector("#exclusionDialog").close());
document.querySelector("#exclusionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#exclusionSubmit");
  const status = document.querySelector("#exclusionStatus");
  const token = document.querySelector("#exclusionToken").value.trim();
  if (!token) { status.textContent = "GitHub 연결키를 입력해 주세요."; return; }
  button.disabled = true;
  const action = document.querySelector("#exclusionAction").value;
  const code = document.querySelector("#exclusionCode").value;
  const reason = document.querySelector("#exclusionReason").value;
  try {
    const response = await fetch(WORKFLOW_DISPATCH_URL, {
      method: "POST",
      headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ref: "main", inputs: { action, code, reason }}),
    });
    if (!response.ok) throw new Error(`요청 실패 (${response.status}) · 연결키와 권한을 확인해 주세요.`);
    try { localStorage.setItem(GITHUB_TOKEN_KEY, token); } catch {}
    state.pendingExclusions.set(code, {action, at: Date.now()});
    state.exclusionMessage = "";
    applyPendingExclusions();
    document.querySelector("#exclusionDialog").close();
    render();
    renderManualExclusions();
    await loadData();
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});

function storedGithubToken() {
  try { return localStorage.getItem(GITHUB_TOKEN_KEY) || ""; }
  catch { return ""; }
}

function updateConnectionState() {
  const connected = Boolean(elements.githubToken.value.trim());
  elements.connectionState.textContent = connected ? "연결키 저장됨" : "연결 필요";
  elements.connectionState.classList.toggle("connected", connected);
  if (!connected) elements.githubConnection.open = true;
}

function setPositionMode(action) {
  const isBuy = action === "buy";
  document.querySelectorAll(".buy-field").forEach((field) => field.classList.toggle("hidden", !isBuy));
  document.querySelectorAll(".sell-field").forEach((field) => field.classList.toggle("hidden", isBuy));
  elements.positionName.required = isBuy;
  elements.positionName.readOnly = !isBuy;
  elements.positionBuyPrice.required = isBuy;
  elements.positionBuyPrice.readOnly = !isBuy;
  elements.positionQuantity.required = true;
  elements.positionSellPrice.required = !isBuy;
  elements.positionSubmitButton.textContent = isBuy ? "등록" : "매도 완료";
  elements.positionFormSubtitle.textContent = isBuy ? "매수정보와 손절가" : "매도 체결가와 실현손익 기록";
}

function updateStopPolicy() {
  const buyPrice = Number(elements.positionBuyPrice.value);
  const technicalStop = Number(elements.positionStopPrice.dataset.technicalStopPrice || elements.positionStopPrice.value);
  if (!buyPrice) {
    elements.stopPolicyNote.textContent = "매수가 입력 후 기술적 손절가와 -5% 가격 중 높은 가격을 적용합니다.";
    return;
  }
  const maximumLossStop = Math.round(buyPrice * (1 - MAX_STOP_PCT / 100));
  if (technicalStop > 0 && technicalStop < buyPrice) {
    const effectiveStop = Math.max(technicalStop, maximumLossStop);
    elements.positionStopPrice.value = effectiveStop;
    elements.stopPolicyNote.textContent = technicalStop < maximumLossStop
      ? `기술적 손절 ${formatter.format(technicalStop)}원 → 최대 -5% ${formatter.format(effectiveStop)}원 적용 · 15:00 이후 확정`
      : `기술적 손절 ${formatter.format(effectiveStop)}원 적용 · 15:00 이후 확정`;
    return;
  }
  elements.positionStopPrice.value = maximumLossStop;
  elements.stopPolicyNote.textContent = `기술적 손절가 없음 → 최대 -5% ${formatter.format(maximumLossStop)}원 적용 · 15:00 이후 확정`;
}

function openPositionDialog(candidate = null, position = null, action = "buy") {
  elements.positionForm.reset();
  elements.positionCode.value = "";
  elements.positionName.value = "";
  elements.positionBuyPrice.value = "";
  elements.positionQuantity.value = "";
  elements.positionSellPrice.value = "";
  elements.positionStopPrice.value = "";
  delete elements.positionStopPrice.dataset.technicalStopPrice;
  elements.positionTargetPct.value = "5";
  elements.githubToken.value = storedGithubToken();
  elements.positionFormStatus.textContent = "";
  elements.positionFormStatus.className = "form-status";
  const actionRadio = elements.positionForm.querySelector(`input[name="positionAction"][value="${action}"]`);
  actionRadio.checked = true;
  setPositionMode(action);
  if (candidate) {
    elements.positionCode.value = candidate.code || "";
    elements.positionName.value = candidate.name || "";
    elements.positionStopPrice.value = candidate.daily?.preSpikeClose || "";
    elements.positionStopPrice.dataset.technicalStopPrice = candidate.daily?.preSpikeClose || "";
    elements.positionFormSubtitle.textContent = `${candidate.name} · ${candidate.code}`;
  }
  if (position) {
    elements.positionCode.value = position.code || elements.positionCode.value;
    elements.positionName.value = position.name || elements.positionName.value;
    elements.positionBuyPrice.value = position.buyPrice || "";
    elements.positionQuantity.value = position.quantity || "";
    elements.positionStopPrice.value = position.stopPrice || elements.positionStopPrice.value;
    elements.positionStopPrice.dataset.technicalStopPrice = position.technicalStopPrice || position.stopPrice || "";
    elements.positionTargetPct.value = position.targetPct || "5";
    elements.positionSellPrice.value = position.sellPrice || "";
    elements.positionFormSubtitle.textContent = `${position.name} · ${position.code}`;
  }
  if (action === "buy") updateStopPolicy();
  updateConnectionState();
  elements.positionDialog.showModal();
  (action === "sold" && position ? elements.positionSellPrice : action === "buy" && (candidate || position) ? elements.positionBuyPrice : elements.positionCode).focus();
}

function closePositionDialog() {
  if (elements.positionDialog.open) elements.positionDialog.close();
}

async function submitPosition(event) {
  event.preventDefault();
  const action = new FormData(elements.positionForm).get("positionAction");
  if (action === "buy") updateStopPolicy();
  const token = elements.githubToken.value.trim();
  if (!token) {
    elements.githubConnection.open = true;
    elements.positionFormStatus.textContent = "GitHub 연결키를 입력해 주세요.";
    elements.positionFormStatus.className = "form-status error";
    elements.githubToken.focus();
    return;
  }
  try { localStorage.setItem(GITHUB_TOKEN_KEY, token); }
  catch { /* The request can still proceed for this session. */ }
  updateConnectionState();
  elements.positionSubmitButton.disabled = true;
  elements.positionFormStatus.textContent = "등록 요청 중...";
  elements.positionFormStatus.className = "form-status";
  const inputs = {
    action,
    code: elements.positionCode.value.trim(),
    name: action === "buy" ? elements.positionName.value.trim() : "",
    buy_price: action === "buy" ? elements.positionBuyPrice.value.trim() : "",
    quantity: elements.positionQuantity.value.trim(),
    sell_price: action === "sold" ? elements.positionSellPrice.value.trim() : "",
    stop_price: action === "buy" ? elements.positionStopPrice.value.trim() : "",
    target_pct: action === "buy" ? elements.positionTargetPct.value.trim() || "5" : "5",
  };
  try {
    const response = await fetch(WORKFLOW_DISPATCH_URL, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main", inputs }),
    });
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) throw new Error("연결키 권한을 확인해 주세요.");
      throw new Error(`등록 요청 실패 (${response.status})`);
    }
    elements.positionFormStatus.textContent = "등록 요청이 완료되었습니다. 잠시 후 현황에 반영됩니다.";
    elements.positionFormStatus.className = "form-status success";
    setTimeout(() => { closePositionDialog(); loadData(); }, 1800);
  } catch (error) {
    elements.positionFormStatus.textContent = error.message || String(error);
    elements.positionFormStatus.className = "form-status error";
  } finally {
    elements.positionSubmitButton.disabled = false;
  }
}

function renderDetail(item) {
  if (!item) {
    elements.detailName.textContent = "종목을 선택하세요";
    elements.detailMeta.textContent = "후보를 누르면 MA20·MA40 흐름을 확인할 수 있습니다.";
    elements.emptyDetail.classList.remove("hidden");
    elements.detailContent.classList.add("hidden");
    elements.naverLink.classList.add("hidden");
    return;
  }
  const intraday = item.intraday || {};
  elements.emptyDetail.classList.add("hidden");
  elements.detailContent.classList.remove("hidden");
  elements.naverLink.classList.remove("hidden");
  elements.naverLink.href = item.naverUrl;
  elements.detailName.textContent = item.name;
  elements.detailMeta.textContent = item.positionDetail
    ? `${item.code} · 매수 ${formatter.format(item.buyPrice)}원 · ${formatter.format(item.quantity)}주 · 손절 ${formatter.format(item.stopPrice)}원`
    : `${item.code} · ${item.market} · ${tierLabels[item.candidateTier || "core"]} · ${statusLabels[item.status] || "확인 필요"}`;
  elements.detailBadge.textContent = item.positionDetail ? "보유 관리" : statusLabels[item.status] || "확인 필요";
  elements.detailBadge.className = `status-chip ${item.status}`;
  elements.detailProgress.textContent = `${Math.min(intraday.riseCount || 0, 5)} / 5`;
  elements.dailySignal.textContent = item.dailySignalDate || "-";
  elements.remainingDays.textContent = item.positionDetail ? "보유 중 계속 감시" : `${item.tradingDaysRemaining}거래일`;
  elements.signalMessage.className = `signal-message${item.status === "signal" ? " signal" : ""}`;
  elements.signalMessage.textContent = item.positionDetail
    ? `매수 ${formatter.format(item.buyPrice)}원 · 목표 ${formatter.format(item.targetPrice)}원 · 손절 ${formatter.format(item.stopPrice)}원 · ${item.returnPct == null ? "수익률 대기" : `현재 ${item.returnPct > 0 ? "+" : ""}${formatter.format(item.returnPct)}%`}`
    : signalCopy(item, intraday);
  const recovery = intraday.sessionRecovery;
  if (recovery?.matched && !item.positionDetail) {
    elements.detailBadge.textContent = "정배열 조정·회복 관찰";
    elements.detailBadge.style.backgroundColor = "#fff0a3";
    elements.signalMessage.textContent = `15:00 기준 정배열 조정·회복 관찰 · ${formatter.format(recovery.price)}원 · 전일 대비 ${recovery.changePct}% / 시가 대비 ${recovery.openChangePct}%`;
  } else {
    elements.detailBadge.style.backgroundColor = "";
  }
  const native = item.displayCharts?.intraday;
  chartViewport.registeredAt = item.registeredAt || null;
  if (chartViewport.code !== item.code) {
    chartViewport.code = item.code;
    chartViewport.start = 0;
    chartViewport.count = null;
    chartViewport.days = 5;
    chartPointers.clear();
  }
  const daily = item.displayCharts?.daily;
  const rebound = native?.referenceRebounds?.filter((event) => event.start.slice(0, 10) >= (item.registeredAt || item.dailySignalDate || "").slice(0, 10)).at(-1);
  const nativeLast = native?.series?.filter((row) => row.complete !== false).at(-1);
  if (!item.positionDetail && nativeLast?.referencePullback) {
    elements.signalMessage.textContent = "MA10 하향 조정 이후 MA3 → MA20 상향 돌파를 기다립니다. MA20·MA40 밀착과 MA40·MA60 상승을 유지하는지 관찰합니다.";
  } else if (!item.positionDetail && rebound) {
    elements.signalMessage.textContent = `밀착 후 재상승 · ${formatDateTime(rebound.time)} · 포착가격 ${formatter.format(rebound.price)}원 · MA10 조정 후 MA3 → MA20 상향 돌파 (별도 관찰 신호)`;
  }
  const fullSeries = native?.series || [];
  if (chartViewport.count == null && fullSeries.length) {
    const dates = [...new Set(fullSeries.map(row => row.t.slice(0, 10)))].slice(-5);
    chartViewport.start = fullSeries.findIndex(row => dates.includes(row.t.slice(0, 10)));
    chartViewport.count = fullSeries.length - chartViewport.start;
  }
  drawChart(fullSeries, null, item.sessionRecoveryHistory || {});
  if (item.paperStrategy && !item.positionDetail) {
    elements.signalMessage.textContent = item.paperStrategy.excludedReason || (item.paperStrategy.group === "target"
      ? "가상검증 · MA20 기준선 회복 후 다음 완성봉 유지·정배열 확인"
      : "가상검증 · MA10 눌림 이후 MA3·MA10 모두 MA20 회복·정배열 확인");
  }
  const dailySeries = daily?.series || [];
  const latest = native?.series?.at(-1);
  const incompleteWarmup = native?.series?.some((row) => row.m60 == null);
  document.querySelector("#chartSource").textContent = `네이버 KRX 원본 · ${latest ? formatDateTime(latest.t) : "자료 대기"}${native?.dataStatus === "stale" ? " · 갱신 지연" : ""}${incompleteWarmup ? " · 초기 MA60 자료 부족" : ""} · 가상판정 동일 원본`;
  const latestDailyDate = dailySeries.at(-1)?.d;
  elements.dailyChartMeta.textContent = `${latestDailyDate || "일봉 대기"} · 네이버 일봉 · MA5 · MA10 · MA20 · MA60${daily?.dataStatus === "stale" ? " · 갱신 지연" : ""}`;
  drawDailyChart(dailySeries);
}

function signalCopy(item, intraday) {
  if (item.status === "signal" || item.status === "signaled") {
    const lead = item.status === "signal" ? "조건이 방금 확정됐습니다." : "과거 감시 중 조건이 확정된 이력입니다.";
    const estimate = intraday.baselineInferred ? " 초기 하향교차 시점은 네이버 과거 데이터 범위로 추정했습니다." : "";
    return `${formatDateTime(intraday.signalTime)} 완성봉에서 MA3가 ${intraday.signalTarget || intraday.entryTarget}를 돌파해 ${lead} 신호 직후 다음 30분봉부터 HTS 현재가와 거래량을 확인하는 조건입니다.${estimate}`;
  }
  if (item.status === "rising") {
    return `MA20이 MA40 아래에서 반등해 ${intraday.riseCount || 0}회 연속 상승 중입니다. 5회가 완성될 때까지 관찰합니다.`;
  }
  if (item.status === "setup") return "A-G 확정 후 MA20이 MA40 아래로 내려왔습니다. 반등이 시작되어 1/5가 되는지 관찰하는 매수 준비 단계입니다.";
  if (item.status === "waiting60") return `MA20 5회 상승이 확인됐습니다. 조정 깊이에 따라 MA3가 ${intraday.entryTarget || "목표 이동평균선"}을 돌파한 완성봉까지 기다립니다.`;
  if (item.status === "excluded") return "MA20이 MA40·MA60 아래에서 3개 완료봉 이상 연속 하락한 구조적 약세 상태입니다. 후보 기간 중 반등 조건이 생기면 다시 판정합니다.";
  if (item.status === "insufficient") return "A-G 발생일의 30분봉 MA20·MA40 기준값과 후속 교차를 현재 네이버 제공 범위에서 확인할 수 없습니다. 신규 후보부터 기준값을 자동 저장합니다.";
  if (item.status === "ineligible") return "A-G 발생일 마감 시 MA20이 MA40 위에 있지 않아 30분봉 후속 감시에서 제외했습니다.";
  if (intraday.dataStatus === "error") return "네이버 분봉을 가져오지 못했습니다. 다음 예약 실행에서 다시 시도합니다.";
  return "MA20의 5회 상승을 관찰합니다. MA20이 MA40까지만 조정되면 MA3→MA40, MA60 아래까지 조정되면 MA3→MA60 돌파를 기다립니다. 일봉 MA10은 참고선입니다.";
}

const chartViewport = { code: null, start: 0, count: null, days: 5, series: [], dailyMa10: null, recoveryHistory: {} };
const chartPointers = new Map();
let chartGesture = null;

function clampChartWindow() {
  const total = chartViewport.series.length;
  if (chartViewport.count == null) return;
  chartViewport.count = Math.min(total, Math.max(Math.min(2, total), Math.round(chartViewport.count)));
  chartViewport.start = Math.max(0, Math.min(total - chartViewport.count, Math.round(chartViewport.start)));
}

function redrawChartWindow() {
  drawChart(chartViewport.series, chartViewport.dailyMa10, chartViewport.recoveryHistory);
}

function chooseChartDays(days) {
  const series = chartViewport.series;
  const dates = [...new Set(series.map((row) => row.t.slice(0, 10)))];
  const completedDates = dates.slice(0, -1).slice(-days);
  const count = completedDates.length
    ? series.filter((row) => completedDates.includes(row.t.slice(0, 10))).length
    : series.length;
  const lastFive = dates.slice(-5);
  chartViewport.count = days === 5 ? series.filter(row => lastFive.includes(row.t.slice(0, 10))).length : Math.min(series.length, count);
  chartViewport.start = series.length - chartViewport.count;
  chartViewport.days = days;
  redrawChartWindow();
}

function chartPointerFraction(clientX) {
  const rect = elements.chart.getBoundingClientRect();
  return Math.max(0, Math.min(1, (clientX - rect.left - 45) / Math.max(1, rect.width - 48)));
}

function zoomChart(factor, clientX) {
  const count = chartViewport.count ?? chartViewport.series.length;
  const fraction = chartPointerFraction(clientX);
  const anchor = chartViewport.start + count * fraction;
  chartViewport.count = count * factor;
  chartViewport.days = null;
  clampChartWindow();
  chartViewport.start = anchor - chartViewport.count * fraction;
  clampChartWindow();
  redrawChartWindow();
}

document.querySelectorAll('[data-chart-days]').forEach((button) => {
  button.addEventListener('click', () => chooseChartDays(Number(button.dataset.chartDays)));
});
document.querySelector('#chartReset').addEventListener('click', () => chooseChartDays(5));
elements.chart.addEventListener('wheel', (event) => {
  if (chartViewport.series.length < 2) return;
  event.preventDefault();
  zoomChart(event.deltaY < 0 ? 0.8 : 1.25, event.clientX);
}, { passive: false });

function beginChartGesture() {
  const points = [...chartPointers.values()];
  if (!points.length) { chartGesture = null; return; }
  const midpoint = points.reduce((sum, point) => sum + point.x, 0) / points.length;
  const count = chartViewport.count ?? chartViewport.series.length;
  chartGesture = { x: midpoint, count, start: chartViewport.start,
    distance: points.length === 2 ? Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y) : null,
    anchor: chartViewport.start + count * chartPointerFraction(midpoint) };
}

elements.chart.addEventListener('pointerdown', (event) => {
  if (event.pointerType === 'mouse' && event.button !== 0) return;
  if (chartPointers.size >= 2) return;
  chartPointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  elements.chart.setPointerCapture(event.pointerId);
  beginChartGesture();
});
elements.chart.addEventListener('pointermove', (event) => {
  if (!chartPointers.has(event.pointerId) || !chartGesture) return;
  chartPointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  const points = [...chartPointers.values()];
  const midpoint = points.reduce((sum, point) => sum + point.x, 0) / points.length;
  chartViewport.days = null;
  if (points.length === 2 && chartGesture.distance > 0) {
    const distance = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
    chartViewport.count = chartGesture.count * chartGesture.distance / Math.max(1, distance);
    clampChartWindow();
    chartViewport.start = chartGesture.anchor - chartViewport.count * chartPointerFraction(midpoint);
  } else {
    chartViewport.count = chartGesture.count;
    chartViewport.start = chartGesture.start - (midpoint - chartGesture.x) * chartGesture.count / Math.max(1, elements.chart.clientWidth - 48);
  }
  clampChartWindow();
  redrawChartWindow();
});
for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
  elements.chart.addEventListener(type, (event) => {
    chartPointers.delete(event.pointerId);
    beginChartGesture();
  });
}

function registrationPhases(series, registeredAt) {
  const start = Date.parse(registeredAt);
  let previous = null, phase = "before", deep = false, recovery = false, reference = false;
  return series.map(original => {
    const row = {...original, phase: "before", referencePullback: false};
    if (!Number.isFinite(start) || Date.parse(row.t) < start) return row;
    if (row.complete === false) { row.phase = phase; row.referencePullback = reference; return row; }
    if (previous) {
      if (previous.m20 >= previous.m40 && row.m20 < row.m40) { recovery = true; phase = "pullback"; deep = false; }
      if (recovery) {
        deep ||= row.m20 < row.m60;
        const target = deep ? "m60" : "m40";
        if (previous.m3 <= previous[target] && row.m3 > row[target]) phase = "fast";
        if (previous.m20 <= previous[target] && row.m20 > row[target]) { phase = "confirmed"; recovery = false; }
      }
      if (row.m20 > row.m40 && row.m40 > previous.m40 && previous.m10 >= previous.m20 && row.m10 < row.m20) reference = true;
      if (reference && (row.m20 <= row.m40 || row.m40 <= previous.m40 || (row.m3 > row.m20 && row.m10 > row.m20))) reference = false;
    }
    row.phase = phase;
    row.referencePullback = reference;
    previous = row;
    return row;
  });
}

function drawChart(series, dailyMa10, recoveryHistory = {}) {
  hideMaTooltip();
  maHover.pad = null;
  chartViewport.series = series;
  chartViewport.dailyMa10 = dailyMa10;
  chartViewport.recoveryHistory = recoveryHistory;
  series = registrationPhases(series, chartViewport.registeredAt);
  clampChartWindow();
  document.querySelectorAll('[data-chart-days]').forEach((button) => {
    button.setAttribute('aria-pressed', String(Number(button.dataset.chartDays) === chartViewport.days));
  });
  if (chartViewport.count != null) series = series.slice(chartViewport.start, chartViewport.start + chartViewport.count);
  const canvas = elements.chart;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 800;
  const height = canvas.clientHeight || 330;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width, height);
  if (series.length < 2) {
    ctx.fillStyle = "#64746c";
    ctx.font = '13px "Malgun Gothic"';
    ctx.fillText("표시할 30분봉 데이터가 부족합니다.", 20, 35);
    return;
  }
  const values = series.flatMap((row) => [row.h, row.l, row.c, row.m3, row.m20, row.m40, row.m60, dailyMa10]).filter((value) => value != null);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const rawSpread = Math.max(max - min, 1);
  const priceMin = min - rawSpread * 0.04;
  const priceMax = max + rawSpread * 0.04;
  const spread = priceMax - priceMin;
  const mobile = window.innerWidth <= 540;
  ctx.font = mobile ? "10px Segoe UI" : "11px Segoe UI";
  const priceLabel = (value) => mobile ? formatter.format(Math.round(value)) : formatter.format(value);
  const labelWidth = Math.max(...[priceMin, priceMax].map((value) => ctx.measureText(priceLabel(value)).width));
  const pad = { left: mobile ? Math.ceil(labelWidth) + 5 : 54, right: mobile ? 3 : 12, top: 42, bottom: 28 };
  const volumeHeight = Math.max(44, Math.round(height * 0.2));
  const volumeTop = height - pad.bottom - volumeHeight;
  const priceBottom = volumeTop - 10;
  const plotWidth = width - pad.left - pad.right;
  const slot = plotWidth / series.length;
  Object.assign(maHover, { series, pad, slot });
  const x = (index) => pad.left + slot * (index + 0.5);
  const phaseColors = { before: "#e9edf0", pullback: "#fff7d1", fast: "#eef8d6", confirmed: "#dff2e7" };
  series.forEach((row, index) => {
    ctx.fillStyle = row.referencePullback ? "#fff7d1" : phaseColors[row.phase] || phaseColors.before;
    ctx.fillRect(pad.left + slot * index, pad.top, slot + 0.5, height - pad.top - pad.bottom);
    if (Date.parse(row.t) >= Date.parse(chartViewport.registeredAt) && recoveryHistory[row.t.slice(0, 10)]?.matched) {
      ctx.fillStyle = "#e8c748";
      ctx.fillRect(pad.left + slot * index, volumeTop - 4, slot + 0.5, 3);
    }
  });
  const y = (value) => pad.top + ((priceMax - value) / spread) * (priceBottom - pad.top);
  ctx.strokeStyle = "#e2e8e4";
  ctx.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const yy = pad.top + (step / 4) * (priceBottom - pad.top);
    ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke();
    ctx.fillStyle = "#64746c"; ctx.font = mobile ? "10px Segoe UI" : "11px Segoe UI";
    const label = priceLabel(priceMax - (spread * step) / 4);
    ctx.fillText(label, mobile ? pad.left - ctx.measureText(label).width - 4 : 3, yy + 4);
  }
  const maxVolume = Math.max(...series.map((row) => Number(row.v) || 0), 1);
  const candleWidth = Math.max(2, Math.min(8, slot * 0.66));
  series.forEach((row, index) => {
    const open = Number(row.o);
    const high = Number(row.h);
    const low = Number(row.l);
    const close = Number(row.c);
    const hasCandle = [open, high, low, close].every(Number.isFinite);
    const rising = !hasCandle || close >= open;
    const color = rising ? "#e5484d" : "#316fee";
    const center = x(index);
    if (hasCandle) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(center, y(high)); ctx.lineTo(center, y(low)); ctx.stroke();
      const bodyTop = Math.min(y(open), y(close));
      const bodyHeight = Math.max(Math.abs(y(open) - y(close)), 1.5);
      ctx.fillStyle = color;
      ctx.fillRect(center - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    }
    const volume = Number(row.v) || 0;
    const barHeight = (volume / maxVolume) * (volumeHeight - 5);
    ctx.fillStyle = rising ? "rgba(229,72,77,.42)" : "rgba(49,111,238,.38)";
    ctx.fillRect(center - candleWidth / 2, height - pad.bottom - barHeight, candleWidth, barHeight);
  });
  if (!series.some((row) => [row.o, row.h, row.l].every((value) => Number.isFinite(Number(value))))) {
    drawLine(ctx, series, "c", "#73827a", 1.4, x, y);
  }
  drawLine(ctx, series, "m3", "#34a853", 1.1, x, y);
  drawLine(ctx, series, "m10", "#d97706", 1.4, x, y);
  drawLine(ctx, series, "m20", "#e53935", 2.2, x, y);
  drawLine(ctx, series, "m40", "#9a641d", 2.2, x, y);
  drawLine(ctx, series, "m60", "#3167ad", 2.2, x, y);
  drawVolumeDivider(ctx, pad.left, width - pad.right, volumeTop - 5);
  if (dailyMa10 != null) {
    ctx.strokeStyle = "#d97706"; ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(pad.left, y(dailyMa10)); ctx.lineTo(width - pad.right, y(dailyMa10)); ctx.stroke(); ctx.setLineDash([]);
  }
  ctx.font = "11px Segoe UI";
  ctx.fillStyle = "#34a853"; ctx.fillRect(pad.left, 9, 14, 1.1); ctx.fillStyle = "#48574f"; ctx.fillText("MA3", pad.left + 19, 15);
  ctx.fillStyle = "#e53935"; ctx.fillRect(pad.left + 58, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA20", pad.left + 77, 15);
  ctx.fillStyle = "#9a641d"; ctx.fillRect(pad.left + 126, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA40", pad.left + 145, 15);
  ctx.fillStyle = "#3167ad"; ctx.fillRect(pad.left + 194, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA60", pad.left + 213, 15);
  if (dailyMa10 != null) {
    ctx.fillStyle = "#d97706"; ctx.fillRect(pad.left, 27, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("일봉 MA10", pad.left + 19, 33);
  } else {
    ctx.fillStyle = "#d97706"; ctx.fillRect(pad.left, 27, 14, 2); ctx.fillStyle = "#48574f"; ctx.fillText("MA10", pad.left + 19, 33);
  }
  ctx.fillStyle = "#64746c";
  const dateIndexes = [0];
  for (let index = 1; index < series.length; index += 1) {
    if (series[index].t.slice(0, 10) !== series[index - 1].t.slice(0, 10)) dateIndexes.push(index);
  }
  dateIndexes.forEach((index) => {
    const label = series[index].t.slice(5, 10);
    const center = x(index);
    ctx.strokeStyle = "rgba(226,232,228,.7)";
    ctx.beginPath(); ctx.moveTo(center, pad.top); ctx.lineTo(center, height - pad.bottom); ctx.stroke();
    ctx.fillStyle = "#64746c";
    ctx.fillText(label, Math.min(center + 3, width - pad.right - ctx.measureText(label).width), height - 8);
  });
  drawRegistrationMarker(ctx, series, "t", chartViewport.registeredAt, x, pad, width, height);
  drawTradeMarkers(ctx, series, x, pad, width, height);
}

const maHover = { series: [], pad: null, slot: 0 };
function hideMaTooltip() {
  document.getElementById("maPriceTooltip").hidden = true;
}
function showMaTooltip(event) {
  const { series, pad, slot } = maHover;
  if (!pad || !slot || !series.length || chartPointers.size > 1) return hideMaTooltip();
  const rect = elements.chart.getBoundingClientRect();
  const localX = event.clientX - rect.left;
  const localY = event.clientY - rect.top;
  if (localX < pad.left || localX > rect.width - pad.right || localY < pad.top || localY > rect.height - pad.bottom) return hideMaTooltip();
  const row = series[Math.min(series.length - 1, Math.floor((localX - pad.left) / slot))];
  const tooltip = document.getElementById("maPriceTooltip");
  tooltip.replaceChildren();
  const date = document.createElement("strong");
  date.textContent = formatDateTime(row.t);
  tooltip.append(date);
  for (const [key, label, color] of [["m3", "MA3", "#34a853"], ["m10", "MA10", "#d97706"], ["m20", "MA20", "#e53935"], ["m40", "MA40", "#9a641d"], ["m60", "MA60", "#3167ad"]]) {
    const line = document.createElement("div");
    line.style.color = color;
    line.textContent = `${label} ${Number.isFinite(row[key]) ? formatter.format(row[key]) + "원" : "자료 없음"}`;
    tooltip.append(line);
  }
  tooltip.hidden = false;
  tooltip.style.left = `${Math.max(4, Math.min(localX + 12, rect.width - tooltip.offsetWidth - 4))}px`;
  tooltip.style.top = `${Math.max(4, Math.min(localY + 12, rect.height - tooltip.offsetHeight - 4))}px`;
}
elements.chart.addEventListener("pointermove", showMaTooltip);
elements.chart.addEventListener("pointerdown", showMaTooltip);
elements.chart.addEventListener("pointerleave", event => { if (event.pointerType !== "touch") hideMaTooltip(); });
document.addEventListener("pointerdown", event => { if (event.target !== elements.chart) hideMaTooltip(); });

const dailyHover = { series: [], history: [], pad: null, slot: 0 };
function hideDailyTooltip() {
  document.querySelector("#dailyPriceTooltip").hidden = true;
}
function showDailyTooltip(event) {
  const { series, history, pad, slot } = dailyHover;
  if (!pad || !slot || !series.length) return hideDailyTooltip();
  const canvas = elements.dailyChart;
  const rect = canvas.getBoundingClientRect();
  const localX = event.clientX - rect.left;
  const localY = event.clientY - rect.top;
  if (localX < pad.left || localX > rect.width - pad.right || localY < pad.top || localY > rect.height - pad.bottom) return hideDailyTooltip();
  const index = Math.min(series.length - 1, Math.floor((localX - pad.left) / slot));
  const row = series[index];
  const prior = history.filter(bar => bar.d < row.d).at(-1) || series[index - 1];
  const tooltip = document.querySelector("#dailyPriceTooltip");
  tooltip.replaceChildren();
  const date = document.createElement("strong");
  date.textContent = row.d;
  tooltip.append(date);
  for (const [key, label] of [["o", "시가"], ["h", "고가"], ["l", "저가"], ["c", "종가"]]) {
    const value = Number(row[key]);
    const pct = prior?.c > 0 ? (value / prior.c - 1) * 100 : null;
    const line = document.createElement("div");
    line.className = pct == null ? "" : pct > 0 ? "price-up" : pct < 0 ? "price-down" : "price-flat";
    line.textContent = `${label} ${formatter.format(value)}원 (${pct == null ? "기준자료 없음" : `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`})`;
    tooltip.append(line);
  }
  tooltip.hidden = false;
  const left = Math.max(4, Math.min(localX + 12, rect.width - tooltip.offsetWidth - 4));
  const top = Math.max(4, Math.min(localY + 12, rect.height - tooltip.offsetHeight - 4));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}
elements.dailyChart.addEventListener("pointermove", showDailyTooltip);
elements.dailyChart.addEventListener("pointerdown", showDailyTooltip);
elements.dailyChart.addEventListener("pointerleave", event => { if (event.pointerType !== "touch") hideDailyTooltip(); });
document.addEventListener("pointerdown", event => { if (event.target !== elements.dailyChart) hideDailyTooltip(); });

function drawDailyChart(series) {
  hideDailyTooltip();
  dailyHover.series = series;
  dailyHover.history = selectedDetailItem()?.displayCharts?.daily?.history || [];
  dailyHover.pad = null;
  const canvas = elements.dailyChart;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 800;
  const height = canvas.clientHeight || 300;
  canvas.width = Math.floor(width * ratio);
  canvas.height = Math.floor(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width, height);
  if (series.length < 2) {
    ctx.fillStyle = "#64746c";
    ctx.font = '13px "Malgun Gothic"';
    ctx.fillText("다음 일봉 후보 갱신 때 그래프가 표시됩니다.", 20, 35);
    return;
  }

  const closes = series.map((row) => Number(row.c));
  const ma10 = closes.map((_, index) => {
    if (index < 9) return null;
    return closes.slice(index - 9, index + 1).reduce((sum, value) => sum + value, 0) / 10;
  });
  const chartSeries = series.map((row, index) => ({ ...row, m10: row.m10 ?? ma10[index] }));
  const observationDate = state.payload?.generatedAt?.slice(0, 10);
  const completedSessions = chartSeries.filter((row) => !observationDate || row.d < observationDate).slice(-20);
  const averageVolume = completedSessions.length === 20 && completedSessions.every((row) => Number.isFinite(Number(row.v)))
    ? completedSessions.reduce((sum, row) => sum + Number(row.v), 0) / 20
    : null;
  const values = chartSeries.flatMap((row) => [row.h, row.l, row.c, row.m5, row.m10, row.m20, row.m60]).filter((value) => value != null);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const rawSpread = Math.max(max - min, 1);
  const priceMin = min - rawSpread * 0.04;
  const priceMax = max + rawSpread * 0.04;
  const spread = priceMax - priceMin;
  const mobile = window.innerWidth <= 540;
  ctx.font = mobile ? "10px Segoe UI" : "11px Segoe UI";
  const priceLabel = (value) => mobile ? formatter.format(Math.round(value)) : formatter.format(value);
  const labelWidth = Math.max(...[priceMin, priceMax].map((value) => ctx.measureText(priceLabel(value)).width));
  const pad = { left: mobile ? Math.ceil(labelWidth) + 5 : 54, right: mobile ? 3 : 12, top: 28, bottom: 28 };
  const volumeHeight = Math.max(42, Math.round(height * 0.2));
  const volumeTop = height - pad.bottom - volumeHeight;
  const priceBottom = volumeTop - 24;
  const plotWidth = width - pad.left - pad.right;
  const slot = plotWidth / chartSeries.length;
  dailyHover.pad = pad;
  dailyHover.slot = slot;
  const x = (index) => pad.left + slot * (index + 0.5);
  const y = (value) => pad.top + ((priceMax - value) / spread) * (priceBottom - pad.top);

  ctx.strokeStyle = "#e2e8e4";
  ctx.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const yy = pad.top + (step / 4) * (priceBottom - pad.top);
    ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke();
    ctx.fillStyle = "#64746c"; ctx.font = mobile ? "10px Segoe UI" : "11px Segoe UI";
    const label = priceLabel(priceMax - (spread * step) / 4);
    ctx.fillText(label, mobile ? pad.left - ctx.measureText(label).width - 4 : 3, yy + 4);
  }

  const maxVolume = Math.max(...chartSeries.map((row) => Number(row.v) || 0), 1);
  const candleWidth = Math.max(2, Math.min(8, slot * 0.66));
  chartSeries.forEach((row, index) => {
    const open = Number(row.o);
    const high = Number(row.h);
    const low = Number(row.l);
    const close = Number(row.c);
    const rising = close >= open;
    const color = rising ? "#e5484d" : "#316fee";
    const center = x(index);
    ctx.strokeStyle = color;
    ctx.beginPath(); ctx.moveTo(center, y(high)); ctx.lineTo(center, y(low)); ctx.stroke();
    ctx.fillStyle = color;
    ctx.fillRect(center - candleWidth / 2, Math.min(y(open), y(close)), candleWidth, Math.max(Math.abs(y(open) - y(close)), 1.5));
    const barHeight = ((Number(row.v) || 0) / maxVolume) * (volumeHeight - 5);
    ctx.fillStyle = rising ? "rgba(229,72,77,.38)" : "rgba(49,111,238,.34)";
    ctx.fillRect(center - candleWidth / 2, height - pad.bottom - barHeight, candleWidth, barHeight);
  });

  ctx.font = '11px "Malgun Gothic"';
  ctx.fillStyle = "#53636b";
  const volumeLabel = averageVolume == null
    ? "거래량 20일 평균: 자료 부족"
    : `거래량 20일 평균 ${formatter.format(Math.round(averageVolume))}주`;
  ctx.fillText(volumeLabel, pad.left, volumeTop - 7);
  if (averageVolume != null) {
    const averageY = height - pad.bottom - (averageVolume / maxVolume) * (volumeHeight - 5);
    ctx.save();
    ctx.strokeStyle = "#53636b";
    ctx.lineWidth = 1.4;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(pad.left, averageY);
    ctx.lineTo(width - pad.right, averageY);
    ctx.stroke();
    ctx.restore();
  }

  drawLine(ctx, chartSeries, "m5", "#34a853", 1.5, x, y);
  drawLine(ctx, chartSeries, "m10", "#d97706", 1.8, x, y);
  drawLine(ctx, chartSeries, "m20", "#e53935", 2.1, x, y);
  drawLine(ctx, chartSeries, "m60", "#3167ad", 2.1, x, y);
  drawVolumeDivider(ctx, pad.left, width - pad.right, volumeTop - 22);
  ctx.font = "11px Segoe UI";
  ctx.fillStyle = "#34a853"; ctx.fillRect(pad.left, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA5", pad.left + 19, 15);
  ctx.fillStyle = "#d97706"; ctx.fillRect(pad.left + 60, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA10", pad.left + 79, 15);
  ctx.fillStyle = "#e53935"; ctx.fillRect(pad.left + 132, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA20", pad.left + 151, 15);
  ctx.fillStyle = "#3167ad"; ctx.fillRect(pad.left + 204, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA60", pad.left + 223, 15);

  const labelStep = Math.max(1, Math.ceil(series.length / 5));
  chartSeries.forEach((row, index) => {
    if (index % labelStep !== 0 && index !== chartSeries.length - 1) return;
    const label = row.d.slice(5);
    const center = x(index);
    ctx.fillStyle = "#64746c";
    ctx.fillText(label, Math.min(center, width - pad.right - ctx.measureText(label).width), height - 8);
  });
  drawRegistrationMarker(ctx, chartSeries, "d", chartViewport.registeredAt, x, pad, width, height);
}

function drawRegistrationMarker(ctx, series, key, registeredAt, x, pad, width, height, options = {}) {
  if (!registeredAt || !series.length) return;
  const stamp = key === "d" ? registeredAt.slice(0, 10) : registeredAt;
  const time = value => Date.parse(key === "d" ? `${value}T00:00:00+09:00` : value);
  const target = time(stamp);
  if (!Number.isFinite(target)) return;
  const first = time(series[0][key]);
  const last = time(series.at(-1)[key]);
  if (target < first || target > last + (key === "t" ? 30 * 60000 : 0)) return;
  let index = series.findIndex(row => time(row[key]) >= target);
  let center;
  if (index < 0) center = x(series.length - 1);
  else if (time(series[index][key]) === target || index === 0) center = x(index);
  else {
    const previous = time(series[index - 1][key]);
    const next = time(series[index][key]);
    // Market closures occupy a boundary, not a fabricated candle.
    const fraction = next - previous > 30 * 60000 ? .5 : (target - previous) / (next - previous);
    center = x(index - 1) + (x(index) - x(index - 1)) * fraction;
  }
  ctx.save();
  ctx.strokeStyle = options.color || "#53616d";
  ctx.lineWidth = 1.5;
  ctx.setLineDash(options.solid ? [] : [4, 4]);
  ctx.beginPath(); ctx.moveTo(center, pad.top); ctx.lineTo(center, height - pad.bottom); ctx.stroke();
  ctx.setLineDash([]);
  ctx.font = '11px "Malgun Gothic"';
  const label = options.label || `등록 ${registeredAt.slice(5, 10)}`;
  const labelWidth = ctx.measureText(label).width;
  const left = Math.max(pad.left, Math.min(center + 4, width - pad.right - labelWidth - 4));
  ctx.fillStyle = "rgba(255,255,255,.92)";
  const labelTop = pad.top + (options.labelOffset || 0);
  ctx.fillRect(left - 2, labelTop + 2, labelWidth + 4, 16);
  ctx.fillStyle = options.color || "#384651";
  ctx.fillText(label, left, labelTop + 14);
  ctx.restore();
}

function drawTradeMarkers(ctx, series, x, pad, width, height) {
  const item = selectedDetailItem();
  if (!item) return;
  const candidate = [...(state.payload?.candidates || []), ...(state.payload?.history || [])].find(row => row.code === item.code);
  const markers = new Map();
  for (const event of candidate?.paperStrategy?.events || []) {
    if (!candidate.registeredAt || Date.parse(event.time) < Date.parse(candidate.registeredAt)) continue;
    markers.set(`signal|${event.time}`, {time: event.time, color: "#263b46", label: `신호 ${formatter.format(event.price)}`});
  }
  for (const position of state.positions?.positions || []) {
    if (position.code !== item.code) continue;
    const virtual = position.mode === "virtual";
    const time = virtual ? position.signalTime : position.openedAt;
    if (virtual && (!candidate?.registeredAt || Date.parse(time) < Date.parse(candidate.registeredAt))) continue;
    if (time) markers.set(`${virtual ? "signal" : "buy"}|${time}`, {
      time, color: "#263b46", label: `${virtual ? "가상매수" : "실제매수"} ${formatter.format(position.buyPrice)}`,
    });
    if (position.status === "closed" && position.closedAt && position.sellPrice != null) {
      const profit = position.sellPrice >= position.buyPrice;
      markers.set(`sell|${position.closedAt}|${position.id || position.openedAt}`, {
        time: position.closedAt, color: profit ? "#d32f2f" : "#245cc2", labelOffset: 18,
        label: `${virtual ? "가상" : ""}${profit ? "수익" : "손실"}매도 ${formatter.format(position.sellPrice)}`,
      });
    }
  }
  for (const marker of markers.values()) {
    drawRegistrationMarker(ctx, series, "t", marker.time, x, pad, width, height, {...marker, solid: true});
  }
}

function drawVolumeDivider(ctx, left, right, top) {
  ctx.save();
  ctx.strokeStyle = "#89978f";
  ctx.lineWidth = 2;
  ctx.setLineDash([]);
  ctx.beginPath(); ctx.moveTo(left, top); ctx.lineTo(right, top); ctx.stroke();
  ctx.restore();
}

function drawLine(ctx, series, key, color, width, x, y) {
  ctx.strokeStyle = color; ctx.lineWidth = width; ctx.beginPath();
  let drawing = false;
  series.forEach((row, index) => {
    if (row[key] == null) { drawing = false; return; }
    if (!drawing) { ctx.moveTo(x(index), y(row[key])); drawing = true; }
    else ctx.lineTo(x(index), y(row[key]));
  });
  ctx.stroke();
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "-";
  return new Intl.DateTimeFormat("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function formatShortTime(value) {
  return value ? value.slice(5, 16).replace("T", " ") : "";
}

document.querySelectorAll(".view-tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.view = button.dataset.view;
    state.keyword = "";
    elements.keyword.value = "";
    state.deepLinkPending = false;
    render();
  });
});
document.querySelector("#operationExclude").addEventListener("click", () => {
  const item = state.payload?.candidates?.find((entry) => entry.code === document.querySelector("#operationStock").value);
  if (item) openExclusionDialog(item, "exclude");
});
elements.keyword.addEventListener("input", (event) => { state.keyword = event.target.value; render(); });
elements.refreshButton.addEventListener("click", loadData);
elements.criteriaButton.addEventListener("click", () => elements.criteriaDialog.showModal());
elements.criteriaDialogClose.addEventListener("click", () => elements.criteriaDialog.close());
elements.criteriaDialog.addEventListener("click", (event) => {
  if (event.target === elements.criteriaDialog) elements.criteriaDialog.close();
});
elements.positionManagerButton.addEventListener("click", () => openPositionDialog());
elements.positionDialogClose.addEventListener("click", closePositionDialog);
elements.positionCancelButton.addEventListener("click", closePositionDialog);
elements.positionForm.addEventListener("submit", submitPosition);
elements.positionForm.querySelectorAll('input[name="positionAction"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    setPositionMode(radio.value);
    if (radio.value === "buy") updateStopPolicy();
  });
});
elements.positionBuyPrice.addEventListener("change", updateStopPolicy);
elements.positionStopPrice.addEventListener("change", () => {
  elements.positionStopPrice.dataset.technicalStopPrice = elements.positionStopPrice.value.trim();
  updateStopPolicy();
});
elements.githubToken.addEventListener("input", updateConnectionState);
elements.clearGithubToken.addEventListener("click", () => {
  try { localStorage.removeItem(GITHUB_TOKEN_KEY); } catch { /* Nothing else to clear. */ }
  elements.githubToken.value = "";
  updateConnectionState();
});
elements.positionDialog.addEventListener("click", (event) => {
  if (event.target === elements.positionDialog) closePositionDialog();
});
function refreshVisibleData(maxAge = 60000) {
  if (document.visibilityState !== "visible" || document.querySelector("dialog[open]")) return;
  if (Date.now() - state.lastLoadedAt >= maxAge) loadData();
}

document.addEventListener("visibilitychange", () => refreshVisibleData());
window.addEventListener("pageshow", (event) => {
  if (event.persisted) refreshVisibleData(0);
});
window.setInterval(() => refreshVisibleData(600000), 600000);

window.addEventListener("resize", () => {
  if (!state.payload) return;
  placeDetailPanel();
  renderSelectedDetail();
});

loadData();
