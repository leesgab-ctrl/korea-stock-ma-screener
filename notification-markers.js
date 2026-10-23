(() => {
  "use strict";
  const stageLabel = stage => ({watch: "1차 검토", primary: "1차", confirmation: "확인"}[stage] || "");
  const time = value => new Intl.DateTimeFormat("ko-KR", {timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit"}).format(new Date(value));

  function forCode(payload, positions, code) {
    const found = new Map();
    for (const item of [...(payload?.notificationHistory || []), ...(positions?.notificationHistory || [])]) {
      if (item.code !== code || !item.id || !Number.isFinite(Date.parse(item.barTime))
          || !Number.isFinite(Date.parse(item.sentAt)) || Date.parse(item.barTime) > Date.parse(item.sentAt)) continue;
      found.set(item.id, item);
    }
    return [...found.values()].sort((a, b) => Date.parse(a.sentAt) - Date.parse(b.sentAt));
  }

  function draw(records, series, x, y, pad, priceBottom) {
    const host = document.getElementById("notificationMarkers");
    const tooltip = document.getElementById("notificationTooltip");
    if (!host || !tooltip) return;
    host.replaceChildren();
    tooltip.hidden = true;
    const groups = new Map();
    for (const item of records) {
      const stamp = Date.parse(item.barTime);
      if (!groups.has(stamp)) groups.set(stamp, []);
      groups.get(stamp).push(item);
    }
    series.forEach((row, index) => {
      const entries = groups.get(Date.parse(row.t));
      if (!entries || !Number.isFinite(row.m3)) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "notification-marker";
      button.dataset.barTime = row.t;
      button.dataset.count = entries.length;
      button.style.left = `${x(index)}px`;
      button.style.top = `${y(row.m3)}px`;
      button.setAttribute("aria-label", `${time(row.t)} 기준봉 · ntfy 발송 ${entries.length}건`);
      button.title = entries.map(entry => `${entry.label} ${stageLabel(entry.stage)} · 발송 ${time(entry.sentAt)}`).join("\n");
      const show = () => {
        tooltip.replaceChildren();
        const heading = document.createElement("strong");
        heading.textContent = `ntfy 발송 ${entries.length}건`;
        tooltip.append(heading);
        const bar = document.createElement("div");
        bar.textContent = `기준봉 ${time(row.t)} 시작`;
        tooltip.append(bar);
        for (const entry of entries) {
          const line = document.createElement("div");
          line.className = "notification-tooltip-entry";
          line.textContent = `${entry.label} ${stageLabel(entry.stage)}\n발송 ${time(entry.sentAt)}`;
          tooltip.append(line);
        }
        tooltip.hidden = false;
        const wrap = host.parentElement;
        tooltip.style.left = `${Math.max(4, Math.min(x(index) + 12, wrap.clientWidth - tooltip.offsetWidth - 4))}px`;
        tooltip.style.top = `${Math.max(pad.top, Math.min(y(row.m3) + 12, wrap.clientHeight - tooltip.offsetHeight - 4))}px`;
      };
      button.addEventListener("click", show);
      button.addEventListener("pointerenter", event => { if (event.pointerType !== "touch") show(); });
      button.addEventListener("focus", show);
      button.addEventListener("keydown", event => { if (event.key === "Escape") tooltip.hidden = true; });
      host.append(button);
    });
  }

  document.addEventListener("pointerdown", event => {
    if (!event.target.closest?.(".notification-marker, #notificationTooltip")) {
      const tooltip = document.getElementById("notificationTooltip");
      if (tooltip) tooltip.hidden = true;
    }
  });
  window.NotificationMarkers = {forCode, draw};
})();
