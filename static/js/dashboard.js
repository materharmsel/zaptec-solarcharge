// dashboard.js — Chart.js grafiek, polling, sparklines, controls

let mainChart = null;
let sparklineP1 = null;
let sparklineTrend = null;
let huidigTijdvenster = 30;
let lijnenActief = { p1: true, trend: true, lv: true, target: true };

function initDashboard() {
  initMainChart();
  initSparklines();
  initToggleKnoppen();
  laadGrafiekData(huidigTijdvenster);
  setInterval(pollStatus, 10000);
}

function initMainChart() {
  const ctx = document.getElementById('main-chart');
  if (!ctx) return;
  mainChart = new Chart(ctx.getContext('2d'), {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'P1 netto (W)',     data: [], borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.08)', borderWidth: 2, pointRadius: 0, fill: true,  tension: 0.3 },
        { label: 'Trend (W)',         data: [], borderColor: '#a78bfa', backgroundColor: 'transparent',           borderWidth: 2, pointRadius: 0, fill: false, tension: 0.5 },
        { label: 'Laadvermogen (W)', data: [], borderColor: '#60a5fa', backgroundColor: 'rgba(96,165,250,0.06)', borderWidth: 2, pointRadius: 0, fill: true,  tension: 0.3 },
        { label: 'Target',            data: [], borderColor: '#4b5563', backgroundColor: 'transparent',           borderWidth: 1.5, pointRadius: 0, fill: false, borderDash: [6,4] },
      ]
    },
    options: {
      responsive: true,
      animation: { duration: 400 },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#111827', borderColor: '#1f2937', borderWidth: 1,
          titleColor: '#9ca3af', bodyColor: '#f9fafb',
          callbacks: { label: ctx => `${ctx.dataset.label}: ${Math.round(ctx.parsed.y)} W` }
        }
      },
      scales: {
        x: { ticks: { color: '#4b5563', font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 6 }, grid: { color: '#1f2937' } },
        y: { ticks: { color: '#4b5563', font: { size: 10 }, callback: v => v + ' W' }, grid: { color: '#1f2937' }, min: -600, max: 600 }
      }
    }
  });
}

function initSparklines() {
  function maakSparkline(id, kleur) {
    const el = document.getElementById(id);
    if (!el) return null;
    return new Chart(el.getContext('2d'), {
      type: 'line',
      data: { labels: [], datasets: [{ data: [], borderColor: kleur, backgroundColor: kleur + '30', borderWidth: 2, pointRadius: 0, fill: true, tension: 0.4 }] },
      options: { responsive: false, animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } } }
    });
  }
  sparklineP1    = maakSparkline('sparkline-p1', '#10b981');
  sparklineTrend = maakSparkline('sparkline-trend', '#a78bfa');
}

function initToggleKnoppen() {
  // Tijdknoppen
  document.querySelectorAll('.time-btn').forEach(btn => {
    btn.addEventListener('click', () => setTijdvenster(parseInt(btn.dataset.minuten), btn));
  });
  // Standaard: 30m actief
  const standaard = document.querySelector('.time-btn[data-minuten="30"]');
  if (standaard) setToggleActief(standaard, true, 'time-btn');

  // Lijn-toggles
  document.querySelectorAll('.lijn-toggle').forEach(btn => {
    btn.addEventListener('click', () => toggleLijn(btn));
    setLijnToggleStijl(btn, true); // alles standaard aan
  });
}

async function laadGrafiekData(minuten) {
  try {
    const res = await fetch(`/api/metingen?minuten=${minuten}`);
    const metingen = await res.json();
    if (!Array.isArray(metingen) || metingen.length === 0) return;

    const spanning = window.DASHBOARD_CONFIG?.spanning_v ?? 230;
    const target   = window.DASHBOARD_CONFIG?.doel_net_vermogen_w ?? 0;

    const labels  = metingen.map(m => m.tijdstip.substr(11, 5));
    const p1Data  = metingen.map(m => m.net_vermogen_w);
    const lvData  = metingen.map(m => (m.gesteld_stroom_a || 0) * spanning * (m.huidige_fasen || 1));
    const tData   = metingen.map(() => target);

    if (!mainChart) return;
    mainChart.data.labels           = labels;
    mainChart.data.datasets[0].data = p1Data;
    mainChart.data.datasets[1].data = new Array(metingen.length).fill(null); // EMA filled by polling
    mainChart.data.datasets[2].data = lvData;
    mainChart.data.datasets[3].data = tData;
    mainChart.update('none');
  } catch (e) {
    console.warn('Grafiek laden mislukt:', e);
  }
}

async function pollStatus() {
  try {
    const res  = await fetch('/api/status');
    const data = await res.json();

    updateP1Kaart(data);
    updateLaadstroomKaart(data);
    updateTrendKaart(data);
    updateStatusBadge(data);

    if (data.metingen && data.metingen.length > 0) {
      voegMeetpuntToe(data.metingen[0], data);
    }
    if (data.metingen) {
      const vals = data.metingen.slice().reverse().map(m => m.net_vermogen_w);
      updateSparkline(sparklineP1, vals);
    }
  } catch (e) {
    console.warn('Polling mislukt:', e);
  }
}

function voegMeetpuntToe(meting, data) {
  if (!mainChart) return;
  const label   = meting.tijdstip.substr(11, 5);
  const spanning = window.DASHBOARD_CONFIG?.spanning_v ?? 230;
  const target   = window.DASHBOARD_CONFIG?.doel_net_vermogen_w ?? 0;

  // Vermijd duplicaten
  if (mainChart.data.labels.length && mainChart.data.labels[mainChart.data.labels.length - 1] === label) return;

  mainChart.data.labels.push(label);
  mainChart.data.datasets[0].data.push(meting.net_vermogen_w);
  mainChart.data.datasets[1].data.push(data.ema_net_vermogen_w ?? null);
  mainChart.data.datasets[2].data.push((meting.gesteld_stroom_a || 0) * spanning * (meting.huidige_fasen || 1));
  mainChart.data.datasets[3].data.push(target);

  const MAX = 200;
  if (mainChart.data.labels.length > MAX) {
    mainChart.data.labels.shift();
    mainChart.data.datasets.forEach(ds => ds.data.shift());
  }
  mainChart.update('none');

  if (data.ema_net_vermogen_w != null) {
    const trendVals = mainChart.data.datasets[1].data.filter(v => v != null);
    updateSparkline(sparklineTrend, trendVals);
  }
}

function updateSparkline(chart, waarden) {
  if (!chart || !waarden.length) return;
  chart.data.labels           = waarden.map((_, i) => i);
  chart.data.datasets[0].data = waarden;
  chart.update('none');
}

function updateP1Kaart(data) {
  const w  = data.net_vermogen_w ?? 0;
  const el = document.getElementById('p1-waarde');
  if (el) {
    el.textContent = Math.round(w) + ' W';
    el.style.color = w <= 0 ? 'var(--green)' : 'var(--red)';
  }
  const badge = document.getElementById('p1-badge');
  const sub   = document.getElementById('p1-sub');
  if (badge) {
    if (w <= 0) {
      badge.innerHTML = '<i data-lucide="arrow-up" class="w-2.5 h-2.5"></i> exporterend';
      badge.className = 'flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-[var(--accent-dim)] text-[var(--accent)]';
    } else {
      badge.innerHTML = '<i data-lucide="arrow-down" class="w-2.5 h-2.5"></i> importerend';
      badge.className = 'flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-[rgba(239,68,68,.1)] text-[var(--red)]';
    }
    if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [badge] });
  }
  if (sub) sub.textContent = w <= 0 ? 'terug naar net' : 'uit het net';
}

function updateLaadstroomKaart(data) {
  const stroom = data.huidig_stroom_a ?? 0;
  const max    = window.DASHBOARD_CONFIG?.max_stroom_a ?? 25;
  const fasen  = data.huidige_fasen ?? 1;
  const pct    = Math.min(stroom / max, 1);
  const omtrek = 251.2;

  const ring = document.getElementById('gauge-ring');
  if (ring) ring.setAttribute('stroke-dashoffset', String(omtrek - pct * omtrek));

  const gv = document.getElementById('gauge-waarde');
  if (gv) gv.textContent = Math.round(stroom) + 'A';

  document.querySelectorAll('.fase-kolom').forEach(col => {
    const f     = parseInt(col.dataset.fase);
    const actief = stroom > 0 && (fasen === 3 || f === 1);
    const val   = col.querySelector('.fase-val');
    const fill  = col.querySelector('.fase-fill');
    const track = fill?.parentElement;
    if (val)  {
      val.textContent  = actief ? Math.round(stroom) + 'A' : '0A';
      val.style.color  = actief ? 'var(--accent)' : 'var(--border)';
    }
    if (fill) {
      fill.style.height     = actief ? (pct * 100) + '%' : '0%';
      fill.style.background = actief ? 'var(--accent)' : 'transparent';
      fill.style.boxShadow  = actief ? '0 0 6px var(--accent)' : 'none';
    }
    if (track) track.style.background = actief ? 'var(--border-green)' : '#161b22';
  });
}

function updateTrendKaart(data) {
  const ema = data.ema_net_vermogen_w;
  const el  = document.getElementById('trend-waarde');
  if (el) el.textContent = ema != null ? Math.round(ema) + ' W' : '— W';
}

function updateStatusBadge(data) {
  const text = document.getElementById('status-text');
  const meta = document.getElementById('status-meta');
  if (text) text.textContent = data.laadmodus || 'Onbekend';
  if (meta) meta.textContent = `· ${data.regelaar_model || ''} · ${data.huidige_fasen || 1}-fase`;
}

function setTijdvenster(minuten, btn) {
  huidigTijdvenster = minuten;
  document.querySelectorAll('.time-btn').forEach(b => setToggleActief(b, false, 'time-btn'));
  setToggleActief(btn, true, 'time-btn');
  if (mainChart) {
    mainChart.data.labels = [];
    mainChart.data.datasets.forEach(ds => ds.data = []);
  }
  laadGrafiekData(minuten);
}

function setToggleActief(btn, actief, type) {
  if (type === 'time-btn') {
    btn.style.background = actief ? '#1f2937' : 'transparent';
    btn.style.color      = actief ? '#d1d5db' : '';
  }
}

function toggleLijn(btn) {
  const lijn = btn.dataset.lijn;
  lijnenActief[lijn] = !lijnenActief[lijn];
  const idx = { p1: 0, trend: 1, lv: 2, target: 3 }[lijn];
  if (mainChart && idx !== undefined) {
    mainChart.data.datasets[idx].hidden = !lijnenActief[lijn];
    mainChart.update('none');
  }
  setLijnToggleStijl(btn, lijnenActief[lijn]);
}

function setLijnToggleStijl(btn, actief) {
  const kleur = btn.dataset.kleur || '#6b7280';
  if (actief) {
    btn.style.background   = kleur + '18';
    btn.style.borderColor  = kleur + '60';
    btn.style.color        = kleur;
  } else {
    btn.style.background   = 'transparent';
    btn.style.borderColor  = '#1f2937';
    btn.style.color        = '#4b5563';
  }
}

async function quickSetting(sleutel, waarde) {
  try {
    await fetch('/api/quick-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [sleutel]: waarde }),
    });
  } catch (e) {
    console.warn('Quick-setting opslaan mislukt:', e);
  }
}
