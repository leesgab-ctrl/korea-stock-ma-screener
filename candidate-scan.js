(() => {
  "use strict";
  const API = "https://api.github.com/repos/leesgab-ctrl/korea-stock-ma-screener/actions/workflows/update-pages.yml";
  const PENDING_KEY = "koreaStockMonitor.candidateScan";
  const RUN_BASE = "https://github.com/leesgab-ctrl/korea-stock-ma-screener/actions/runs/";
  let options, panel, pending, timer, polling = false, submitting = false;
  const read = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
  const field = name => panel.querySelector(`[data-scan="${name}"]`);
  const date = value => Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat("ko-KR", {timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}).format(new Date(value)) : "기록 없음";

  function savePending(value) {
    pending = value;
    if (value) write(PENDING_KEY, JSON.stringify(value));
    else { try { localStorage.removeItem(PENDING_KEY); } catch {} }
    field("submit").disabled = Boolean(value) || submitting;
    field("check").hidden = !value;
  }

  function status(message, error = false) {
    field("status").textContent = message;
    field("status").classList.toggle("error", error);
  }

  function showRun(id) {
    const link = field("run");
    link.hidden = !id;
    if (id) link.href = RUN_BASE + encodeURIComponent(id);
  }

  function refresh(payload) {
    if (!panel) return;
    payload ||= options.getPayload();
    const report = payload?.candidateScan;
    const candidates = payload?.candidates || [];
    field("last").textContent = date(report?.completedAt);
    field("basis").textContent = report?.asOf || payload?.asOf || "-";
    field("checked").textContent = report ? `${report.checkedStocks.toLocaleString("ko-KR")}종목` : "기록 없음";
    field("counts").textContent = `AG ${candidates.filter(item => item.candidateTier === "core").length} · AK ${candidates.filter(item => item.candidateTier === "expanded_ak").length} · AH ${candidates.filter(item => item.candidateTier === "ah").length}`;
    field("added").textContent = report ? `${report.newRegistrations.length}종목` : "기록 없음";
    field("dataNote").textContent = report && !report.currentDayData ? "당일 일봉 미수집 · 이전 거래일 데이터로 신규 등록하지 않았습니다." : "";
    const list = field("newList");
    list.replaceChildren();
    for (const item of report?.newRegistrations || []) {
      const li = document.createElement("li");
      li.textContent = `${item.name} (${item.code}) · ${item.candidateTier === "expanded_ak" ? "AK" : "AG"} · ${date(item.registeredAt)}`;
      list.append(li);
    }
    const recent = field("recent");
    recent.replaceChildren();
    for (const item of [...candidates].sort((a, b) => String(b.registeredAt).localeCompare(String(a.registeredAt))).slice(0, 10)) {
      const li = document.createElement("li");
      li.textContent = `${date(item.registeredAt)} · ${item.name} · ${item.candidateTier === "ah" ? "AH" : item.candidateTier === "expanded_ak" ? "AK" : "AG"}`;
      recent.append(li);
    }
  }

  async function api(suffix, init = {}) {
    const token = field("token").value.trim();
    if (!token) throw new Error("GitHub 연결키를 입력해 주세요. 이 저장소의 Actions 읽기·쓰기 권한이 필요합니다.");
    const response = await fetch(API + suffix, {
      ...init, cache: "no-store",
      headers: {Accept: "application/vnd.github+json", Authorization: `Bearer ${token}`,
        "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"},
    });
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) throw new Error("GitHub 연결키의 유효기간과 이 저장소의 Actions 읽기·쓰기 권한을 확인해 주세요.");
      throw new Error(`검색 요청 확인 실패 (${response.status})`);
    }
    return response.status === 204 ? null : response.json();
  }

  function schedule() {
    clearTimeout(timer);
    if (pending && Date.now() - pending.at < 30 * 60 * 1000) timer = setTimeout(check, 15000);
    else if (pending) status("완료 확인이 지연되고 있습니다. 실행 내역을 확인하거나 상태 확인을 눌러 주세요.", true);
  }

  async function check() {
    if (!pending || polling || submitting) return;
    polling = true;
    clearTimeout(timer);
    field("check").disabled = true;
    try {
      const result = await api("/runs?per_page=50");
      const run = result.workflow_runs.find(item => pending.runId ? item.id === pending.runId
        : item.event === "workflow_dispatch" && item.display_title === `Candidate scan ${pending.id}`);
      if (!run) { status("실행 요청 접수 · GitHub 실행 대기 중"); return; }
      savePending({...pending, runId: run.id});
      showRun(run.id);
      if (run.status !== "completed") {
        status(run.status === "in_progress" ? "A-G·A-K·A-H 검색·등록·배포 진행 중" : "다른 작업 완료 후 실행 대기 중");
        return;
      }
      if (run.conclusion !== "success") {
        status(`검색·배포 완료 실패 (${run.conclusion}) · 실행 내역을 확인해 주세요.`, true);
        savePending(null);
        return;
      }
      status("검색·배포 성공 · 앱 반영 확인 중");
      const response = await fetch(`data/candidate-monitor.json?scan=${Date.now()}`, {cache: "no-store"});
      if (!response.ok) throw new Error("배포 데이터 확인이 지연되고 있습니다.");
      const payload = await response.json();
      const report = payload.candidateScan;
      const sameRun = String(report?.runId) === String(run.id);
      if (!sameRun && !(Date.parse(report?.startedAt) >= Date.parse(run.updated_at))) return;
      await options.loadData();
      refresh(payload);
      status(sameRun ? `검색·등록 완료 · 신규 ${report.newRegistrations.length}종목 추가`
        : "검색·등록 완료 · 이후 실행된 최신 검색 결과를 표시합니다.");
      savePending(null);
    } catch (error) {
      status(`${error.message} · 요청을 다시 보내지 않고 상태만 재확인합니다.`, true);
    } finally {
      polling = false;
      field("check").disabled = false;
      schedule();
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (pending || submitting) return;
    submitting = true;
    field("submit").disabled = true;
    showRun(null);
    try {
      const result = await api("/runs?per_page=20");
      const active = result.workflow_runs.find(item => item.status !== "completed");
      if (active) {
        savePending({at: Date.now(), runId: active.id});
        status("이미 대상종목 검색이 실행 중입니다. 기존 실행을 확인합니다.");
        showRun(active.id);
      } else {
        const id = crypto.randomUUID();
        // Persist before POST: a lost response must not cause a duplicate dispatch.
        savePending({at: Date.now(), id});
        status("검색 실행 요청 중");
        try {
          await api("/dispatches", {method: "POST", body: JSON.stringify({ref: "main", inputs: {request_id: id}})});
        } catch (error) {
          if (/권한|실패 \(4\d\d\)/.test(error.message)) savePending(null);
          throw error;
        }
        status("실행 요청 접수 · 검색·등록·배포 완료를 기다리는 중");
      }
      write(options.tokenKey, field("token").value.trim());
    } catch (error) {
      status(pending ? `${error.message} · 요청 접수 여부는 상태 확인으로 확인해 주세요.` : error.message, true);
      field("connection").open = true;
    } finally {
      submitting = false;
      field("submit").disabled = Boolean(pending);
      schedule();
    }
  }

  function open(config) {
    options = config;
    if (!panel) {
      panel = document.createElement("section");
      panel.className = "candidate-scan";
      panel.setAttribute("aria-label", "대상종목 검색 및 추가");
      panel.innerHTML = `<h2>대상종목 검색·추가</h2>
        <dl class="candidate-scan-summary">
          <div><dt>자동 검색 예약</dt><dd>평일 08:30 · 16:40 KST (실행 지연 가능)</dd></div>
          <div><dt>마지막 검색 완료</dt><dd data-scan="last"></dd></div>
          <div><dt>일봉 기준일</dt><dd data-scan="basis"></dd></div>
          <div><dt>검색 대상</dt><dd data-scan="checked"></dd></div>
          <div><dt>현재 관리</dt><dd data-scan="counts"></dd></div>
          <div><dt>마지막 검색 신규 등록</dt><dd data-scan="added"></dd></div>
        </dl>
        <p data-scan="dataNote" class="muted"></p><ul data-scan="newList"></ul>
        <form data-scan="form">
          <details data-scan="connection"><summary>GitHub 연결</summary>
            <label>연결키<input data-scan="token" type="password" autocomplete="off"></label>
            <p class="muted">이 저장소의 Actions 읽기·쓰기 권한</p>
          </details>
          <div class="candidate-scan-actions">
            <button data-scan="submit" class="external-link" type="submit">A-G·A-K·A-H 검색 및 추가</button>
            <button data-scan="check" class="external-link" type="button" hidden>상태 확인</button>
            <a data-scan="run" target="_blank" rel="noopener noreferrer" hidden>실행 내역</a>
          </div>
          <p data-scan="status" role="status" aria-live="polite"></p>
        </form>
        <h3>최근 대상종목 등록</h3><ul data-scan="recent" class="candidate-scan-recent"></ul>`;
      field("form").addEventListener("submit", submit);
      field("check").addEventListener("click", () => { if (pending) pending.at = Date.now(); check(); });
      try { pending = JSON.parse(read(PENDING_KEY)); } catch { pending = null; }
      if (!pending || !Number.isFinite(pending.at) || (!pending.runId && !pending.id)) pending = null;
    }
    field("token").value = read(options.tokenKey) || field("token").value;
    field("connection").open = !field("token").value;
    savePending(pending);
    refresh();
    options.openOperationContent(config.button, panel);
    if (pending) check();
  }

  window.CandidateScan = {open, refresh};
})();
