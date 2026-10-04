const state = { payload: null, filter: "all", keyword: "", selectedCode: null };

const elements = {
  activeCount: document.querySelector("#activeCount"),
  signalCount: document.querySelector("#signalCount"),
  risingCount: document.querySelector("#risingCount"),
  asOf: document.querySelector("#asOf"),
  runStatus: document.querySelector("#runStatus"),
  updatedAt: document.querySelector("#updatedAt"),
  pushState: document.querySelector("#pushState"),
  candidateMeta: document.querySelector("#candidateMeta"),
  candidateList: document.querySelector("#candidateList"),
  template: document.querySelector("#candidateTemplate"),
  keyword: document.querySelector("#keyword"),
  refreshButton: document.querySelector("#refreshButton"),
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
  lastBar: document.querySelector("#lastBar"),
  dailySignal: document.querySelector("#dailySignal"),
  remainingDays: document.querySelector("#remainingDays"),
  signalMessage: document.querySelector("#signalMessage"),
  chart: document.querySelector("#maChart"),
};

const statusLabels = {
  signal: "매수 검토",
  signaled: "신호 이력",
  rising: "상승 진행",
  watching: "관찰 중",
  insufficient: "기준자료 부족",
  ineligible: "초기조건 제외",
};
const formatter = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });

async function loadData() {
  elements.refreshButton.disabled = true;
  try {
    const response = await fetch(`data/candidate-monitor.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.payload = await response.json();
    const candidates = state.payload.candidates || [];
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
  elements.activeCount.textContent = summary.active ?? candidates.length;
  elements.signalCount.textContent = summary.signals ?? 0;
  elements.risingCount.textContent = summary.rising ?? 0;
  elements.asOf.textContent = asOf || "-";
  elements.runStatus.textContent = summary.dataErrors ? `분봉 오류 ${summary.dataErrors}건` : "클라우드 감시 정상";
  elements.updatedAt.textContent = generatedAt ? `마지막 갱신 ${formatDateTime(generatedAt)}` : "갱신 기록 없음";
  elements.pushState.textContent = summary.pushConfigured ? "휴대폰 푸시 연결" : "푸시 연결 대기";
  elements.pushState.className = `status-chip${summary.pushConfigured ? "" : " rising"}`;
  renderCandidates(candidates);
  renderDetail(candidates.find((item) => item.code === state.selectedCode));
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
  const visible = filteredCandidates(candidates);
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
    node.querySelector(".candidate-days").textContent = `A-G ${item.dailySignalDate} · ${item.tradingDaysRemaining}일 남음`;
    node.querySelector(".candidate-price").textContent = intraday.lastPrice ? `${formatter.format(intraday.lastPrice)}원` : "분봉 대기";
    node.addEventListener("click", () => { state.selectedCode = item.code; render(); });
    elements.candidateList.append(node);
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
  elements.lastBar.textContent = intraday.lastBarTime ? formatDateTime(intraday.lastBarTime) : "-";
  elements.dailySignal.textContent = item.dailySignalDate;
  elements.remainingDays.textContent = `${item.tradingDaysRemaining}거래일`;
  elements.signalMessage.className = `signal-message${item.status === "signal" ? " signal" : ""}`;
  elements.signalMessage.textContent = signalCopy(item, intraday);
  drawChart(intraday.series || []);
}

function signalCopy(item, intraday) {
  if (item.status === "signal" || item.status === "signaled") {
    const lead = item.status === "signal" ? "조건이 방금 확정됐습니다." : "과거 감시 중 조건이 확정된 이력입니다.";
    return `${formatDateTime(intraday.signalTime)} 완성봉에서 ${lead} 신호 직후 다음 30분봉부터 HTS 현재가와 거래량을 확인하는 조건입니다.`;
  }
  if (item.status === "rising") {
    return `MA20이 MA40 아래에서 반등해 ${intraday.riseCount || 0}회 연속 상승 중입니다. 5회가 완성될 때까지 관찰합니다.`;
  }
  if (item.status === "insufficient") return "A-G 발생일의 30분봉 MA20·MA40 기준값이 네이버 제공 범위에서 벗어났습니다. 신규 후보부터 기준값을 자동 저장합니다.";
  if (item.status === "ineligible") return "A-G 발생일 마감 시 MA20이 MA40 위에 있지 않아 30분봉 후속 감시에서 제외했습니다.";
  if (intraday.dataStatus === "error") return "네이버 분봉을 가져오지 못했습니다. 다음 예약 실행에서 다시 시도합니다.";
  return "MA20이 MA40 아래에서 반등한 뒤 5회 연속 상승하는지 관찰 중입니다.";
}

function drawChart(series) {
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
  const values = series.flatMap((row) => [row.c, row.m20, row.m40]).filter((value) => value != null);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = Math.max(max - min, 1);
  const pad = { left: 54, right: 18, top: 22, bottom: 34 };
  const x = (index) => pad.left + (index / (series.length - 1)) * (width - pad.left - pad.right);
  const y = (value) => pad.top + ((max - value) / spread) * (height - pad.top - pad.bottom);
  ctx.strokeStyle = "#e2e8e4";
  ctx.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const yy = pad.top + (step / 4) * (height - pad.top - pad.bottom);
    ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke();
    ctx.fillStyle = "#64746c"; ctx.font = "11px Segoe UI";
    ctx.fillText(formatter.format(max - (spread * step) / 4), 3, yy + 4);
  }
  drawLine(ctx, series, "c", "#73827a", 1.4, x, y);
  drawLine(ctx, series, "m20", "#ba3f3f", 2.2, x, y);
  drawLine(ctx, series, "m40", "#3167ad", 2.2, x, y);
  ctx.fillStyle = "#ba3f3f"; ctx.fillRect(pad.left, 7, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA20", pad.left + 19, 13);
  ctx.fillStyle = "#3167ad"; ctx.fillRect(pad.left + 72, 7, 14, 3); ctx.fillStyle = "#48574f"; ctx.fillText("MA40", pad.left + 91, 13);
  ctx.fillStyle = "#64746c";
  ctx.fillText(formatShortTime(series[0].t), pad.left, height - 10);
  const lastLabel = formatShortTime(series.at(-1).t);
  ctx.fillText(lastLabel, width - pad.right - ctx.measureText(lastLabel).width, height - 10);
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
window.addEventListener("resize", () => {
  if (!state.payload) return;
  renderDetail(state.payload.candidates.find((item) => item.code === state.selectedCode));
});

loadData();
