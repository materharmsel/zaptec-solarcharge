# Dashboard Overhaul — Design Spec
**Datum:** 2026-04-17  
**Status:** Goedgekeurd door gebruiker

---

## Doel

Volledige visuele en structurele overhaul van de webinterface. Het huidige ontwerp is functioneel maar minimaal. Het nieuwe ontwerp moet aanvoelen als een professionele SaaS-applicatie: schoon, donker, futuristisch, met vloeiende animaties en een duidelijke informatiestructuur.

---

## Tech stack toevoegingen

Het systeem draait op een Raspberry Pi zonder build-tooling. Geen Node.js, geen bundler. Alle toevoegingen zijn CDN-gebaseerd — vier `<script>`/`<link>` regels in een gedeelde base-template, verder niks installeren.

| Toevoeging | CDN | Doel |
|---|---|---|
| **Tailwind CSS** (Play CDN) | `cdn.tailwindcss.com` | Alle styling, spacing, kleuren, basis-animaties (`transition`, `duration-*`, `animate-pulse`) |
| **Alpine.js** | `cdn.jsdelivr.net/npm/alpinejs` | Interactieve UI zonder losse JS: sidebar toggle, accordion, dropdowns — via HTML-attributen (`x-data`, `x-show`, `x-transition`) |
| **Chart.js** | `cdn.jsdelivr.net/npm/chart.js` | Echte interactieve grafieken met animaties, tooltips, tijdselectie |
| **Lucide Icons** | `unpkg.com/lucide@latest` | Consistente SVG-iconen, geen emoji's |

Geen React, geen Vue, geen build-stap. De bestaande Flask/Jinja2 + Python setup blijft volledig ongewijzigd.

### Base template
Alle templates erven van een nieuw `templates/base.html` (Jinja2 `{% extends %}`). Daarin zitten: CDN-imports, sidebar, CSS-variabelen (kleurthema), en een `{% block content %}` slot. Zo hoeft elke pagina alleen de eigen inhoud te definiëren.

---

## Kleurthema

**Groen / Zwart (Solar)**

| Token | Waarde | Gebruik |
|---|---|---|
| `--bg-base` | `#0d1117` | Pagina-achtergrond |
| `--bg-surface` | `#111827` | Kaarten |
| `--bg-subtle` | `#0a0f16` | Sidebar, verdiept |
| `--border` | `#1f2937` | Randen |
| `--border-green` | `#1a3a2a` | Groene kaart-randen |
| `--accent` | `#10b981` | Primaire actie / actieve staat |
| `--accent-dim` | `#10b98130` | Glow, achtergrond highlight |
| `--text-primary` | `#f9fafb` | Hoofdtekst |
| `--text-secondary` | `#9ca3af` | Labels |
| `--text-muted` | `#4b5563` | Hints, meta |
| `--green` | `#10b981` | Export / positief |
| `--red` | `#ef4444` | Import / fout |
| `--orange` | `#f97316` | Waarschuwing / noodoverride |
| `--blue` | `#60a5fa` | Laadvermogen |
| `--purple` | `#a78bfa` | Vermogenstrend (EMA) |

---

## Navigatie — Inklapbare sidebar

### Structuur
Smalle icon-only sidebar (52px ingeklapt), uitklapbaar naar 200px via een knop. Op mobiel: sidebar klapt in tot hamburgermenu.

### Uitklapgedrag
- Klik op pijlknop rechtsboven in sidebar → uitklappen
- Instellingen en Systeem zijn **accordion-secties**: klik op de sectie-header om sub-items te tonen/verbergen
- Voorkeur wordt opgeslagen in `localStorage`

### Menu-items

```
⚡  [logo]
──────────────
⊞  Dashboard
~  Sessies
──────────────
◎  Instellingen          ← accordion header
    ⚡  Laadregeling
    🖥  Apparaten
    ⚙  Interface
──────────────
▣  Systeem               ← accordion header
    ↑  Updates
    💾 Backups
──────────────
[onderin]
🔧 Diagnostics           ← alleen zichtbaar als debug_modus=true
```

Alle iconen: Lucide Icons. Geen emoji's.

### Actieve staat
Actieve pagina: groene achtergrond (`--accent-dim`), groene iconkleur (`--accent`).

---

## Dashboard-pagina (`/`)

### 1. Paginaheader
Rij met paginatitel links ("Dashboard") en een status-badge rechts:
- Groene pulserende dot + tekst: "Laden op zonne-energie · SolarFlow · 1-fase"
- Kleur en tekst wisselen per laadmodus (zie bestaande `laadmodus`-logica in `web.py`)

### 2. Drie KPI-kaarten (bovenste rij)

**Kaart 1 — P1 Meter**
- Grote waarde: huidig netvermogen in W (groen als export, rood als import)
- Trend-badge: "exporterend" / "importerend" met pijl
- Subtext: "terug naar net" / "uit net"
- Sparkline rechtsonder: P1 netto over laatste 30 metingen

**Kaart 2 — Laadstroom** *(middenkaard, iets breder)*
- Links: cirkel-gauge (ring) — huidig_stroom_a vs max_stroom_a, getal in het midden
- Rechts: drie verticale fase-balken L1 / L2 / L3
  - Actieve fasen: groene balk + groene waarde + glow
  - Inactieve fasen: donkere lege balk + gedimde "0A"
- Beide elementen vullen de kaart volledig in hoogte en breedte

**Kaart 3 — Vermogenstrend**
- Grote waarde: ema_net_vermogen_w in W (paars)
- Trend-badge: "stabiel" / "stijgend" / "dalend"
- Subtext: "gewogen gemiddelde"
- Sparkline rechtsonder: EMA over laatste 30 metingen

### 3. Controls-strip

Horizontale balk met vier bedieningselementen:
1. **Aan/uit-knop** — groen "Actief" of rood "Uitgeschakeld" (POST `/toggle`)
2. **Algoritme** — dropdown: SolarFlow v1 / Legacy
3. **Doel netvermogen** — dropdown: 0W Neutraal / +50W Veiligheidsmarge / −100W / −200W / Aangepast
4. **Huisprofiel** — dropdown: Rustig / Normaal / Druk / Aangepast

Dropdowns posten direct naar `/instellingen` via kleine AJAX-aanroep (geen paginaverversing).

### 4. Grafiek — Vermogen & Laadstroom

**Tijdselectie** (rechtsboven): `15m` · `30m` · `1u` · `3u`  
**Lijntoggle-knoppen** (rechtsboven, naast tijdselectie): elk klikbaar aan/uit
- 🟢 P1 netto (W)
- 🟣 Trend / EMA (W)
- 🔵 Laadvermogen (W) — berekend: `huidig_stroom_a × spanning_v × huidige_fasen`
- ⬜ Target (gestippelde horizontale lijn op `doel_net_vermogen_w`)

**Grafiek-inhoud:**
- Y-as: −500W tot +500W, nul-lijn duidelijk gemarkeerd
- Export-zone (boven nul): subtiel groen achtergrondvlak
- Import-zone (onder nul): subtiel rood achtergrondvlak
- Lijnvlakken: kleur-fill onder P1 netto en laadvermogen-lijn
- **Event-markers** als verticale streepjes:
  - Fase wissel: blauw bolletje + label "1→3" of "3→1"
  - Noodoverride import: oranje driehoekje omhoog
  - Noodoverride export: oranje driehoekje omlaag
- Legenda voor markers onderaan de grafiek

**Data-ophaal:** `/api/status` geeft al metingen (20 stuks). Voor langere tijdvensters (1u/3u) is een nieuw endpoint `/api/metingen?minuten=60` nodig dat meer metingen retourneert.

---

## Sessies-pagina (`/sessies`)

### Tabel
Kolommen: Datum | Duur | kWh | Score | Model | Expand-knop

### Uitklapbare rij (Optie A)
Klik op expand-knop → rij klapt open, toont:

**Stats-grid (2 rijen × 3 kolommen):**
- Sessiescore (gekleurd: groen ≥75, oranje ≥50, rood <50)
- Gem. afwijking van target (W)
- Totaal geladen (kWh)
- Fase wisselingen (aantal)
- Noodoverride import (aantal)
- Noodoverride export (aantal)

**Mini-grafiek van de sessie:**
- Toont P1 netto + laadvermogen over de duur van die sessie
- Event-markers: fase wisselingen (blauw) en noodoverrides (oranje)
- X-as: tijdstempel van start tot einde sessie
- Data ophalen via nieuw endpoint `/api/sessies/<id>/metingen`

### Paginering
Bestaande paginering blijft: "Vorige / Pagina X van Y / Volgende"

---

## Overige pagina's

De overige pagina's (Laadregeling, Apparaten, Interface, Updates, Backups, Diagnostics) krijgen dezelfde visuele basis: donker thema, sidebar, kaarten. De functionaliteit blijft ongewijzigd — alleen de visuele laag verandert.

Instellingen worden gesplitst van één groot `instellingen.html` naar drie aparte templates:
- `laadregeling.html` — algoritme, stroomlimits, fasemodus, EMA-instellingen
- `apparaten.html` — HomeWizard IP/token, Zaptec IDs, pollintervallen
- `interface.html` — poort, logniveau, retentie, debug-modus

Beheer wordt gesplitst:
- `updates.html` — versie, branch, update-knop
- `backups.html` — backup maken, lijst, rollback

---

## Animaties & microinteracties

*Dit gedeelte wordt door `impeccable` uitgewerkt.* Richtlijnen:
- Kaart hover: subtiele border-glow (`--accent`)
- Expanding rij: vloeiende hoogte-animatie (CSS `grid-template-rows: 0fr → 1fr`)
- Sparklines: fade-in bij laden
- Status-dot: CSS pulse-animatie
- Grafiek-lijnen: Chart.js `animation.duration: 400ms`
- Sidebar uitklappen: CSS `width` transitie

---

## Nieuwe API-endpoints nodig

| Endpoint | Methode | Doel |
|---|---|---|
| `/api/metingen?minuten=N` | GET | Meer metingen voor langere grafiek-tijdvensters |
| `/api/sessies/<id>/metingen` | GET | Meetdata van één specifieke sessie voor mini-grafiek |

---

## Wat niet verandert

- Flask backend, Python, alle bestaande routes
- Config YAML structuur
- Database schema
- Algoritme-logica
- Systemd-integratie
- Bestaande API-endpoints (blijven werken)
