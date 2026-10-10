function aggregatePriceAnalysis(payload, days, today) {
  const dates = payload.dates.filter(date => date < today).slice(-days);
  const rows = payload.records.filter(row => dates.includes(row.date));
  const lookup = new Map(payload.records.map(row => [row.id + '|' + row.date, row]));
  const groups = new Map();
  let missing = 0, unchanged = 0;
  for (const row of rows) {
    const priorDate = payload.dates[payload.dates.indexOf(row.date) - 1];
    const prior = lookup.get(row.id + '|' + priorDate);
    if (!row.color || !prior?.color || row.ruleVersion !== prior.ruleVersion || !Number.isFinite(row.change)) { missing += 1; continue; }
    if (prior.color === row.color) { unchanged += 1; continue; }
    const key = prior.color + '|' + row.color;
    if (!groups.has(key)) groups.set(key, { from: prior.color, to: row.color, rows: [] });
    groups.get(key).rows.push(row);
  }
  return { dates, total: rows.length, missing, unchanged, groups: [...groups.values()].map(group => ({...group,
    average: group.rows.reduce((sum, row) => sum + row.change, 0) / group.rows.length,
    weight: rows.length ? group.rows.length / rows.length * 100 : 0,
    largeGains: group.rows.filter(row => row.change >= 20 - 1e-10).length,
  })).sort((a, b) => b.average - a.average) };
}

async function renderPriceAnalysis() {
  const content = document.createElement('section');
  content.className = 'price-analysis';
  content.textContent = '주가분석을 불러오는 중입니다.';
  openOperationContent(document.querySelector('#priceAnalysisButton'), content);
  try {
    const response = await fetch(`data/price-analysis.json?t=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error('마감 분석 기록이 아직 없습니다.');
    const data = await response.json();
    content.replaceChildren();
    const title = document.createElement('h2');
    title.textContent = '주가분석';
    content.append(title);
    const note = document.createElement('p');
    note.className = 'analysis-note';
    note.textContent = '전일 마감까지 · 같은 색 유지 제외 · 진하기별 구분 · 당일 등락률이며 미래 수익률이 아닙니다.';
    content.append(note);
    const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Seoul'}).format(new Date());
    const swatch = color => {
      const span = document.createElement('span');
      span.className = 'analysis-swatch'; span.style.background = color; span.title = color;
      return span;
    };
    {
      const results = {5: aggregatePriceAnalysis(data, 5, today), 10: aggregatePriceAnalysis(data, 10, today)};
      const combined = new Map();
      for (const days of [5, 10]) for (const group of results[days].groups) {
        const key = group.from + '|' + group.to;
        if (!combined.has(key)) combined.set(key, {from: group.from, to: group.to});
        combined.get(key)[days] = group;
      }
      let sortDays = 5;
      const heading = document.createElement('h3');
      heading.textContent = '최근 5 · 10거래일';
      content.append(heading);
      const meta = document.createElement('p'); meta.className = 'analysis-note';
      meta.textContent = [5, 10].map(days => `${days}일 ${results[days].dates[0] || '-'} ~ ${results[days].dates.at(-1) || '-'} · 전체 ${results[days].total}건 · 자료 부족 ${results[days].missing}건`).join(' / ');
      content.append(meta);
      const table = document.createElement('table'); table.className = 'analysis-table';
      table.classList.add('analysis-combined');
      table.innerHTML = '<thead><tr><th rowspan="2">전일 →<br>해당일</th><th colspan="3">5거래일</th><th colspan="3">10거래일</th><th rowspan="2">20%이상<br>건수</th></tr><tr><th>건수</th><th><button type="button" data-sort-days="5">평균<br>등락률</button></th><th>비중</th><th>건수</th><th><button type="button" data-sort-days="10">평균<br>등락률</button></th><th>비중</th></tr></thead><tbody></tbody>';
      const body = table.querySelector('tbody');
      const renderRows = () => {
      body.replaceChildren();
      table.querySelectorAll('[data-sort-days]').forEach(button => {
        const active = Number(button.dataset.sortDays) === sortDays;
        button.setAttribute('aria-pressed', String(active));
        button.parentElement.setAttribute('aria-sort', active ? 'descending' : 'none');
      });
      for (const pair of [...combined.values()].sort((a, b) => (b[sortDays]?.average ?? -Infinity) - (a[sortDays]?.average ?? -Infinity) || (a.from + a.to).localeCompare(b.from + b.to))) {
        const row = document.createElement('tr');
        const colors = document.createElement('td'); colors.append(swatch(pair.from), document.createTextNode(' → '), swatch(pair.to));
        row.append(colors);
        for (const days of [5, 10]) {
        const group = pair[days] || {rows: [], average: null, weight: 0};
        const count = document.createElement('td');
        const detail = document.createElement('button'); detail.type = 'button'; detail.className = 'analysis-detail';
        detail.textContent = `${group.rows.length}건`; detail.title = `${days}거래일 상세`; detail.disabled = !group.rows.length; detail.setAttribute('aria-expanded', 'false'); count.append(detail);
        const average = document.createElement('td'); average.textContent = group.average == null ? '-' : `${group.average >= 0 ? '+' : ''}${group.average.toFixed(1)}%`;
        average.className = group.average >= 0 ? 'positive' : 'negative';
        const weight = document.createElement('td'); weight.textContent = `${group.weight.toFixed(1)}%`;
        row.append(count, average, weight);
        detail.addEventListener('click', () => {
          if (row.nextElementSibling?.classList.contains('analysis-details-row')) {
            const same = row.nextElementSibling.dataset.days === String(days);
            row.nextElementSibling.remove();
            row.querySelectorAll('.analysis-detail').forEach(button => button.setAttribute('aria-expanded', 'false'));
            if (same) return;
          }
          const expanded = document.createElement('tr'); expanded.className = 'analysis-details-row';
          expanded.dataset.days = days;
          const cell = document.createElement('td'); cell.colSpan = 8;
          const list = document.createElement('table'); list.className = 'analysis-table analysis-stocks';
          list.innerHTML = '<thead><tr><th>종목명</th><th>유형</th><th>날짜</th><th>최종가격</th><th>등락률</th></tr></thead><tbody></tbody>';
          for (const record of [...group.rows].sort((a, b) => b.date.localeCompare(a.date))) {
            const stock = document.createElement('tr');
            const name = document.createElement('td'); const button = document.createElement('button');
            button.type = 'button'; button.className = 'history-stock-button'; button.textContent = record.name; button.setAttribute('aria-expanded', 'false'); name.append(button); stock.append(name);
            for (const value of [record.type, record.date.slice(5), `${Math.round(record.close).toLocaleString('ko-KR')}원`, `${record.change >= 0 ? '+' : ''}${record.change.toFixed(1)}%`]) {
              const field = document.createElement('td'); field.textContent = value; stock.append(field);
            }
            stock.lastElementChild.className = record.change >= 0 ? 'positive' : 'negative';
            list.querySelector('tbody').append(stock);
            button.addEventListener('click', () => {
              if (stock.nextElementSibling?.classList.contains('analysis-chart-row')) { stock.nextElementSibling.remove(); button.setAttribute('aria-expanded', 'false'); return; }
              const item = [...(state.payload.history || []), ...(state.payload.candidates || [])].find(item => (item.id || item.code + '|' + item.registeredAt) === record.id);
              const chartRow = document.createElement('tr'); chartRow.className = 'analysis-chart-row';
              const chartCell = document.createElement('td'); chartCell.colSpan = 5;
              if (item) {
                const snapshot = structuredClone(item);
                snapshot.displayCharts ||= {};
                for (const [kind, key] of [['intraday', 't'], ['daily', 'd']]) {
                  const chart = snapshot.displayCharts[kind];
                  if (!chart) continue;
                  chart.series = [...new Map([...(chart.history || []), ...(chart.series || [])].map(row => [row[key], row])).values()]
                    .filter(row => row[key].slice(0, 10) <= record.date).sort((a, b) => a[key].localeCompare(b[key]));
                  chart.history = chart.series;
                }
                appendInlineChart(chartCell, snapshot, `analysis-${record.id}-${record.date}`);
              } else chartCell.textContent = '저장된 그래프 자료가 없습니다.';
              chartRow.append(chartCell); stock.after(chartRow); button.setAttribute('aria-expanded', 'true');
            });
          }
          cell.append(list); expanded.append(cell); row.after(expanded); detail.setAttribute('aria-expanded', 'true');
        });
        }
        const remark = document.createElement('td');
        const notes = [5, 10].map(days => `${days}일${pair[days]?.largeGains || 0}건`);
        remark.textContent = notes.join('/');
        remark.title = '전일 종가 대비 당일 종가가 20% 이상 상승한 종목·거래일 건수';
        row.append(remark); body.append(row);
      }
      };
      table.querySelectorAll('[data-sort-days]').forEach(button => button.addEventListener('click', () => { sortDays = Number(button.dataset.sortDays); renderRows(); }));
      renderRows();
      content.append(table);
      if (!combined.size) { const empty = document.createElement('p'); empty.textContent = '비교 가능한 색 전환 기록이 없습니다.'; content.append(empty); }
    }
  } catch (error) { content.textContent = error.message; }
}
