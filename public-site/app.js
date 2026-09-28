(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const clean = value => String(value ?? '').trim();
  const url = value => /^https?:\/\//i.test(clean(value)) ? clean(value) : '';
  let data;

  function setView() {
    const key = location.hash.slice(1);
    const view = ['daily','stocks','tracker','market','performance','sms','transcript'].includes(key) ? key : 'daily';
    document.querySelectorAll('.view').forEach(el => { el.hidden = el.id !== view; });
    document.querySelectorAll('.tabs a').forEach(el => {
      if (el.dataset.view === view) el.setAttribute('aria-current','page');
      else el.removeAttribute('aria-current');
    });
  }

  function textCard(title, body, meta = '') {
    return `<article class="card"><h2>${esc(title)}</h2>${meta ? `<p class="meta">${esc(meta)}</p>` : ''}<div class="prose">${esc(body || '尚無內容')}</div></article>`;
  }

  function table(rows, fields) {
    if (!rows.length) return '<p class="hint">這一天沒有這類紀錄。</p>';
    return `<div class="card"><table class="records"><thead><tr>${fields.map(f => `<th>${esc(f[0])}</th>`).join('')}</tr></thead><tbody>${rows.map(r =>
      `<tr>${fields.map(f => `<td data-label="${esc(f[0])}">${esc(r[f[1]] || '—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  function inline(text) {
    return esc(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  }

  function articleHtml(markdown) {
    const lines = String(markdown || '').split(/\r?\n/);
    const out = []; let i = 0;
    while (i < lines.length) {
      const line = lines[i].trim();
      if (!line) { i++; continue; }
      if (line.startsWith('|') && i + 1 < lines.length && /^\|?[\s:|-]+\|?$/.test(lines[i + 1].trim())) {
        const cells = raw => raw.trim().replace(/^\||\|$/g,'').split('|').map(x => x.trim());
        const head = cells(line); i += 2;
        const body = [];
        while (i < lines.length && lines[i].trim().startsWith('|')) body.push(cells(lines[i++]));
        out.push(`<div class="article-scroll"><table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${body.map(row => `<tr>${head.map((_, n) => `<td>${inline(row[n] || '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
        continue;
      }
      if (/^#{1,3}\s/.test(line)) {
        const level = Math.min(3, line.match(/^#+/)[0].length);
        out.push(`<h${level}>${inline(line.replace(/^#+\s*/,''))}</h${level}>`);
      } else if (/^[・•*-]\s*/.test(line)) {
        out.push(`<p>• ${inline(line.replace(/^[・•*-]\s*/,''))}</p>`);
      } else out.push(`<p>${inline(line)}</p>`);
      i++;
    }
    return out.join('');
  }

  function renderDaily() {
    const days = [...new Set([...data.articles.map(x => x.date), ...data.trades.map(x => x['日期']), ...data.holdings.map(x => x['日期'])])].sort().reverse();
    $('dayPicker').innerHTML = days.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join('');
    const draw = () => {
      const day = $('dayPicker').value;
      const article = data.articles.find(x => x.date === day);
      const trades = data.trades.filter(x => x['日期'] === day);
      const held = data.holdings.filter(x => x['日期'] === day);
      $('dailyBody').innerHTML =
        `<div class="grid day-group">${textCard('當日操作', `${trades.length} 筆`)}${textCard('會員持股聲明', `${held.length} 筆`)}</div>` +
        `<section class="day-group"><h2>買賣與觀望</h2>${table(trades,[['名稱','股票名稱'],['代號','代號'],['方向','方向'],['價位','價位說明'],['說明','理由摘錄']])}</section>` +
        `<section class="day-group"><h2>會員持股</h2>${table(held,[['名稱','股票名稱'],['代號','代號'],['立場','目前立場'],['說明','說明重點']])}</section>` +
        `<section class="card article"><h2>每日整理</h2>${article ? articleHtml(article.body) : '<p class="hint">這一天沒有完成的影片每日整理。會員通知請到該分頁查看。</p>'}</section>`;
    };
    $('dayPicker').addEventListener('change', draw);
    draw();
  }

  function stockIndex() {
    const map = new Map();
    for (const row of [...data.trades, ...data.holdings, ...data.tracker, ...data.quotes]) {
      const code = clean(row['代號']);
      const name = clean(row['股票名稱'] || row['名稱']);
      if (code && !map.has(code)) map.set(code, name);
      else if (code && name && !map.get(code)) map.set(code, name);
    }
    return [...map].map(([code,name]) => ({code,name})).sort((a,b) => a.code.localeCompare(b.code));
  }

  function renderStocks() {
    const all = stockIndex();
    const results = $('stockResults');
    const draw = () => {
      const q = clean($('stockSearch').value).toLowerCase();
      if (!q) { results.innerHTML = '<p class="hint">輸入名稱或代號，查看已公開的逐日紀錄與持股聲明。</p>'; return; }
      const hits = all.filter(x => x.code.toLowerCase().includes(q) || x.name.toLowerCase().includes(q)).slice(0,30);
      results.innerHTML = hits.length ? hits.map(x => `<button class="stock-result" data-code="${esc(x.code)}">${esc(x.name)}　<span class="meta">${esc(x.code)}</span></button>`).join('') : '<p class="hint">找不到已收錄的標的。</p>';
    };
    $('stockSearch').addEventListener('input', draw);
    results.addEventListener('click', ev => {
      const btn = ev.target.closest('[data-code]'); if (!btn) return;
      const code = btn.dataset.code;
      const item = all.find(x => x.code === code);
      const rows = data.trades.filter(x => x['代號'] === code).sort((a,b) => b['日期'].localeCompare(a['日期']));
      const held = data.holdings.filter(x => x['代號'] === code).sort((a,b) => b['日期'].localeCompare(a['日期']));
      const quote = data.quotes.find(x => x['代號'] === code);
      const details = `<div class="card stock-detail"><h2>${esc(item?.name || code)} ${esc(code)}</h2>${quote ? `<p class="meta">參考報價 ${esc(quote['現價'])}｜${esc(quote['更新時間'])}</p>` : ''}<h3>操作與觀望</h3>${table(rows,[['日期','日期'],['方向','方向'],['說明','理由摘錄']])}<h3>會員持股</h3>${table(held,[['日期','日期'],['立場','目前立場'],['說明','說明重點']])}</div>`;
      results.querySelectorAll('.stock-detail').forEach(el => el.remove());
      btn.insertAdjacentHTML('afterend', details);
    });
    draw();
  }

  function renderTracker() {
    const rows = data.tracker.slice().sort((a,b) => clean(a['代號']).localeCompare(clean(b['代號'])));
    $('trackerBody').innerHTML = rows.length ? `<div class="grid">${rows.map(r =>
      `<details class="card"><summary>${esc(r['股票名稱'])} <span class="meta">${esc(r['代號'])}</span>　<span class="tag">${esc(r['狀態'])}</span></summary><p class="meta">首次買入 ${esc(r['首次買入日'])}｜最新說明 ${esc(r['最新說明日期'])}</p><p>進場價 ${esc(r['進場價'])}　${esc(r['進場價來源'])}</p><p>最近賣出 ${esc(r['最近賣出日'] || '—')}　出場價 ${esc(r['出場價'] || '—')}</p><p>提及 ${esc(r['提及次數'])} 次</p><div class="prose">${esc(r['逐日說明'])}</div></details>`).join('')}</div>` : '<p class="hint">尚無已發布的持股追蹤資料。</p>';
  }

  function lineSvg(points) {
    // 空白快取不是零；把它當零會畫出不存在的暴跌與假績效。
    const vals = points.map(x => {
      const raw = String(x?.value ?? '').replace(/,/g, '').trim();
      return raw === '' ? NaN : Number(raw);
    }).filter(Number.isFinite);
    if (vals.length < 2) return '<p class="hint">尚未累積足夠折線資料。</p>';
    let min = Math.min(...vals), max = Math.max(...vals);
    if (min === max) { min -= 1; max += 1; }
    const coords = vals.map((v,i) => `${20+i*760/(vals.length-1)},${210-(v-min)*190/(max-min)}`).join(' ');
    const color = vals.at(-1) >= vals[0] ? '#ba3041' : '#158060';
    return `<svg class="chart" viewBox="0 0 800 230" role="img" aria-label="數值由 ${esc(vals[0])} 到 ${esc(vals.at(-1))}"><polyline points="${coords}" fill="none" stroke="${color}" stroke-width="2.5" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function renderMarket() {
    const keys = ['taiex','tx','dxy','usdtwd','wti','brent'];
    const cards = keys.map(key => {
      const item = data.market[key]; if (!item) return '';
      const d = item.data, val = Number(d.value), change = Number(d.change);
      return `<article class="card"><h2>${esc(d.label || key)}</h2><p class="metric">${Number.isFinite(val) ? esc(val.toLocaleString('zh-TW')) : '—'} <small>${esc(d.unit || '')}</small></p><p class="${change >= 0 ? 'up' : 'down'}">${Number.isFinite(change) ? (change >= 0 ? '+' : '') + esc(change) : '—'} ${d.percent != null ? `(${esc(d.percent)}%)` : ''}</p><p class="meta">${esc(d.time || '')}｜快取 ${esc(item.updated)}｜${esc(item.source)}</p>${lineSvg(Array.isArray(d.line) ? d.line : [])}</article>`;
    }).filter(Boolean);
    const sectors = data.market.sectors?.data;
    let sectorHtml = '';
    if (sectors?.rows?.length) {
      const rows = sectors.rows;
      const renderRows = arr => arr.map(r => `<div class="row"><span>${esc(r.name)}</span><b>${Number(r.percent).toFixed(2)}%</b></div><div class="bar"><i style="width:${Math.min(100,Math.max(0,Number(r.percent)))}%"></i></div>`).join('');
      sectorHtml = `<article class="card"><h2>產業成交比重</h2><p class="meta">${esc(sectors.date)}｜${esc(sectors.basis || '')}</p>${renderRows(rows.slice(0,5))}${rows.length > 5 ? `<details><summary>展開其餘 ${rows.length - 5} 項</summary>${renderRows(rows.slice(5))}</details>` : ''}</article>`;
    }
    $('marketBody').innerHTML = `<div class="grid">${cards.join('') || '<p class="hint">市場快取尚未就緒。</p>'}${sectorHtml}</div>`;
  }

  function renderPerformance() {
    const rows = data.performance;
    $('performanceBody').innerHTML = `<article class="card"><p class="meta">僅顯示 2026/07/08 起的已發布資料，共 ${rows.length} 個日期。</p>${lineSvg(rows.map(r => ({value:r['平均報酬']})))}${table(rows.slice(-20).reverse(),[['日期','日期'],['追蹤檔數','追蹤檔數'],['平均報酬','平均報酬'],['正報酬比例','正報酬比例']])}</article>`;
  }

  function renderSms() {
    const items = data.sms.slice().sort((a,b) => clean(b['發文時間']).localeCompare(clean(a['發文時間'])));
    $('smsBody').innerHTML = items.length ? items.map(r =>
      `<article class="card"><h2>${esc(r['標題'] || '會員通知')}</h2><p class="meta">${esc(r['發文時間'])}｜解析 ${esc(r['解析狀態'] || '待處理')}</p><div class="prose">${esc(r['原文'])}</div>${url(r['網址']) ? `<p><a href="${esc(url(r['網址']))}" target="_blank" rel="noopener noreferrer">查看來源</a></p>` : ''}</article>`).join('') : '<p class="hint">尚無已公開的會員通知。</p>';
  }

  function renderTranscript() {
    const items = data.transcripts;
    $('transcriptPicker').innerHTML = items.map(r => `<option value="${esc(r.date)}">${esc(r.date)}</option>`).join('');
    const draw = () => {
      const row = items.find(r => r.date === $('transcriptPicker').value);
      $('transcriptBody').innerHTML = row ? `<article class="card"><h2>${esc(row.title || row.date)}</h2><p class="meta">${esc(row.date)}｜以下為閱讀排版；原文可展開核對</p><div class="prose">${esc(row.reading)}</div><details><summary>查看原始逐字稿</summary><div class="prose">${esc(row.raw)}</div></details></article>` : '<p class="hint">尚無已完成的逐字稿。</p>';
    };
    $('transcriptPicker').addEventListener('change', draw);
    draw();
  }

  window.addEventListener('hashchange', setView);
  setView();
  fetch('data.json?t=' + Date.now(), {cache:'no-store'})
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(snapshot => {
      data = snapshot;
      const stamp = new Date(snapshot.generatedAt);
      $('topState').textContent = `快照 ${stamp.toLocaleString('zh-TW',{timeZone:'Asia/Taipei'})}`;
      const age = Date.now() - stamp.getTime();
      $('siteStatus').textContent = age > 60*60*1000 ? '資料超過一小時未更新；仍可查閱最後成功發布的紀錄。' : '資料已載入。這是公開快照，盤中數值請核對各卡片的來源時間。';
      $('siteStatus').classList.toggle('warn', age > 60*60*1000);
      renderDaily(); renderStocks(); renderTracker(); renderMarket(); renderPerformance(); renderSms(); renderTranscript();
    })
    .catch(() => {
      $('topState').textContent = '資料暫時無法載入';
      $('siteStatus').className = 'status error';
      $('siteStatus').textContent = '公開快照讀取失敗。請重新整理；若持續發生，查看 GitHub Actions 的 public-site-pages 執行狀態。';
    });
})();
