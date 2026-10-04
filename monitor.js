const state = { payload: null, positions: null, filter: "all", keyword: "", selectedCode: null };
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
  detailName: document.querySelector("#detailName"),
  detailMeta: document.querySelector("#detailMeta"),
  naverLink: document.querySelector("#naverLink"),
  emptyDetail: document.querySelector("#emptyDetail"),
  detailContent: document.querySelector("#detailContent"),
  detailBadge: document.querySelector("#detailBadge"),
  detailProgress: document.querySelector("#detailProgress"),
  lastPrice: document.querySelector("#lastPrice"),
  ma20: document.querySelector("#ma20"),
  ma40: document.querySelector("#ma40"),
  ma60: document.querySelector("#ma60"),
  dailyMa10: document.querySelector("#dailyMa10"),
  lastBar: document.querySelector("#lastBar"),
  dailySignal: document.querySelector("#dailySignal"),
  remainingDays: document.querySelector("#remainingDays"),
  signalMessage: document.querySelector("#signalMessage"),
  chart: document.querySelector("#maChart"),
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

const statusLabels = {
  signal: "매수 검토",
  signaled: "매수포착 완료",
  setup: "매수 준비",
  rising: "상승 진행",
  watching: "관찰 중",
  insufficient: "기준자료 부족",
  ineligible: "초기조건 제외",
  excluded: "일봉 MA10 이탈",
  waiting10: "MA10 회복 대기",
  waiting60: "MA60 돌파 대기",
};
const statusPriority = { signal: 0, rising: 1, waiting60: 2, setup: 3, signaled: 4, watching: 5, waiting10: 6, insufficient: 7, ineligible: 8, excluded: 9 };
const formatter = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });

async function loadData() {
  elements.refreshButton.disabled = true;
  try {
    const stamp = Date.now();
    const [response, positionsResponse] = await Promise.all([
      fetch(`data/candidate-monitor.json?t=${stamp}`, { cache: "no-store" }),
      fetch(`data/positions.json?t=${stamp}`, { cache: "no-store" }),
    ]);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.payload = await response.json();
    state.positions = positionsResponse.ok ? await positionsResponse.json() : { positions: [] };
    const candidates = (state.payload.candidates || []).filter((item) => !["excluded", "ineligible"].includes(item.status));
    if (!state.selectedCode || !candidates.some((item) => item.code === state.selectedCode)) {
      state.selectedCode = candidates[0]?.code || null;
    }
    render();
  } catch (error) {
    elements.runStatus.textContent = "감시 데이터를 불러오지 못했습니다";
    elements.updatedAt.textContent = String(error);
    elements.candidateList.innerHTML = '<div class="empty-list">잠시 후 다시 시도해 주세요.</div>';
  } finally {
    elements.refreshButton.disabled = false;
  }
}

function render() {
  const { summary = {}, candidates = [], asOf, generatedAt } = state.payload;
  const activeCandidates = candidates.filter((item) => !["excluded", "ineligible"].includes(item.status));
  elements.activeCount.textContent = summary.active ?? activeCandidates.length;
  elements.setupCount.textContent = (summary.setup ?? 0) + (summary.waiting60 ?? 0);
  elements.signalCount.textContent = summary.signals ?? 0;
  elements.signaledCount.textContent = summary.signalHistory ?? 0;
  elements.risingCount.textContent = summary.rising ?? 0;
  const openPositions = (state.positions?.positions || []).filter((item) => item.status === "open");
  elements.positionCount.textContent = openPositions.length;
  elements.asOf.textContent = asOf || "-";
  elements.runStatus.textContent = summary.dataErrors ? `분봉 오류 ${summary.dataErrors}건` : "클라우드 감시 정상";
  elements.updatedAt.textContent = generatedAt ? `마지막 갱신 ${formatDateTime(generatedAt)}` : "갱신 기록 없음";
  elements.pushState.textContent = summary.pushConfigured ? "휴대폰 푸시 연결" : "푸시 연결 대기";
  elements.pushState.className = `status-chip${summary.pushConfigured ? "" : " rising"}`;
  renderCandidates(activeCandidates);
  const closedPositions = (state.positions?.positions || []).filter((item) => item.status === "closed");
  renderPositions(openPositions, closedPositions);
  renderDetail(activeCandidates.find((item) => item.code === state.selectedCode));
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
    const returnPct = item.returnPct;
    const returnClass = returnPct == null ? "" : returnPct >= 0 ? "positive" : "negative";
    const warning = item.riskWarning
      ? "손절폭이 목표수익률보다 큽니다. 매수·비중 재검토"
      : item.trendWeak ? "30분봉 추세약화 감지" : "목표가·손절가 감시 중";
    article.className = "position-item";
    article.innerHTML = `
      <header><strong></strong><span></span></header>
      <div class="position-values"><span class="position-price"></span><span class="position-return ${returnClass}"></span></div>
      <div class="position-values"><span class="position-quantity"></span><span class="position-value"></span></div>
      <div class="position-values"><span class="position-stop"></span><span class="position-target"></span></div>
      <div class="position-warning"></div>
      <div class="position-item-actions"><button class="position-edit" type="button">수정</button><button class="position-close" type="button">매도완료</button></div>`;
    article.querySelector("strong").textContent = item.name;
    article.querySelector("header span").textContent = item.code;
    article.querySelector(".position-price").textContent = `매수 ${formatter.format(item.buyPrice)}원 · 현재 ${item.lastPrice ? formatter.format(item.lastPrice) : "-"}원`;
    article.querySelector(".position-return").textContent = returnPct == null ? "-" : `${returnPct >= 0 ? "+" : ""}${formatter.format(returnPct)}%`;
    article.querySelector(".position-quantity").textContent = item.quantity ? `${formatter.format(item.quantity)}주` : "수량 미입력";
    const investedAmount = item.investedAmount ?? (item.quantity ? item.buyPrice * item.quantity : null);
    const currentValue = item.quantity && item.lastPrice ? item.quantity * item.lastPrice : null;
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
  article.querySelector("strong").textContent = item.name;
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
    const statusMatch = state.filter === "all" || item.status === state.filter;
    const keywordMatch = !keyword || item.name.toLowerCase().includes(keyword) || item.code.includes(keyword);
    return statusMatch && keywordMatch;
  });
}

function renderCandidates(candidates) {
  const visible = filteredCandidates(candidates).sort((a, b) =>
    (statusPriority[a.status] ?? 99) - (statusPriority[b.status] ?? 99)
    || a.name.localeCompare(b.name, "ko")
  );
  elements.candidateMeta.textContent = `${visible.length}개 표시 / ${candidates.length}개 관리`;
  elements.candidateList.innerHTML = "";
  if (!visible.length) {
    elements.candidateList.innerHTML = '<div class="empty-list">현재 조건에 해당하는 후보가 없습니다.</div>';
    return;
  }
  for (const item of visible) {
    const node = elements.template.content.firstElementChild.cloneNode(true);
    const intraday = item.intraday || {};
    const rise = Math.min(intraday.riseCount || 0, 5);
    node.dataset.status = item.status;
    node.classList.toggle("selected", item.code === state.selectedCode);
    node.querySelector(".candidate-name").textContent = item.name;
    node.querySelector(".candidate-status").textContent = statusLabels[item.status] || "확인 필요";
    node.querySelector(".candidate-code").textContent = `${item.code} · ${item.market}`;
    node.querySelector(".candidate-progress i").style.width = `${(rise / 5) * 100}%`;
    node.querySelector(".candidate-rise").textContent = `${rise} / 5`;
    node.querySelector(".candidate-days").textContent = `A-G ${item.dailySignalDate} · ${item.tradingDaysRemaining}일 남음`;
    node.querySelector(".candidate-price").textContent = intraday.lastPrice ? `${formatter.format(intraday.lastPrice)}원` : "분봉 대기";
    const technicalStop = item.daily?.preSpikeClose;
    const maximumLossStop = intraday.lastPrice ? Math.round(intraday.lastPrice * 0.95) : null;
    const stopPrice = technicalStop && maximumLossStop ? Math.max(technicalStop, maximumLossStop) : technicalStop;
    const capped = technicalStop && maximumLossStop && technicalStop < maximumLossStop;
    node.querySelector(".candidate-stop").textContent = stopPrice
      ? `기본 손절 ${formatter.format(stopPrice)}원${capped ? " (-5% 제한)" : ""}`
      : "손절가 확인 필요";
    node.querySelector(".candidate-select").addEventListener("click", () => { state.selectedCode = item.code; render(); });
    node.querySelector(".candidate-register").addEventListener("click", () => {
      const position = state.positions?.positions?.find((entry) => entry.code === item.code && entry.status === "open") || null;
      openPositionDialog(item, position, "buy");
    });
    elements.candidateList.append(node);
  }
}

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
  elements.detailMeta.textContent = `${item.code} · ${item.market} · ${statusLabels[item.status] || "확인 필요"}`;
  elements.detailBadge.textContent = statusLabels[item.status] || "확인 필요";
  elements.detailBadge.className = `status-chip ${item.status}`;
  elements.detailProgress.textContent = `${Math.min(intraday.riseCount || 0, 5)} / 5`;
  elements.lastPrice.textContent = intraday.lastPrice ? `${formatter.format(intraday.lastPrice)}원` : "-";
  elements.ma20.textContent = intraday.ma20 == null ? "-" : formatter.format(intraday.ma20);
  elements.ma40.textContent = intraday.ma40 == null ? "-" : formatter.format(intraday.ma40);
  elements.ma60.textContent = intraday.ma60 == null ? "-" : formatter.format(intraday.ma60);
  elements.dailyMa10.textContent = intraday.dailyMa10 == null ? "-" : formatter.format(intraday.dailyMa10);
  elements.lastBar.textContent = intraday.lastBarTime ? formatDateTime(intraday.lastBarTime) : "-";
  elements.dailySignal.textContent = item.dailySignalDate;
  elements.remainingDays.textContent = `${item.tradingDaysRemaining}거래일`;
  elements.signalMessage.className = `signal-message${item.status === "signal" ? " signal" : ""}`;
  elements.signalMessage.textContent = signalCopy(item, intraday);
  drawChart(intraday.series || [], intraday.dailyMa10);
}

function signalCopy(item, intraday) {
  if (item.status === "signal" || item.status === "signaled") {
    const lead = item.status === "signal" ? "조건이 방금 확정됐습니다." : "과거 감시 중 조건이 확정된 이력입니다.";
    const estimate = intraday.baselineInferred ? " 초기 하향교차 시점은 네이버 과거 데이터 범위로 추정했습니다." : "";
    return `${formatDateTime(intraday.signalTime)} 완성봉에서 ${lead} 신호 직후 다음 30분봉부터 HTS 현재가와 거래량을 확인하는 조건입니다.${estimate}`;
  }
  if (item.status === "rising") {
    return `MA20이 MA40 아래에서 반등해 ${intraday.riseCount || 0}회 연속 상승 중입니다. 5회가 완성될 때까지 관찰합니다.`;
  }
  if (item.status === "setup") return "A-G 확정 후 MA20이 MA40 아래로 내려왔습니다. 반등이 시작되어 1/5가 되는지 관찰하는 매수 준비 단계입니다.";
  if (item.status === "waiting60") return "MA20이 MA60 아래까지 내려갔습니다. MA20이 MA60을 다시 돌파한 완성봉까지 기다립니다.";
  if (item.status === "waiting10") return "30분봉 MA20이 해당 날짜의 직전 완료 일봉 MA10 아래에 있어 매수신호를 보류합니다. MA10 위로 회복하면 감시를 자동 재개합니다.";
  if (item.status === "excluded") return "MA20이 MA40·MA60·일봉 MA10 아래에서 3개 완료봉 이상 연속 하락해 구조적 약세 후보로 제외했습니다.";
  if (item.status === "insufficient") return "A-G 발생일의 30분봉 MA20·MA40 기준값과 후속 교차를 현재 네이버 제공 범위에서 확인할 수 없습니다. 신규 후보부터 기준값을 자동 저장합니다.";
  if (item.status === "ineligible") return "A-G 발생일 마감 시 MA20이 MA40 위에 있지 않아 30분봉 후속 감시에서 제외했습니다.";
  if (intraday.dataStatus === "error") return "네이버 분봉을 가져오지 못했습니다. 다음 예약 실행에서 다시 시도합니다.";
  return "MA20의 5회 상승을 관찰합니다. MA60 아래까지 조정되면 MA60 재돌파를 기다리고, 일봉 MA10 아래면 제외합니다.";
}

function drawChart(series, dailyMa10) {
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
  const values = series.flatMap((row) => [row.h, row.l, row.c, row.m20, row.m40, row.m60, dailyMa10]).filter((value) => value != null);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const rawSpread = Math.max(max - min, 1);
  const priceMin = min - rawSpread * 0.04;
  const priceMax = max + rawSpread * 0.04;
  const spread = priceMax - priceMin;
  const pad = { left: 54, right: 12, top: 28, bottom: 28 };
  const volumeHeight = Math.max(44, Math.round(height * 0.2));
  const volumeTop = height - pad.bottom - volumeHeight;
  const priceBottom = volumeTop - 10;
  const plotWidth = width - pad.left - pad.right;
  const slot = plotWidth / series.length;
  const x = (index) => pad.left + slot * (index + 0.5);
  const y = (value) => pad.top + ((priceMax - value) / spread) * (priceBottom - pad.top);
  ctx.strokeStyle = "#e2e8e4";
  ctx.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const yy = pad.top + (step / 4) * (priceBottom - pad.top);
    ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke();
    ctx.fillStyle = "#64746c"; ctx.font = "11px Segoe UI";
    ctx.fillText(formatter.format(priceMax - (spread * step) / 4), 3, yy + 4);
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
  drawLine(ctx, series, "m20", "#ba3f3f", 2.2, x, y);
  drawLine(ctx, series, "m40", "#3167ad", 2.2, x, y);
  drawLine(ctx, series, "m60", "#9a641d", 2.2, x, y);
  if (dailyMa10 != null) {
    ctx.strokeStyle = "#176b58"; ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(pad.left, y(dailyMa10)); ctx.lineTo(width - pad.right, y(dailyMa10)); ctx.stroke(); ctx.setLineDash([]);
  }
  ctx.font = "11px Segoe UI";
  ctx.fillStyle = "#ba3f3f"; ctx.fillRect(pad.left, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA20", pad.left + 19, 15);
  ctx.fillStyle = "#3167ad"; ctx.fillRect(pad.left + 68, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA40", pad.left + 87, 15);
  ctx.fillStyle = "#9a641d"; ctx.fillRect(pad.left + 136, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA60", pad.left + 155, 15);
  ctx.fillStyle = "#176b58"; ctx.fillRect(pad.left + 204, 9, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("일봉 MA10", pad.left + 223, 15);
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

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === button));
    state.filter = button.dataset.filter;
    render();
  });
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
window.addEventListener("resize", () => {
  if (!state.payload) return;
  renderDetail(state.payload.candidates.find((item) => item.code === state.selectedCode));
});

loadData();
