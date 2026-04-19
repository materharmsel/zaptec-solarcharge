// sessies.js — uitklapbare sessie-rijen + mini-grafiek

let huidigePagina = 1;
let totaalPaginas = 1;
let openSessieId = null;
let sessieCharts = {};

async function laadSessies(pagina) {
  huidigePagina = pagina;
  try {
    const res  = await fetch(`/api/sessies?pagina=${pagina}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    totaalPaginas = data.paginas;
    renderTabel(data.sessies);
    renderPaginering(data.pagina, data.paginas, data.totaal);
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) {
    console.warn('Sessies laden mislukt:', e);
  }
}

function renderTabel(sessies) {
  const tbody = document.getElementById('sessies-tbody');
  tbody.innerHTML = '';

  if (!sessies || sessies.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-8 text-center text-[var(--text-muted)]">Geen sessies gevonden.</td></tr>';
    return;
  }

  sessies.forEach(s => {
    const rij = document.createElement('tr');
    rij.className = 'data-rij border-b border-[var(--border)] hover:bg-gray-50 transition-colors cursor-pointer';
    rij.dataset.sessieId = s.id;

    const scoreKleur = s.gem_score >= 75 ? '#0D9488' : s.gem_score >= 50 ? '#f97316' : '#ef4444';
    const scoreBg    = s.gem_score >= 75 ? 'rgba(13,148,136,.12)' : s.gem_score >= 50 ? 'rgba(249,115,22,.12)' : 'rgba(239,68,68,.12)';
    const model      = (s.model || '').includes('solar') ? 'SolarFlow' : 'Legacy';
    const modelKleur = (s.model || '').includes('solar') ? '#8B5CF6' : '#6b7280';
    const duur       = formatDuur(s.duur_s);
    const datum      = (s.start_tijdstip || '').substr(0, 16).replace('T', ' ');
    const kwh        = s.geladen_kwh != null ? Number(s.geladen_kwh).toFixed(1) : '—';
    const score      = s.gem_score  != null ? Math.round(s.gem_score) : '—';

    rij.innerHTML = `
      <td class="px-4 py-3 text-[var(--text-primary)]">${datum}</td>
      <td class="px-4 py-3 text-[var(--text-secondary)]">${duur}</td>
      <td class="px-4 py-3 font-semibold text-[var(--text-primary)]">${kwh}</td>
      <td class="px-4 py-3">
        <span class="px-2 py-0.5 rounded text-[10px] font-bold" style="background:${scoreBg};color:${scoreKleur};">${score}</span>
      </td>
      <td class="px-4 py-3">
        <span class="text-[10px]" style="color:${modelKleur};">${model}</span>
      </td>
      <td class="px-4 py-3">
        <button class="expand-btn w-6 h-6 rounded border border-[var(--border)] flex items-center justify-center transition-colors hover:border-[var(--accent)]"
                style="background:var(--bg-base);" data-sessie-id="${s.id}">
          <i data-lucide="chevron-down" class="w-3 h-3 text-[var(--text-muted)]"></i>
        </button>
      </td>
    `;

    rij.querySelector('.expand-btn').addEventListener('click', e => {
      e.stopPropagation();
      toggleDetail(s);
    });
    rij.addEventListener('click', () => toggleDetail(s));

    tbody.appendChild(rij);
  });
}

function toggleDetail(sessie) {
  const id    = sessie.id;
  const tbody = document.getElementById('sessies-tbody');
  const bestaand = tbody.querySelector(`.detail-rij[data-detail-id="${id}"]`);

  if (bestaand) {
    sluitDetail(id, tbody, bestaand);
    return;
  }

  // Sluit eerder open rij
  if (openSessieId && openSessieId !== id) {
    const oudRij = tbody.querySelector(`.detail-rij[data-detail-id="${openSessieId}"]`);
    if (oudRij) sluitDetail(openSessieId, tbody, oudRij);
  }

  openSessieId = id;
  updateExpandBtn(tbody, id, true);

  const dataRij  = tbody.querySelector(`tr[data-sessie-id="${id}"]`);
  if (!dataRij) return;

  const template  = document.getElementById('detail-template');
  const clone     = template.content.cloneNode(true);
  const detailRij = clone.querySelector('.detail-rij');
  detailRij.dataset.detailId = id;
  dataRij.after(detailRij);

  vulStats(detailRij.querySelector('.stats-grid'), sessie);
  laadSessieGrafiek(detailRij.querySelector('.sessie-chart'), id);
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

function sluitDetail(id, tbody, rij) {
  if (sessieCharts[id]) { sessieCharts[id].destroy(); delete sessieCharts[id]; }
  rij.remove();
  if (openSessieId === id) openSessieId = null;
  updateExpandBtn(tbody, id, false);
}

function updateExpandBtn(tbody, sessieId, open) {
  const btn = tbody.querySelector(`.expand-btn[data-sessie-id="${sessieId}"]`);
  if (!btn) return;
  btn.style.background   = open ? 'rgba(13,148,136,.10)' : 'var(--bg-base)';
  btn.style.borderColor  = open ? 'rgba(13,148,136,.4)'  : 'var(--border)';
  const icon = btn.querySelector('i');
  if (icon) {
    icon.setAttribute('data-lucide', open ? 'chevron-up' : 'chevron-down');
    icon.style.color = open ? 'var(--accent)' : 'var(--text-muted)';
    if (typeof lucide !== 'undefined') lucide.createIcons({ elements: [btn] });
  }
}

function vulStats(grid, sessie) {
  const stats = [
    { val: sessie.gem_score  != null ? Math.round(sessie.gem_score) + ' / 100' : '—',
      lbl: 'Sessiescore',
      kleur: sessie.gem_score >= 75 ? '#0D9488' : sessie.gem_score >= 50 ? '#f97316' : '#ef4444' },
    { val: sessie.gem_afwijking_w != null ? '±' + Math.round(sessie.gem_afwijking_w) + ' W' : '—',
      lbl: 'Gem. afwijking target', kleur: '#d1d5db' },
    { val: sessie.geladen_kwh != null ? Number(sessie.geladen_kwh).toFixed(1) + ' kWh' : '—',
      lbl: 'Totaal geladen', kleur: '#d1d5db' },
    { val: sessie.fase_wissel_count ?? 0,
      lbl: 'Fase wisselingen', kleur: '#d1d5db' },
    { val: sessie.no_import_count ?? 0,
      lbl: 'Noodoverride import',
      kleur: (sessie.no_import_count ?? 0) > 0 ? '#f97316' : '#d1d5db' },
    { val: sessie.no_export_count ?? 0,
      lbl: 'Noodoverride export', kleur: '#d1d5db' },
  ];
  stats.forEach(s => {
    const kaart = document.createElement('div');
    kaart.className = 'rounded-lg p-3 border border-[var(--border)]';
    kaart.style.background = 'var(--bg-surface)';
    kaart.innerHTML = `<div class="text-lg font-bold leading-none mb-1.5" style="color:${s.kleur};">${s.val}</div>
                       <div class="text-[10px] text-[var(--text-muted)]">${s.lbl}</div>`;
    grid.appendChild(kaart);
  });
}

// Samenvatten naar 1 punt per minuut — zelfde aanpak als dashboard.js
function downsampleSessie(metingen) {
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
    net_vermogen_w:   gem(ms.map(m => m.net_vermogen_w)),
    gesteld_stroom_a: gem(ms.map(m => m.gesteld_stroom_a || 0)),
    huidige_fasen:    ms[ms.length - 1].huidige_fasen || 1,
  }));
}

async function laadSessieGrafiek(canvas, sessieId) {
  try {
    const res  = await fetch(`/api/sessies/${sessieId}/metingen`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const metingen = data.metingen || [];
    const events   = data.events   || [];

    if (metingen.length === 0) {
      const p = document.createElement('p');
      p.className = 'text-[10px] text-[var(--text-muted)] mt-1';
      p.textContent = 'Geen meetdata beschikbaar voor deze sessie.';
      canvas.parentElement.appendChild(p);
      return;
    }

    const spanning = 230;
    const punten   = downsampleSessie(metingen);
    const labels   = punten.map(p => p.label);
    const p1Data   = punten.map(p => p.net_vermogen_w);
    const lvData   = punten.map(p => p.gesteld_stroom_a * spanning * p.huidige_fasen);

    // Events: afgerond naar minuut, zoekt in de downsampled labels
    const faseWissels   = events.filter(e => e.event_type === 'fase_wissel')
                                .map(e => e.tijdstip.substr(11, 5));
    const noodoverrides = events.filter(e => e.event_type && e.event_type.startsWith('noodoverride'))
                                .map(e => e.tijdstip.substr(11, 5));

    const eventPlugin = {
      id: 'eventMarkers_' + sessieId,
      afterDraw(chart) {
        const { ctx, chartArea: { top, bottom }, scales: { x } } = chart;
        [...faseWissels, ...noodoverrides].forEach(t => {
          const idx = labels.indexOf(t);
          if (idx < 0) return;
          const xPos = x.getPixelForValue(idx);
          ctx.save();
          ctx.strokeStyle = noodoverrides.includes(t) ? '#f97316' : '#3b82f6';
          ctx.setLineDash([3, 3]);
          ctx.globalAlpha = 0.5;
          ctx.beginPath();
          ctx.moveTo(xPos, top);
          ctx.lineTo(xPos, bottom);
          ctx.stroke();
          ctx.restore();
        });
      }
    };

    // Guard: canvas may have been removed if user closed the row during fetch
    if (!canvas.isConnected) return;

    sessieCharts[sessieId] = new Chart(canvas.getContext('2d'), {
      type: 'line',
      plugins: [eventPlugin],
      data: {
        labels,
        datasets: [
          { label: 'P1 netto (W)',     data: p1Data, borderColor: '#0D9488', backgroundColor: 'rgba(13,148,136,0.08)', borderWidth: 1.5, pointRadius: 0, fill: true,  tension: 0.4, cubicInterpolationMode: 'monotone' },
          { label: 'Laadvermogen (W)', data: lvData, borderColor: '#3B82F6', backgroundColor: 'rgba(59,130,246,0.06)', borderWidth: 1.5, pointRadius: 0, fill: true,  tension: 0.3, cubicInterpolationMode: 'monotone' },
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: '#FFFFFF', borderColor: '#E5E7EB', borderWidth: 1, titleColor: '#6B7280', bodyColor: '#111827' }
        },
        scales: {
          x: { ticks: { color: '#9CA3AF', font: { size: 9 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }, grid: { color: '#F3F4F6' } },
          y: { ticks: { color: '#9CA3AF', font: { size: 9 }, callback: v => v + 'W' }, grid: { color: '#F3F4F6' } }
        }
      }
    });
  } catch (e) {
    console.warn('Sessie-grafiek laden mislukt:', e);
  }
}

function formatDuur(seconden) {
  if (!seconden) return '—';
  const u = Math.floor(seconden / 3600);
  const m = Math.floor((seconden % 3600) / 60);
  return u > 0 ? `${u}u ${m}min` : `${m}min`;
}

function renderPaginering(pagina, paginas, totaal) {
  const countEl = document.getElementById('sessie-count');
  if (countEl) countEl.textContent = `${totaal} sessie${totaal !== 1 ? 's' : ''}`;
  const infoEl = document.getElementById('pagina-info');
  if (infoEl) infoEl.textContent = `Pagina ${pagina} van ${paginas}`;
  const vorigeBtn   = document.getElementById('btn-vorige');
  const volgendeBtn = document.getElementById('btn-volgende');
  if (vorigeBtn)   vorigeBtn.disabled   = pagina <= 1;
  if (volgendeBtn) volgendeBtn.disabled = pagina >= paginas;
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

function wisselPagina(delta) {
  laadSessies(huidigePagina + delta);
}
