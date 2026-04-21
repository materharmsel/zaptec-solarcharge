// dashboard.js — Chart.js grafiek, polling, sparklines, controls

let mainChart = null;
let sparklineP1 = null;
let sparklineTrend = null;
let huidigTijdvenster = 30;
let lijnenActief = { p1: true, trend: true, lv: true, target: true };

const SPARKLINE_MINUTEN = 15;
const SPARKLINE_MAX_PUNTEN = 90; // 15 min × ~1 punt / 10s polling
let sparkP1Waarden = [];
let sparkTrendWaarden = [];

function initDashboard() {
  initMainChart();
  initSparklines();
  initToggleKnoppen();
  laadGrafiekData(huidigTijdvenster);
  laadSparklineData();
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
        { label: 'P1 netto (W)',     data: [], borderColor: '#0D9488', backgroundColor: 'rgba(13,148,136,0.08)', borderWidth: 2, pointRadius: 0, fill: true,  tension: 0.4, cubicInterpolationMode: 'monotone' },
        { label: 'Trend (W)',         data: [], borderColor: '#8B5CF6', backgroundColor: 'transparent',           borderWidth: 2, pointRadius: 0, fill: false, tension: 0.5 },
        { label: 'Laadvermogen (W)', data: [], borderColor: '#3B82F6', backgroundColor: 'rgba(59,130,246,0.06)', borderWidth: 2, pointRadius: 0, fill: true,  tension: 0.3 },
        { label: 'Target',            data: [], borderColor: '#9CA3AF', backgroundColor: 'transparent',           borderWidth: 1.5, pointRadius: 0, fill: false, borderDash: [6,4] },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#FFFFFF', borderColor: '#E5E7EB', borderWidth: 1,
          titleColor: '#6B7280', bodyColor: '#111827',
          callbacks: { label: ctx => `${ctx.dataset.label}: ${Math.round(ctx.parsed.y)} W` }
        }
      },
      scales: {
        x: { ticks: { color: '#9CA3AF', font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 6 }, grid: { color: '#F3F4F6' } },
        y: { ticks: { color: '#9CA3AF', font: { size: 10 }, callback: v => v + ' W' }, grid: { color: '#F3F4F6' } }
      }
    }
  });
}

function initSparklines() {
  function maakSparkline(id, kleur, rgb) {
    const el = document.getElementById(id);
    if (!el) return null;
    const ctx = el.getContext('2d');
    // Gradient-plugin: herberekent bij elke draw — blijft correct bij resize + retina
    const gradientFill = {
      id: 'sparkGradientFill',
      beforeDatasetsDraw(chart) {
        const { chartArea } = chart;
        if (!chartArea) return;
        const g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
        g.addColorStop(0, `rgba(${rgb},0.18)`);
        g.addColorStop(1, `rgba(${rgb},0)`);
        chart.data.datasets[0].backgroundColor = g;
      }
    };
    const chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [{
          data: [],
          borderColor: kleur,
          borderWidth: 1.5,
          pointRadius: 0,
          fill: true,
          tension: 0.4,
          cubicInterpolationMode: 'monotone',
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        layout: { padding: { top: 2, bottom: 0, left: 0, right: 0 } },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false } },
        elements: { line: { borderJoinStyle: 'round', borderCapStyle: 'round' } }
      },
      plugins: [gradientFill]
    });
    // Forceer resize nadat de browser de layout heeft afgerond — lost op dat Chart.js
    // soms de default 300×150 aanhoudt als de parent net pas zichtbaar werd.
    requestAnimationFrame(() => chart.resize());
    return chart;
  }
  sparklineP1    = maakSparkline('sparkline-p1',    '#0D9488', '13,148,136');
  sparklineTrend = maakSparkline('sparkline-trend', '#8B5CF6', '139,92,246');
}

async function laadSparklineData() {
  try {
    const res = await fetch(`/api/metingen?minuten=${SPARKLINE_MINUTEN}`);
    const metingen = await res.json();
    if (!Array.isArray(metingen) || metingen.length === 0) return;
    // /api/metingen geeft nieuwste-eerst; draai om naar chronologisch
    const chrono = metingen.slice().reverse();
    sparkP1Waarden    = chrono.map(m => m.net_vermogen_w).filter(v => v != null).slice(-SPARKLINE_MAX_PUNTEN);
    // ema_net_vermogen_w niet in metingen-rij → blijft leeg tot eerste poll
    updateSparkline(sparklineP1, sparkP1Waarden);
  } catch (e) {
    console.warn('Sparkline seeden mislukt:', e);
  }
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

// Samenvatten naar 1 punt per minuut (gemiddelde) voor een cleane grafiek
function downsamplePerMinuut(metingen) {
  if (metingen.length === 0) return [];
  const gem = arr => arr.reduce((s, v) => s + v, 0) / arr.length;
  const buckets = new Map();
  metingen.forEach(m => {
    const key = m.tijdstip.substr(11, 5); // "HH:MM"
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(m);
  });
  return Array.from(buckets.entries()).map(([label, ms]) => ({
    label,
    net_vermogen_w:  gem(ms.map(m => m.net_vermogen_w)),
    gesteld_stroom_a: gem(ms.map(m => m.gesteld_stroom_a || 0)),
    huidige_fasen:   ms[ms.length - 1].huidige_fasen || 1,
  }));
}

async function laadGrafiekData(minuten) {
  try {
    const res = await fetch(`/api/metingen?minuten=${minuten}`);
    const metingen = await res.json();
    if (!Array.isArray(metingen) || metingen.length === 0) return;

    const spanning = window.DASHBOARD_CONFIG?.spanning_v ?? 230;
    const target   = window.DASHBOARD_CONFIG?.doel_net_vermogen_w ?? 0;

    const punten = downsamplePerMinuut(metingen);
    const labels  = punten.map(p => p.label);
    const p1Data  = punten.map(p => p.net_vermogen_w);
    const lvData  = punten.map(p => p.gesteld_stroom_a * spanning * p.huidige_fasen);
    const tData   = punten.map(() => target);

    if (!mainChart) return;
    mainChart.data.labels           = labels;
    mainChart.data.datasets[0].data = p1Data;
    mainChart.data.datasets[1].data = new Array(punten.length).fill(null); // EMA filled by polling
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

      const nieuwste = data.metingen[0];
      if (nieuwste && nieuwste.net_vermogen_w != null) {
        sparkP1Waarden.push(nieuwste.net_vermogen_w);
        if (sparkP1Waarden.length > SPARKLINE_MAX_PUNTEN) sparkP1Waarden.shift();
        updateSparkline(sparklineP1, sparkP1Waarden);
      }
    }
    if (data.ema_net_vermogen_w != null) {
      sparkTrendWaarden.push(data.ema_net_vermogen_w);
      if (sparkTrendWaarden.length > SPARKLINE_MAX_PUNTEN) sparkTrendWaarden.shift();
      updateSparkline(sparklineTrend, sparkTrendWaarden);
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

  const MAX = 60;
  if (mainChart.data.labels.length > MAX) {
    mainChart.data.labels.shift();
    mainChart.data.datasets.forEach(ds => ds.data.shift());
  }
  mainChart.update('none');
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
  var stroom = data.huidig_stroom_a || 0;
  var max    = window.DASHBOARD_CONFIG?.max_stroom_a || 25;
  var fasen  = data.huidige_fasen || 1;
  var pct    = Math.min(stroom / max, 1);

  var gv = document.getElementById('gauge-waarde');
  if (gv) gv.textContent = Math.round(stroom) + 'A';

  document.querySelectorAll('.fase-kolom').forEach(function(col) {
    var f      = parseInt(col.dataset.fase);
    var actief = stroom > 0 && (fasen === 3 || f === 1);
    var val    = col.querySelector('.fase-val');
    var fill   = col.querySelector('.fase-fill');
    if (val) {
      val.textContent = actief ? Math.round(stroom) + 'A' : '0A';
      val.style.color = actief ? 'var(--accent)' : 'var(--border)';
    }
    if (fill) {
      fill.style.width      = actief ? (pct * 100) + '%' : '0%';
      fill.style.background = actief ? 'var(--accent)' : 'transparent';
    }
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
    mainChart.data.datasets.forEach(ds => { ds.data = []; });
    mainChart.update('none'); // direct visueel leegmaken — geeft gebruiker feedback
  }
  laadGrafiekData(minuten);
}

function setToggleActief(btn, actief, type) {
  if (type === 'time-btn') {
    btn.style.background   = actief ? 'rgba(13,148,136,0.10)' : 'transparent';
    btn.style.color        = actief ? '#0D9488'               : '';
    btn.style.borderRadius = '6px';
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
    btn.style.borderColor  = '#E5E7EB';
    btn.style.color        = '#9CA3AF';
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
