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
    if (prior.color === row.color) unchanged += 1;
    const key = prior.color + '|' + Boolean(prior.compact) + '|' + row.color + '|' + Boolean(row.compact);
    if (!groups.has(key)) groups.set(key, { from: prior.color, to: row.color, fromCompact: prior.compact === true, toCompact: row.compact === true, rows: [] });
    const earlierDate = payload.dates[payload.dates.indexOf(row.date) - 2];
    const earlier = lookup.get(row.id + '|' + earlierDate);
    const volumeRatio = Number.isFinite(prior.volume) && prior.volume >= 0 && earlier?.volume > 0
      ? prior.volume / earlier.volume * 100 : null;
    groups.get(key).rows.push({...row, volumeRatio});
  }
  return { dates, total: rows.length, missing, unchanged, groups: [...groups.values()].map(group => ({...group,
    average: group.rows.reduce((sum, row) => sum + row.change, 0) / group.rows.length,
    volumeAverage: (() => {
      const valid = group.rows.filter(row => Number.isFinite(row.volumeRatio));
      return valid.length ? valid.reduce((sum, row) => sum + row.volumeRatio, 0) / valid.length : null;
    })(),
    weight: rows.length ? group.rows.length / rows.length * 100 : 0,
    largeGains: group.rows.filter(row => row.change >= 20 - 1e-10).length,
  })).sort((a, b) => b.average - a.average) };
}

function filterChartShapeAnalysis(payload, exclusions) {
  const codes = new Set(exclusions.filter(entry => entry.category === 'chart_shape'
    || (!entry.category && /차트\s*형태/.test(entry.reason || ''))).map(entry => entry.code));
  return {...payload, records: payload.records.filter(row => !codes.has(row.code))};
}

function sortPriceAnalysisPairs(pairs, days, grouped = false) {
  const scores = new Map();
  const identity = pair => pair.from + '|' + Boolean(pair.fromCompact);
  if (grouped) for (const pair of pairs) {
    const score = scores.get(identity(pair)) || {sum: 0, count: 0};
    for (const row of pair[5]?.rows || []) { score.sum += row.change; score.count += 1; }
    scores.set(identity(pair), score);
  }
  const average = color => {
    const score = scores.get(color);
    return score?.count ? score.sum / score.count : -Infinity;
  };
  return [...pairs].sort((a, b) => {
    if (grouped && identity(a) !== identity(b)) return average(identity(b)) - average(identity(a)) || identity(a).localeCompare(identity(b));
    return (b[days]?.average ?? -Infinity) - (a[days]?.average ?? -Infinity) || (a.from + a.to).localeCompare(b.from + b.to);
  });
}

async function renderPriceAnalysis(grouped = false) {
  const content = document.createElement('section');
  content.className = 'price-analysis';
  content.textContent = '주가분석을 불러오는 중입니다.';
  openOperationContent(document.querySelector(grouped ? '#shapeAnalysisButton' : '#priceAnalysisButton'), content);
  try {
    const response = await fetch(`data/price-analysis.json?t=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error('마감 분석 기록이 아직 없습니다.');
    const data = filterChartShapeAnalysis(await response.json(), state.payload.manualExclusions || []);
    content.replaceChildren();
    const title = document.createElement('h2');
    title.textContent = grouped ? '형태분석' : '주가분석';
    content.append(title);
    const note = document.createElement('p');
    note.className = 'analysis-note';
    note.textContent = '전일 마감까지 · 같은 색 유지 포함 · 진하기별 구분 · 당일 등락률이며 미래 수익률이 아닙니다.';
    content.append(note);
    const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Seoul'}).format(new Date());
    const currentDate = data.dates.filter(date => date < today).at(-1);
    const currentByColor = new Map();
    for (const item of state.payload.candidates || []) {
      const type = {reference: '상승', target: '조정'}[chartGroup(item)];
      if (!type || !currentDate || currentDate < item.registeredAt?.slice(0, 10)) continue;
      const id = item.id || item.code + '|' + item.registeredAt;
      const series = [...new Map([...(item.displayCharts?.intraday?.history || []), ...(item.displayCharts?.intraday?.series || [])].map(row => [row.t, row])).values()]
        .filter(row => row.complete !== false && row.t.slice(0, 10) <= currentDate).sort((a, b) => a.t.localeCompare(b.t));
      const last = series.at(-1);
      const saved = data.records.find(row => row.id === id && row.date === currentDate);
      const color = saved?.color || (last?.t.slice(0, 10) === currentDate && Date.parse(last.t) >= Date.parse(item.registeredAt)
        && [last.m3, last.m10, last.m20, last.m40, last.m60].every(value => value > 0) ? phaseBackground(chartPhases(series).at(-1)) : null);
      if (!color) continue;
      const quote = displayQuote(item);
      const record = {id, name: item.name, type, date: currentDate, close: quote.price, change: quote.change, current: true};
      const spread = last ? maximumMaSpread(last) : null;
      const compact = saved?.compact ?? (spread !== null && spread <= 0.8);
      const colorKey = color + '|' + Boolean(compact);
      if (!currentByColor.has(colorKey)) currentByColor.set(colorKey, []);
      currentByColor.get(colorKey).push(record);
    }
    const swatch = (color, compact) => {
      const span = document.createElement('span');
      span.className = 'analysis-swatch'; span.style.background = color; span.title = color;
      if (compact) { span.classList.add('ma-compact-swatch'); span.title += ' · 5개 MA 최대간격 0.8% 이하'; }
      return span;
    };
    {
      const results = {5: aggregatePriceAnalysis(data, 5, today), 10: aggregatePriceAnalysis(data, 10, today)};
      const combined = new Map();
      for (const days of [5, 10]) for (const group of results[days].groups) {
        const key = group.from + '|' + group.fromCompact + '|' + group.to + '|' + group.toCompact;
        if (!combined.has(key)) combined.set(key, {from: group.from, to: group.to, fromCompact: group.fromCompact, toCompact: group.toCompact});
        combined.get(key)[days] = group;
      }
      for (const pair of combined.values()) pair.current = {rows: currentByColor.get(pair.from + '|' + Boolean(pair.fromCompact)) || []};
      let sortDays = 5;
      const heading = document.createElement('h3');
      heading.textContent = '최근 5 · 10거래일';
      content.append(heading);
      const meta = document.createElement('p'); meta.className = 'analysis-note';
      meta.textContent = [5, 10].map(days => `${days}일 ${results[days].dates[0] || '-'} ~ ${results[days].dates.at(-1) || '-'} · 전체 ${results[days].total}건 · 자료 부족 ${results[days].missing}건`).join(' / ');
      meta.textContent += ` · 현재종목 색 기준 ${currentDate || '-'}`;
      content.append(meta);
      const table = document.createElement('table'); table.className = 'analysis-table';
      table.classList.add('analysis-combined');
      table.innerHTML = '<thead><tr><th rowspan="2">전일 →<br>해당일</th><th colspan="4">5거래일</th><th colspan="3">10거래일</th><th rowspan="2">비고</th><th rowspan="2">현재<br>종목</th></tr><tr><th>건수</th><th>(평균)<br>거래량%</th><th><button type="button" data-sort-days="5">등락률</button></th><th>비중</th><th>건수</th><th>(평균)<br>거래량%</th><th><button type="button" data-sort-days="10">등락률</button></th></tr></thead><tbody></tbody>';
      const body = table.querySelector('tbody');
      table.querySelector('thead tr:first-child th:last-child').classList.add('analysis-current-heading');
      const renderRows = () => {
      body.replaceChildren();
      table.querySelectorAll('[data-sort-days]').forEach(button => {
        const active = Number(button.dataset.sortDays) === sortDays;
        button.setAttribute('aria-pressed', String(active));
        button.parentElement.setAttribute('aria-sort', active ? 'descending' : 'none');
      });
      const ordered = sortPriceAnalysisPairs([...combined.values()], sortDays, grouped);
      for (const [index, pair] of ordered.entries()) {
        const row = document.createElement('tr');
        if (grouped && (ordered[index - 1]?.from !== pair.from || ordered[index - 1]?.fromCompact !== pair.fromCompact)) row.classList.add('analysis-group-start');
        if (grouped && (ordered[index + 1]?.from !== pair.from || ordered[index + 1]?.fromCompact !== pair.fromCompact)) row.classList.add('analysis-group-end');
        if (pair[5]?.average >= 4) row.classList.add('analysis-strong-return');
        const colors = document.createElement('td'); colors.append(swatch(pair.from, pair.fromCompact), document.createTextNode(' → '), swatch(pair.to, pair.toCompact));
        row.append(colors);
        for (const days of [5, 10, 'current']) {
        const group = pair[days] || {rows: [], average: null, weight: 0};
        const count = document.createElement('td');
        if (days === 'current') count.className = 'analysis-current-cell';
        const detail = document.createElement('button'); detail.type = 'button'; detail.className = 'analysis-detail';
        detail.textContent = `${group.rows.length}${days === 'current' ? '종목' : '건'}`; detail.title = days === 'current' ? '현재종목 상세' : `${days}거래일 상세`; detail.disabled = !group.rows.length; detail.setAttribute('aria-expanded', 'false'); count.append(detail);
        if (days === 'current' && !group.rows.length) detail.remove();
        const average = document.createElement('td'); average.textContent = group.average == null ? '-' : `${group.average >= 0 ? '+' : ''}${group.average.toFixed(1)}%`;
        average.className = group.average >= 0 ? 'positive' : 'negative';
        const weight = document.createElement('td'); weight.textContent = `${(group.weight || 0).toFixed(1)}%`;
        const volume = document.createElement('td');
        volume.textContent = group.volumeAverage == null ? '-' : `${Math.round(group.volumeAverage)}%`;
        volume.title = '전일 거래량 ÷ 전전일 거래량 × 100의 평균 (거래일 기준, 자료 없는 사례 제외)';
        if (days === 'current') row.append(count);
        else {
          row.append(count, volume, average);
          if (days === 5) row.append(weight);
        }
        detail.addEventListener('click', () => {
          if (row.nextElementSibling?.classList.contains('analysis-details-row')) {
            const same = row.nextElementSibling.dataset.days === String(days);
            row.nextElementSibling.remove();
            row.querySelectorAll('.analysis-detail').forEach(button => button.setAttribute('aria-expanded', 'false'));
            if (same) return;
          }
          const expanded = document.createElement('tr'); expanded.className = 'analysis-details-row';
          expanded.dataset.days = days;
          const cell = document.createElement('td'); cell.colSpan = 10;
          const list = document.createElement('table'); list.className = 'analysis-table analysis-stocks';
          list.innerHTML = '<thead><tr><th>종목명</th><th>유형</th><th>날짜</th><th>최종가격</th><th>등락률</th></tr></thead><tbody></tbody>';
          for (const record of [...group.rows].sort((a, b) => b.date.localeCompare(a.date))) {
            const stock = document.createElement('tr');
            const name = document.createElement('td'); const button = document.createElement('button');
            button.type = 'button'; button.className = 'history-stock-button'; button.textContent = record.name; button.setAttribute('aria-expanded', 'false'); name.append(button); stock.append(name);
            for (const value of [record.type, record.date.slice(5), record.close == null ? '-' : `${Math.round(record.close).toLocaleString('ko-KR')}원`, record.change == null ? '-' : `${record.change >= 0 ? '+' : ''}${record.change.toFixed(1)}%`]) {
              const field = document.createElement('td'); field.textContent = value; stock.append(field);
            }
            stock.lastElementChild.className = record.change >= 0 ? 'positive' : 'negative';
            list.querySelector('tbody').append(stock);
            button.addEventListener('click', () => {
              if (stock.nextElementSibling?.classList.contains('analysis-chart-row')) { stock.nextElementSibling.remove(); button.setAttribute('aria-expanded', 'false'); return; }
              const items = record.current ? state.payload.candidates || [] : [...(state.payload.history || []), ...(state.payload.candidates || [])];
              const item = items.find(item => (item.id || item.code + '|' + item.registeredAt) === record.id);
              const chartRow = document.createElement('tr'); chartRow.className = 'analysis-chart-row';
              const chartCell = document.createElement('td'); chartCell.colSpan = 5;
              if (item) {
                const snapshot = structuredClone(item);
                snapshot.displayCharts ||= {};
                for (const [kind, key] of [['intraday', 't'], ['daily', 'd']]) {
                  const chart = snapshot.displayCharts[kind];
                  if (!chart) continue;
                  chart.series = [...new Map([...(chart.history || []), ...(chart.series || [])].map(row => [row[key], row])).values()]
                    .filter(row => record.current || row[key].slice(0, 10) <= record.date).sort((a, b) => a[key].localeCompare(b[key]));
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
        remark.textContent = (pair[5]?.largeGains || pair[10]?.largeGains) ? notes.join('/') : '';
        if ([pair.from, pair.to].some(color => ['#f8cbdc', '#f2abc6', '#e98bab'].includes(color))) {
          const warning = document.createElement('div');
          warning.textContent = '다음날 MA 상승을 꼭 확인할 것';
          remark.append(warning);
        }
        remark.title = '전일 종가 대비 당일 종가가 20% 이상 상승한 종목·거래일 건수';
        row.insertBefore(remark, row.lastElementChild); body.append(row);
      }
      };
      table.querySelectorAll('[data-sort-days]').forEach(button => button.addEventListener('click', () => { sortDays = Number(button.dataset.sortDays); renderRows(); }));
      renderRows();
      content.append(table);
      if (!combined.size) { const empty = document.createElement('p'); empty.textContent = '비교 가능한 색 전환 기록이 없습니다.'; content.append(empty); }
    }
  } catch (error) { content.textContent = error.message; }
}
