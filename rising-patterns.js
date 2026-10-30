(function (root) {
  "use strict";
  const REPOSITORY = "leesgab-ctrl/korea-stock-ma-screener";
  const BRANCH = "codex/study-images";
  const INDEX = "data/study-images.json";
  const TOKEN_KEY = "koreaStockMonitor.githubToken";
  const MAX_BYTES = 10 * 1024 * 1024;
  const TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"};

  class StoreError extends Error {
    constructor(message, status = 0, connectionRequired = [401, 403].includes(status)) {
      super(message); this.status = status; this.connectionRequired = connectionRequired;
    }
  }

  async function requestError(response) {
    let detail = "";
    try { detail = (await response.json())?.message || ""; } catch { /* Some failures have no JSON body. */ }
    const header = name => response.headers?.get(name);
    const limited = response.status === 429 || response.status === 403
      && (header("x-ratelimit-remaining") === "0" || header("retry-after")
        || /rate limit|abuse detection/i.test(detail));
    if (limited) {
      const retry = Number(header("retry-after"));
      const reset = Number(header("x-ratelimit-reset"));
      const seconds = retry > 0 ? retry : reset > 0 ? Math.max(0, reset - Date.now() / 1000) : 0;
      const wait = seconds > 0 ? `${Math.max(1, Math.ceil(seconds / 60))}분 후` : "잠시 후";
      return new StoreError(`GitHub API 사용 한도에 도달했습니다. ${wait} 다시 등록해 주세요. 토큰 권한을 바꿀 필요는 없습니다. 입력한 내용은 유지됩니다.`, response.status, false);
    }
    const message = response.status === 401
      ? "이 브라우저의 GitHub 토큰이 만료되었거나 올바르지 않습니다. 유효한 토큰을 입력해 주세요. 입력한 내용은 유지됩니다."
      : response.status === 403 && /resource not accessible|insufficient permission/i.test(detail)
        ? "이 브라우저의 토큰에 이미지 저장 권한이 없습니다. korea-stock-ma-screener 저장소의 Contents 읽기·쓰기가 필요합니다. PC와 휴대폰의 연결 토큰은 따로 저장됩니다. 입력한 내용은 유지됩니다."
        : response.status === 403
          ? "GitHub가 요청을 거절했습니다 (403). 이 브라우저의 토큰 권한 또는 GitHub 접근 제한을 확인해 주세요. 입력한 내용은 유지됩니다."
          : `게시판 요청에 실패했습니다 (${response.status}). 입력한 내용은 유지됩니다.`;
    return new StoreError(message, response.status);
  }

  function validateIndex(value) {
    if (value?.version !== 1 || !Array.isArray(value.records)) throw new StoreError("게시판 자료 형식을 확인할 수 없습니다.");
    const ids = new Set();
    for (const item of value.records) {
      if (typeof item.id !== "string" || !/^[a-zA-Z0-9-]{8,80}$/.test(item.id) || ids.has(item.id)
          || typeof item.title !== "string" || !item.title.trim() || item.title.length > 80
          || !/^data\/study-images\/[a-zA-Z0-9-]{8,80}\.(png|jpg|webp)$/.test(item.imagePath)
          || !TYPES[item.mime] || typeof item.note !== "string" || item.note.length > 2000
          || typeof item.stock !== "string" || item.stock.length > 80
          || item.conditionConfirmed !== undefined && typeof item.conditionConfirmed !== "boolean"
          || item.conditionConfirmedAt != null && !Number.isFinite(Date.parse(item.conditionConfirmedAt))
          || typeof item.chartDate !== "string" || item.chartDate && !/^\d{4}-\d{2}-\d{2}$/.test(item.chartDate)
          || !Number.isFinite(Date.parse(item.createdAt)) || !Number.isFinite(Date.parse(item.updatedAt))) {
        throw new StoreError("게시판 자료가 올바르지 않아 덮어쓰지 않았습니다.");
      }
      ids.add(item.id);
    }
    return value;
  }

  function applyChange(index, change) {
    validateIndex(index);
    const records = index.records.map(item => ({...item}));
    const at = records.findIndex(item => item.id === change.record.id);
    const previous = records[at];
    if (change.expectedRevision == null ? Boolean(previous)
        : !previous || (previous.revision || previous.updatedAt) !== change.expectedRevision) {
      throw new StoreError("다른 기기에서 이 게시물이 변경됐습니다. 목록을 새로고침한 뒤 다시 확인해 주세요.", 409);
    }
    if (at < 0 && records.length >= 300) throw new StoreError("게시판 보관 한도는 휴지통을 포함해 300개입니다.");
    if (at < 0) records.push({...change.record});
    else records[at] = {...change.record};
    return validateIndex({version: 1, records});
  }

  class StudyImageStore {
    constructor({fetcher = root.fetch?.bind(root), token = () => ""} = {}) {
      this.fetcher = fetcher;
      this.token = token;
    }
    async request(path, {method = "GET", body, raw = false, allowMissing = false} = {}) {
      const token = this.token().trim();
      const headers = {Accept: raw ? "application/vnd.github.raw+json" : "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10"};
      if (token) headers.Authorization = `Bearer ${token}`;
      if (body) headers["Content-Type"] = "application/json";
      let response;
      try {
        response = await this.fetcher(`https://api.github.com/repos/${REPOSITORY}/${path}`,
          {method, headers, body: body ? JSON.stringify(body) : undefined, cache: "no-store"});
      } catch { throw new StoreError("GitHub 연결이 끊겼습니다. 입력 내용은 유지되니 연결 후 다시 확인해 주세요."); }
      if (response.status === 404 && allowMissing) return null;
      if (!response.ok) {
        throw await requestError(response);
      }
      return response.status === 204 ? null : response.json();
    }
    async read() {
      const ref = await this.request(`git/ref/heads/${BRANCH}`, {allowMissing: true});
      if (!ref) return {index: {version: 1, records: []}, revision: null};
      const revision = ref.object.sha;
      const index = await this.request(`contents/${INDEX}?ref=${encodeURIComponent(revision)}`, {raw: true, allowMissing: true});
      return {index: validateIndex(index || {version: 1, records: []}), revision};
    }
    async ensureBranch() {
      const existing = await this.request(`git/ref/heads/${BRANCH}`, {allowMissing: true});
      if (existing) return;
      const main = await this.request("git/ref/heads/main");
      try {
        await this.request("git/refs", {method: "POST", body: {ref: `refs/heads/${BRANCH}`, sha: main.object.sha}});
      } catch (error) {
        if (![409, 422].includes(error.status)) throw error;
        if (!await this.request(`git/ref/heads/${BRANCH}`, {allowMissing: true})) throw error;
      }
    }
    async save(change, image = null) {
      if (!this.token().trim()) throw new StoreError("이미지를 저장하려면 GitHub 연결이 필요합니다.", 401);
      await this.ensureBranch();
      let blob;
      for (let attempt = 0; attempt < 3; attempt++) {
        const current = await this.read();
        const index = applyChange(current.index, change);
        if (image && !blob) blob = await this.request("git/blobs", {method: "POST", body: {content: image.base64, encoding: "base64"}});
        const parent = await this.request(`git/commits/${current.revision}`);
        // A dedicated branch and a base tree keep monitoring files and simultaneous edits intact.
        const entries = [{path: INDEX, mode: "100644", type: "blob", content: JSON.stringify(index, null, 2) + "\n"}];
        if (image) entries.push({path: change.record.imagePath, mode: "100644", type: "blob", sha: blob.sha});
        const tree = await this.request("git/trees", {method: "POST", body: {base_tree: parent.tree.sha, tree: entries}});
        const commit = await this.request("git/commits", {method: "POST", body: {
          message: "Update study image board", tree: tree.sha, parents: [current.revision]}});
        try {
          await this.request(`git/refs/heads/${BRANCH}`, {method: "PATCH", body: {sha: commit.sha, force: false}});
          return {index, revision: commit.sha};
        } catch (error) {
          try {
            const saved = await this.read();
            if (saved.index.records.some(item => item.id === change.record.id && item.revision === change.record.revision)) return saved;
          } catch { /* The caller keeps its draft when a successful save cannot be confirmed. */ }
          if (![409, 422].includes(error.status) || attempt === 2) throw error;
        }
      }
    }
    imageUrl(record, revision) {
      if (!/^[a-f0-9]{40}$/.test(revision || "")) throw new StoreError("이미지 저장 위치를 확인하지 못했습니다.");
      return `https://raw.githubusercontent.com/${REPOSITORY}/${revision}/${record.imagePath}`;
    }
  }

  const api = {StudyImageStore, StoreError, applyChange, validateIndex, MAX_BYTES, TYPES, BRANCH};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  if (!root.document || new URLSearchParams(root.location.search).get("inlineChart") === "1"
      || new URLSearchParams(root.location.search).get("historyChart") === "1") return;

  const board = {records: [], revision: null, loaded: false, loading: false, busy: false,
    trash: false, draft: null, editing: null, creatingId: null, previewUrl: null, lastLoadedAt: 0, generation: 0, imageGeneration: 0, ruleLinks: []};
  let ui;
  const token = () => { try { return root.localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } };
  const store = new StudyImageStore({token: () => ui ? ui.token.value : token()});
  root.RisingPatterns = {get count() { return board.records.filter(item => !item.deletedAt).length; },
    get confirmed() { return board.records.filter(item => !item.deletedAt && item.conditionConfirmed === true).map(item => {
      const rule = board.ruleLinks.find(rule => rule.enabled && rule.imageIds?.includes(item.id));
      const thumbnail = rule?.thumbnail;
      const cropUrl = crop => /^data\/pattern-icons\/[a-zA-Z0-9-]+\.png$/.test(crop?.path || "")
        && Number.isFinite(crop.width) && crop.width > 0 && Number.isFinite(crop.height) && crop.height > 0
        ? new URL(crop.path, root.location.href).href : null;
      const thumbnailUrl = cropUrl(thumbnail);
      const imageUrl = store.imageUrl(item, board.revision);
      return {id: item.id, title: item.title, stock: item.stock, imageUrl,
        thumbnailUrl: thumbnailUrl || imageUrl,
        stageThumbnailUrls: Object.fromEntries(Object.entries(rule?.stageThumbnails || {}).map(([stage, crop]) => [stage, cropUrl(crop)])),
        width: thumbnailUrl ? thumbnail.width : item.width, height: thumbnailUrl ? thumbnail.height : item.height};
    }); },
    preview(id) {
      const item = board.records.find(item => item.id === id && !item.deletedAt && item.conditionConfirmed === true);
      if (ui && item) preview(item);
    },
    setRuleLinks(rules) {
      if (JSON.stringify(board.ruleLinks) === JSON.stringify(rules)) return;
      board.ruleLinks = rules;
      if (ui) renderGallery();
    },
    open() { if (ui && !board.loading && (!board.loaded || Date.now() - board.lastLoadedAt > 60000)) refresh(); }, refresh};

  function status(message, error = false) {
    ui.status.textContent = message;
    ui.status.classList.toggle("is-error", error);
  }
  function rememberConnection() {
    try { root.localStorage.setItem(TOKEN_KEY, ui.token.value.trim()); return true; }
    catch { return false; }
  }
  function savedStatus(message) {
    status(message + (rememberConnection() ? "" : " 연결 정보를 이 브라우저에 저장하지 못했습니다."));
  }
  function updateBusy(value) {
    board.busy = value;
    document.getElementById("captureNew").disabled = value;
    ui.form.querySelectorAll("input, textarea, button").forEach(item => item.disabled = value);
    ui.save.disabled = value || !board.draft && !board.editing;
    ui.gallery.querySelectorAll(".capture-item-actions button, .capture-confirmation input").forEach(item => item.disabled = value);
  }
  function releaseDraft() {
    if (board.previewUrl) URL.revokeObjectURL(board.previewUrl);
    board.previewUrl = null;
    board.draft = null;
  }
  function resetForm() {
    board.imageGeneration++;
    releaseDraft();
    board.editing = null;
    board.creatingId = null;
    ui.form.reset();
    ui.form.hidden = true;
    ui.image.hidden = true;
    ui.image.removeAttribute("src");
    ui.placeholder.hidden = false;
    ui.fileMeta.textContent = "";
    ui.save.textContent = "등록";
    ui.save.disabled = true;
  }
  function dateText(value) {
    return new Intl.DateTimeFormat("ko-KR", {timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(value));
  }
  function setRecords(result) {
    board.records = result.index.records;
    board.revision = result.revision;
    board.loaded = true;
    board.lastLoadedAt = Date.now();
    renderGallery();
    root.document.dispatchEvent(new CustomEvent("rising-patterns-change"));
  }
  async function refresh() {
    if (!ui || board.loading || board.busy) return;
    board.loading = true;
    const generation = ++board.generation;
    try {
      const result = await store.read();
      if (generation === board.generation) {
        setRecords(result);
        if (ui.status.classList.contains("is-error")) status("");
      }
    } catch (error) { status(error.message || "게시판 목록을 불러오지 못했습니다.", true); }
    finally { board.loading = false; }
  }
  function fileData(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new StoreError("이미지 파일을 읽지 못했습니다."));
      reader.readAsDataURL(file);
    });
  }
  async function chooseImage(file) {
    if (!file || board.busy) return;
    if (!TYPES[file.type]) { status("PNG, JPG, WebP 이미지만 등록할 수 있습니다.", true); return; }
    if (!file.size || file.size > MAX_BYTES) { status("이미지는 10MB 이하로 선택해 주세요.", true); return; }
    const url = URL.createObjectURL(file);
    const generation = ++board.imageGeneration;
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      if (generation !== board.imageGeneration) { URL.revokeObjectURL(url); return; }
      if (image.naturalWidth * image.naturalHeight > 40000000) throw new StoreError("이미지 해상도가 너무 큽니다. 4천만 화소 이하로 선택해 주세요.");
      releaseDraft();
      board.previewUrl = url;
      board.draft = {file, width: image.naturalWidth, height: image.naturalHeight};
      ui.image.src = url;
      ui.form.hidden = false;
      ui.image.hidden = false;
      ui.placeholder.hidden = true;
      ui.fileMeta.textContent = `${image.naturalWidth} × ${image.naturalHeight} · ${(file.size / 1024).toFixed(0)}KB`;
      if (!ui.title.value) ui.title.value = file.name && file.name !== "image.png" ? file.name.replace(/\.[^.]+$/, "").slice(0, 80) : `공부 이미지 ${dateText(new Date().toISOString())}`;
      ui.save.disabled = false;
      status("");
    } catch (error) {
      URL.revokeObjectURL(url);
      status(error instanceof StoreError ? error.message : "이미지를 열 수 없습니다. 다른 파일을 선택해 주세요.", true);
    } finally { ui.file.value = ""; }
  }
  async function pasteImage() {
    try {
      if (!navigator.clipboard?.read) throw new Error("unsupported");
      const items = await navigator.clipboard.read();
      for (const item of items) {
        const type = item.types.find(value => TYPES[value]);
        if (type) { await chooseImage(await item.getType(type)); return; }
      }
      status("클립보드에 이미지가 없습니다.", true);
    } catch { status("클립보드 이미지를 읽지 못했습니다. 이미지 선택 또는 Ctrl+V를 사용해 주세요.", true); }
  }
  function preview(item) {
    ui.previewTitle.textContent = item.title;
    ui.previewImage.src = store.imageUrl(item, board.revision);
    ui.previewImage.alt = item.title;
    ui.previewNote.textContent = item.note;
    ui.preview.showModal();
  }
  function edit(item) {
    if (board.busy) return;
    resetForm();
    board.editing = {...item};
    ui.form.hidden = false;
    ui.title.value = item.title;
    ui.stock.value = item.stock;
    ui.date.value = item.chartDate;
    ui.note.value = item.note;
    ui.confirmed.checked = item.conditionConfirmed === true;
    ui.image.src = store.imageUrl(item, board.revision);
    ui.image.hidden = false;
    ui.placeholder.hidden = true;
    ui.fileMeta.textContent = `${item.width} × ${item.height}`;
    ui.save.textContent = "수정 저장";
    ui.save.disabled = false;
    ui.form.scrollIntoView({block: "start", behavior: "smooth"});
    ui.title.focus({preventScroll: true});
    status("");
  }
  async function moveToTrash(item, restore = false) {
    if (board.busy) return;
    updateBusy(true);
    status(restore ? "복원 중…" : "휴지통으로 이동 중…");
    try {
      const record = {...item, deletedAt: restore ? null : new Date().toISOString(),
        updatedAt: new Date().toISOString(), revision: crypto.randomUUID()};
      const result = await store.save({record, expectedRevision: item.revision || item.updatedAt});
      setRecords(result);
      if (board.editing?.id === item.id) resetForm();
      savedStatus(restore ? "이미지를 복원했습니다." : "휴지통으로 이동했습니다.");
    } catch (error) {
      status(error.message || "변경 내용을 저장하지 못했습니다.", true);
      if (error.connectionRequired) ui.connection.open = true;
    } finally { updateBusy(false); }
  }
  async function confirmCondition(item, confirmed) {
    if (board.busy) return;
    updateBusy(true);
    status("포착조건 확정 상태 저장 중…");
    try {
    const record = {...item, conditionConfirmed: confirmed, conditionConfirmedAt: confirmed ? new Date().toISOString() : null,
        updatedAt: new Date().toISOString(), revision: crypto.randomUUID()};
      const result = await store.save({record, expectedRevision: item.revision || item.updatedAt});
      setRecords(result);
      savedStatus(confirmed ? "포착조건으로 확정했습니다. 연결된 검색조건은 다음 서버 갱신부터 적용됩니다." : "포착조건 확정을 해제했습니다.");
    } catch (error) {
      status(error.message || "확정 상태를 저장하지 못했습니다.", true);
      if (error.connectionRequired) ui.connection.open = true;
      renderGallery();
    } finally { updateBusy(false); }
  }
  function renderGallery() {
    const active = board.records.filter(item => !item.deletedAt);
    ui.count.textContent = active.length;
    ui.trashCount.textContent = board.records.length - active.length;
    const keyword = ui.search.value.trim().toLocaleLowerCase();
    const items = board.records.filter(item => Boolean(item.deletedAt) === board.trash
      && `${item.title} ${item.stock} ${item.note}`.toLocaleLowerCase().includes(keyword))
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    ui.gallery.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "empty-list";
      empty.textContent = keyword ? "검색 결과가 없습니다." : board.trash ? "휴지통이 비어 있습니다." : "등록된 이미지가 없습니다.";
      ui.gallery.append(empty);
      return;
    }
    for (const item of items) {
      const article = document.createElement("article");
      article.className = "capture-item";
      article.dataset.id = item.id;
      article.innerHTML = '<button class="capture-image-button" type="button"><img loading="lazy"></button><div class="capture-item-content"><h3></h3><p class="capture-item-meta"></p><p class="capture-item-note"></p><div class="capture-item-actions"></div></div>';
      const imageButton = article.querySelector(".capture-image-button");
      imageButton.title = `${item.title} 크게 보기`;
      imageButton.setAttribute("aria-label", `${item.title} 크게 보기`);
      const image = imageButton.querySelector("img");
      image.src = store.imageUrl(item, board.revision);
      image.alt = item.title;
      imageButton.addEventListener("click", () => preview(item));
      article.querySelector("h3").textContent = item.title;
      article.querySelector(".capture-item-meta").textContent = [item.stock, item.chartDate, `등록 ${dateText(item.createdAt)}`].filter(Boolean).join(" · ");
      article.querySelector(".capture-item-note").textContent = item.note;
      if (!board.trash) {
        const label = document.createElement("label");
        label.className = "capture-confirmation";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = item.conditionConfirmed === true;
        checkbox.disabled = board.busy;
        checkbox.addEventListener("change", () => confirmCondition(item, checkbox.checked));
        label.append(checkbox, document.createTextNode("포착조건 확정"));
        article.querySelector(".capture-item-content").insertBefore(label, article.querySelector(".capture-item-actions"));
        const links = board.ruleLinks.filter(rule => rule.imageIds?.includes(item.id) && rule.enabled);
        const linkState = document.createElement("p");
        linkState.className = "capture-rule-state";
        const liveLinks = links.filter(rule => rule.active);
        const activeLabel = liveLinks.some(rule => rule.notificationsEnabled !== false)
          ? " · 검색·알림 사용 중" : " · 검색 사용 · 문자 중지";
        linkState.textContent = links.length ? links.map(rule => rule.name).join(" · ")
          + (item.conditionConfirmed ? liveLinks.length ? activeLabel : " · 다음 서버 갱신 적용" : " · 확정 체크 대기")
          : "검색조건 설정 대기";
        article.querySelector(".capture-item-content").insertBefore(linkState, article.querySelector(".capture-item-actions"));
      }
      const action = (label, callback) => {
        const button = document.createElement("button");
        button.type = "button"; button.className = "external-link"; button.textContent = label;
        button.disabled = board.busy;
        button.addEventListener("click", callback);
        article.querySelector(".capture-item-actions").append(button);
      };
      if (board.trash) action("복원", () => moveToTrash(item, true));
      else { action("수정", () => edit(item)); action("휴지통", () => moveToTrash(item)); }
      ui.gallery.append(article);
    }
  }
  async function submit(event) {
    event.preventDefault();
    if (board.busy || !board.draft && !board.editing) return;
    const title = ui.title.value.trim();
    if (!title) { ui.title.focus(); return; }
    updateBusy(true);
    status("공개 게시판에 저장 중…");
    try {
      const now = new Date().toISOString();
      if (!board.editing && !board.creatingId) board.creatingId = crypto.randomUUID();
      const record = {...(board.editing || {}), id: board.editing?.id || board.creatingId,
        title, stock: ui.stock.value.trim(), chartDate: ui.date.value, note: ui.note.value.trim(), conditionConfirmed: ui.confirmed.checked,
        conditionConfirmedAt: ui.confirmed.checked ? (board.editing?.conditionConfirmed ? board.editing.conditionConfirmedAt || board.editing.updatedAt : now) : null,
        createdAt: board.editing?.createdAt || now, updatedAt: now, revision: crypto.randomUUID(), deletedAt: null};
      let image = null;
      if (board.draft) {
        const {file, width, height} = board.draft;
        Object.assign(record, {imagePath: `data/study-images/${crypto.randomUUID()}.${TYPES[file.type]}`,
          mime: file.type, bytes: file.size, width, height});
        image = {base64: await fileData(file)};
      }
      const result = await store.save({record, expectedRevision: board.editing ? board.editing.revision || board.editing.updatedAt : null}, image);
      setRecords(result);
      resetForm();
      savedStatus("저장했습니다. PC와 휴대폰에서 같은 게시판을 볼 수 있습니다.");
    } catch (error) {
      status(error.message || "이미지를 저장하지 못했습니다. 입력한 내용은 유지됩니다.", true);
      if (error.connectionRequired) ui.connection.open = true;
    } finally { updateBusy(false); }
  }
  function initialize() {
    const get = id => document.getElementById(`capture${id}`);
    ui = {form: get("Form"), dropzone: get("Dropzone"), file: get("File"), image: get("DraftImage"), placeholder: get("DraftPlaceholder"),
      title: get("Title"), stock: get("Stock"), date: get("Date"), note: get("Note"), confirmed: get("Confirmed"), save: get("Save"), fileMeta: get("FileMeta"),
      status: get("Status"), count: get("Count"), trashCount: get("TrashCount"), gallery: get("Gallery"), search: get("Search"),
      connection: get("Connection"), token: get("Token"), preview: get("PreviewDialog"), previewImage: get("PreviewImage"),
      previewTitle: get("PreviewTitle"), previewNote: get("PreviewNote")};
    if (!ui.form) return;
    get("New").addEventListener("click", () => { if (!board.busy) { ui.form.hidden = false; ui.file.click(); } });
    get("Choose").addEventListener("click", () => ui.file.click());
    ui.dropzone.addEventListener("click", () => { if (!board.busy) ui.file.click(); });
    ui.dropzone.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (!board.busy) ui.file.click(); }
    });
    ui.file.addEventListener("change", () => chooseImage(ui.file.files[0]));
    for (const type of ["dragenter", "dragover"]) ui.dropzone.addEventListener(type, event => {
      event.preventDefault(); if (!board.busy) ui.dropzone.classList.add("is-dragging");
    });
    for (const type of ["dragleave", "drop"]) ui.dropzone.addEventListener(type, event => {
      event.preventDefault(); ui.dropzone.classList.remove("is-dragging");
      if (type === "drop") chooseImage(event.dataTransfer.files[0]);
    });
    document.addEventListener("paste", event => {
      if (document.getElementById("capturePanel").classList.contains("hidden") || board.busy) return;
      const file = [...(event.clipboardData?.files || [])].find(file => file.type.startsWith("image/"));
      if (file) { event.preventDefault(); chooseImage(file); }
    });
    get("Paste").addEventListener("click", pasteImage);
    get("Cancel").addEventListener("click", () => { if (!board.busy) { resetForm(); status(""); } });
    ui.form.addEventListener("submit", submit);
    ui.search.addEventListener("input", renderGallery);
    for (const [id, trash] of [["Active", false], ["Trash", true]]) get(id).addEventListener("click", () => {
      board.trash = trash;
      get("Active").setAttribute("aria-selected", String(!trash));
      get("Trash").setAttribute("aria-selected", String(trash));
      renderGallery();
    });
    get("PreviewClose").addEventListener("click", () => ui.preview.close());
    ui.preview.addEventListener("click", event => { if (event.target === ui.preview) ui.preview.close(); });
    ui.token.value = token();
    get("Connect").addEventListener("click", () => {
      const saved = rememberConnection();
      status(saved ? "연결 정보를 이 브라우저에 저장했습니다. 실제 저장 권한은 이미지 등록 시 확인됩니다."
        : "연결 정보를 저장하지 못했습니다. 브라우저 저장 설정을 확인해 주세요.", !saved);
    });
    root.addEventListener("beforeunload", event => {
      if (board.draft || board.busy) { event.preventDefault(); event.returnValue = ""; }
    });
    refresh();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, {once: true});
  else initialize();
})(typeof window === "undefined" ? globalThis : window);
