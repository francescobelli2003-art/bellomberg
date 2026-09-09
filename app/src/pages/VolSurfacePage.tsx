import { useEffect, useRef, useState } from 'react';
import { useScalaTesto } from '../lib/svg-kit';
import VolWorkbench from '@/components/VolWorkbench';
import { volRequest } from '@/lib/vol-deck';
import './dashboard-command.css';

// #179 — F12 Volatility Surface: superficie IV da chain Polygon multi-expiry
// v2: hover preciso, palette terminale, cresta ATM, 0DTE esclusi dal plot,
//     ali clippate al p99, stat-strip (expected move / IV-RV / RV pct / earnings)
// v3 (fix 22/07, segnalazione PM smile/plot): connectgaps OFF (i buchi dichiarati
//     dal builder RESTANO buchi — mai superficie inventata), geometria z NON più
//     tagliata (satura solo il COLORE oltre il p99), cresta ATM presa dalla
//     superficie stessa a K/S=1 (prima era la media call/put e galleggiava).
// v4 "VOL DECK" (25/07, sequenza OBSIDIAN F12): vestito .obsx, PLUMBING INTATTO
//     (stesso endpoint, stesso Plotly, stessi calcoli). Strumenti nuovi su dati
//     veri: PROIETTORE DI STRIKE (cono expected-move per scadenza, 1σ/2σ,
//     marker earnings), ALTIMETRO IV RANK (iv_history_context voce (39),
//     tricotomia dichiarata), SEZIONE SMILE X-RAY (curve 2D per scadenza,
//     buchi = gap veri). skew_note/term_slope/smoothing del payload ora RESI.

declare global { interface Window { Plotly?: any } }

function loadPlotly(): Promise<any> {
  return new Promise((resolve, reject) => {
    if (window.Plotly) return resolve(window.Plotly);
    const s = document.createElement('script');
    s.src = new URL('./vendor/plotly-2.32.0.min.js', document.baseURI).href;
    s.onload = () => resolve(window.Plotly);
    s.onerror = () => reject(new Error('Bundle Plotly locale non disponibile'));
    document.head.appendChild(s);
  });
}

function pctile(arr: number[], p: number): number {
  if (!arr.length) return 0;
  const a = [...arr].sort((x, y) => x - y);
  const i = Math.min(a.length - 1, Math.max(0, Math.floor(p * (a.length - 1))));
  return a[i];
}

// prezzo con decimali sensati (stessa filosofia di fmtPx di TerminalChart)
const px = (v: number) =>
  v >= 1000 ? v.toLocaleString('it-IT', { maximumFractionDigits: 0 })
  : v >= 100 ? v.toFixed(1) : v.toFixed(2);

// mix lineare fra due colori RGB (per la scala vicino→lontano dello X-RAY)
function mixc(a: [number, number, number], b: [number, number, number], t: number) {
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

/* cella hero, stesso idioma di F2 (HeroStat è locale a quella pagina) */
function VStat({ label, value, sub, tone, title }: {
  label: string; value: string; tone?: string; sub?: string; title?: string;
}) {
  return (
    <div title={title} style={{ padding: '10px 16px', borderLeft: '1px solid rgba(26,36,64,.6)', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
      <div style={{ fontSize: 9, letterSpacing: '.2em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{label}</div>
      <div className={'num ' + (tone || '')} style={{ fontSize: 17, fontWeight: 300, marginTop: 2, color: tone ? undefined : '#ECF1FA', whiteSpace: 'nowrap' }}>{value}</div>
      {sub && <div style={{ fontSize: 9, fontWeight: 600, color: '#73829F', marginTop: 2, whiteSpace: 'nowrap' }}>{sub}</div>}
    </div>
  );
}

/* ============================================================
   PROIETTORE DI STRIKE — strumento-firma F12: il cono expected-move
   per scadenza (spot ± ATM IV × √T), 1σ banda piena + 2σ tratteggiata,
   asse √t (il front respira), marker EARNINGS, tooltip con gli strike.
   È il prezzo DELLE OPZIONI, non una previsione: dichiarato in legenda.
   ============================================================ */
function StrikeProjector({ spot, term, earnings }: { spot: number; term: any[]; earnings?: string | null }) {
  const pts = (term || []).filter(s => s.days >= 2 && s.atm_iv != null && isFinite(s.atm_iv) && s.atm_iv > 0);
  if (!(spot > 0) || pts.length < 2) {
    return <div className="num" style={{ padding: '14px 12px', fontSize: 9, fontWeight: 600, color: '#73829F' }}>
      n.d. — servono spot e almeno 2 scadenze utilizzabili (0-1 DTE escluse a monte)
    </div>;
  }
  const W = 332, H = 236, L = 46, R = 62, T = 12, B = 24;
  const maxD = pts[pts.length - 1].days;
  const sx = (d: number) => L + (W - L - R) * Math.sqrt(Math.max(0, d) / maxD);
  const sig = (s: any, k: number) => s.atm_iv * Math.sqrt(s.days / 365) * k;
  const pad = Math.max(...pts.map(p => sig(p, 2))) * 1.08;
  const sy = (rel: number) => T + (H - T - B) * (1 - (rel + pad) / (2 * pad));
  const apex = `${sx(0).toFixed(1)},${sy(0).toFixed(1)}`;
  const band = (k: number) => 'M' + apex
    + pts.map(p => `L${sx(p.days).toFixed(1)},${sy(sig(p, k)).toFixed(1)}`).join('')
    + [...pts].reverse().map(p => `L${sx(p.days).toFixed(1)},${sy(-sig(p, k)).toFixed(1)}`).join('') + 'Z';
  const line2 = (up: boolean) => 'M' + apex
    + pts.map(p => `L${sx(p.days).toFixed(1)},${sy(sig(p, up ? 2 : -2)).toFixed(1)}`).join('');
  // griglia orizzontale a passo "pulito" (~3-5 linee dentro la banda)
  let step = 1;
  for (const c of [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2]) { if (2 * pad / c <= 6) { step = c; break; } }
  const gl: number[] = [];
  for (let v = step; v < pad; v += step) { gl.push(v); gl.push(-v); }
  // marker earnings se dentro la finestra delle scadenze
  const eT = earnings ? Date.parse(earnings + 'T00:00:00Z') : NaN;
  const eDays = isFinite(eT) ? Math.ceil((eT - Date.now()) / 86400000) : null;
  const showE = eDays != null && eDays > 0 && eDays <= maxD;
  // etichette asse x senza affollamento (√t comprime il fondo)
  let lastLx = -999;
  const xt = pts.map((p, i) => {
    const x = sx(p.days);
    const show = i === 0 || i === pts.length - 1 || x - lastLx > 30;
    if (show) lastLx = x;
    return { x, d: p.days, show };
  });
  const last = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="vsxsvg" role="img"
         aria-label="Cono expected move per scadenza: spot più/meno ATM IV per radice del tempo">
      {gl.map((v, i) => (
        <g key={i}>
          <line x1={L} x2={W - R} y1={sy(v)} y2={sy(v)} stroke="#131C33" strokeWidth="1" />
          <text x={L - 4} y={sy(v) + 3} fontSize="9" fontWeight={600} fill="#73829F" textAnchor="end" fontFamily="monospace">
            {(v > 0 ? '+' : '') + Math.round(v * 100) + '%'}
          </text>
        </g>
      ))}
      <path d={band(2)} fill="rgba(41,211,242,.045)" />
      <path d={band(1)} fill="rgba(41,211,242,.13)" stroke="rgba(41,211,242,.6)" strokeWidth="1" />
      <path d={line2(true)} fill="none" stroke="rgba(41,211,242,.3)" strokeWidth="1" strokeDasharray="3 3" />
      <path d={line2(false)} fill="none" stroke="rgba(41,211,242,.3)" strokeWidth="1" strokeDasharray="3 3" />
      {/* spot: la linea di fede dello strumento */}
      <line x1={L} x2={W - R} y1={sy(0)} y2={sy(0)} stroke="#95A1BA" strokeWidth="1" strokeDasharray="1 3" />
      <text x={L - 4} y={sy(0) + 3} fontSize="9" fill="#8D9FC4" textAnchor="end" fontFamily="monospace">{px(spot)}</text>
      {/* earnings: il premio evento reso visibile dove vive */}
      {showE && <g>
        <line x1={sx(eDays!)} x2={sx(eDays!)} y1={T} y2={H - B} stroke="rgba(255,165,30,.65)" strokeWidth="1" strokeDasharray="4 3" />
        <text x={sx(eDays!) + 3} y={T + 9} fontSize="9" fill="#FFA51E" fontFamily="monospace">E {earnings!.slice(8, 10)}/{earnings!.slice(5, 7)}</text>
      </g>}
      {/* ticks scadenze + punti 1σ con tooltip strike */}
      {xt.map((t, i) => (
        <g key={i}>
          <line x1={t.x} x2={t.x} y1={H - B} y2={H - B + 3} stroke="#2A3760" strokeWidth="1" />
          {t.show && <text x={t.x} y={H - B + 13} fontSize="9" fontWeight={600} fill="#73829F" textAnchor="middle" fontFamily="monospace">{t.d}g</text>}
        </g>
      ))}
      {pts.map((p, i) => {
        const s1 = sig(p, 1);
        return (
          <g key={i}>
            <circle cx={sx(p.days)} cy={sy(s1)} r="2.2" fill="#29D3F2">
              <title>{p.expiry} · +{(s1 * 100).toFixed(1)}% → {px(spot * (1 + s1))}</title>
            </circle>
            <circle cx={sx(p.days)} cy={sy(-s1)} r="2.2" fill="#29D3F2">
              <title>{p.expiry} · −{(s1 * 100).toFixed(1)}% → {px(spot * (1 - s1))}</title>
            </circle>
          </g>
        );
      })}
      {/* strike 1σ all'ultima scadenza: il perimetro a fine finestra */}
      <text x={W - R + 5} y={sy(sig(last, 1)) + 2.5} fontSize="9" fill="#29D3F2" fontFamily="monospace">
        {px(spot * (1 + sig(last, 1)))} · +{(sig(last, 1) * 100).toFixed(0)}%
      </text>
      <text x={W - R + 5} y={sy(-sig(last, 1)) + 2.5} fontSize="9" fill="#29D3F2" fontFamily="monospace">
        {px(spot * (1 - sig(last, 1)))} · −{(sig(last, 1) * 100).toFixed(0)}%
      </text>
      <text x={W - R + 5} y={sy(0) + 3} fontSize="9" fontWeight={600} fill="#73829F" fontFamily="monospace">SPOT</text>
    </svg>
  );
}

/* ============================================================
   ALTIMETRO IV RANK — iv_history_context (voce (39) backend), con la
   tricotomia di casa: chiave ASSENTE = backend pre-riavvio (dichiarato),
   error = verbatim (storia insufficiente / tabella assente), dato =
   scala 0-100 con YOUNG urlato finché la storia non matura. Mai un
   rank spacciato per maturo.
   ============================================================ */
function IvAltimeter({ ctx }: { ctx: any }) {
  if (ctx === undefined) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 9, fontWeight: 600, color: '#73829F', lineHeight: 1.7 }}>
      n.d. — STORICO IV NON ESPOSTO DAL BACKEND VIVO<br />
      <span style={{ color: '#B97A00' }}>SI ACCENDE DA SOLO AL RIAVVIO (VOCE (39)) · RACCOLTA PARTITA COL SEED DEL 24/07</span>
    </div>;
  }
  if (!ctx || ctx.error) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 9, color: '#B97A00', lineHeight: 1.7 }}>
      DICHIARATO DAL BACKEND: {String(ctx?.error || 'contesto vuoto')}
      {ctx?.n_obs != null && <span style={{ fontWeight: 600, color: '#73829F' }}> · {ctx.n_obs} oss. raccolte</span>}
    </div>;
  }
  const pct = Math.max(0, Math.min(100, Number(ctx.iv_percentile)));
  const H = 178, top = 12, bot = 166;
  const y = (p: number) => bot - (bot - top) * (p / 100);
  const tone = pct >= 80 ? '#FF3D60' : pct >= 60 ? '#FFA51E' : pct <= 20 ? '#21E0A0' : '#29D3F2';
  return (
    <div style={{ display: 'flex', gap: 12, padding: '8px 12px 10px', alignItems: 'stretch' }}>
      <svg viewBox={`0 0 60 ${H}`} width="60" height={H} style={{ flex: '0 0 60px' }} role="img" aria-label={`IV rank ${pct} su 100`}>
        <line x1="40" x2="40" y1={top} y2={bot} stroke="#2A3760" strokeWidth="1.5" />
        {Array.from({ length: 11 }, (_, i) => i * 10).map(p => (
          <g key={p}>
            <line x1={p % 50 === 0 ? 30 : 34} x2="40" y1={y(p)} y2={y(p)} stroke="#2A3760" strokeWidth="1" />
            {p % 50 === 0 && <text x="26" y={y(p) + 3} fontSize="9" fontWeight={600} fill="#73829F" textAnchor="end" fontFamily="monospace">{p}</text>}
          </g>
        ))}
        <line x1="40" x2="48" y1={y(pct)} y2={y(pct)} stroke={tone} strokeWidth="2" />
        <path d={`M48,${y(pct)} l7,-4 v8 Z`} fill={tone} />
      </svg>
      <div className="num" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 3, minWidth: 0 }}>
        <div style={{ fontSize: 9, letterSpacing: '.2em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase' }}>IV RANK · PERCENTILE STORICO</div>
        <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1, color: tone }}>{ctx.iv_percentile}<span style={{ fontSize: 11, fontWeight: 400 }}>°</span></div>
        <div style={{ fontSize: 9, color: '#8D9FC4' }}>ATM front {(ctx.iv_front_current * 100).toFixed(1)}%
          <span style={{ fontWeight: 600, color: '#73829F' }}> · min {(ctx.iv_min * 100).toFixed(1)} · max {(ctx.iv_max * 100).toFixed(1)}</span></div>
        <div style={{ fontSize: 9, fontWeight: 600, color: '#73829F', textTransform: 'uppercase', letterSpacing: '.08em' }}>
          {ctx.n_obs} oss. dal {ctx.history_from}
        </div>
        {ctx.young && (
          <span className="chip a" style={{ alignSelf: 'flex-start' }}
                title={`storia sotto ${ctx.young_threshold_obs ?? 60} osservazioni: il percentile ha un floor strutturale 100/n — non confrontarlo con un rank maturo`}>
            STORIA GIOVANE {ctx.n_obs}/{ctx.young_threshold_obs ?? 60}
          </span>
        )}
      </div>
    </div>
  );
}

/* ============================================================
   SEZIONE SMILE // X-RAY — curve smile 2D per scadenza, INTERATTIVA
   (richiesta PM 25/07 live: "premendo sul grafico vedo l'IV"):
   crosshair oro sul punto griglia più vicino al puntatore (press o
   hover) + lettura sotto il grafico: K/S, strike implicito e IV di
   OGNI scadenza in quel punto. I null del builder qui RESTANO buchi
   (n.d. nella lettura): questa è la sezione FEDELE — l'interpolazione
   vive solo nel mesh 3D, dichiarata in legenda.
   ============================================================ */
// larghezza del viewBox dello smile: serve al componente E all'hook che
// compensa la scala del testo, quindi vive qui e non dentro il corpo.
const XR_W = 1400;
const XR_C0: [number, number, number] = [41, 211, 242];   // vicine = cyan
const XR_C1: [number, number, number] = [155, 123, 255];  // lontane = viola
function SmileXray({ grid, slices, spot }: { grid: number[]; slices: any[]; spot?: number }) {
  const [sel, setSel] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  // PRIMA del return anticipato qui sotto: gli hook non stanno dietro a un if.
  // Misurato: questo SVG e' reso a 1302px a `terzo` (0,93x) ma a 3009px col
  // Windows al 150% (2,15x), dove un 10,5px diventava 22,57px — il doppio di
  // tutto il resto della pagina. Il difetto va nei DUE versi, non solo giu'.
  const kT = useScalaTesto(svgRef, XR_W);
  const use = (slices || []).filter((s: any) => s.days >= 2 && (s.iv_grid || []).some((v: any) => v != null));
  if (!grid?.length || use.length < 1) {
    return <div className="num" style={{ padding: '12px', fontSize: 9, fontWeight: 600, color: '#73829F' }}>n.d. — nessuna curva utilizzabile (0-1 DTE escluse)</div>;
  }
  const vals = use.flatMap((s: any) => s.iv_grid.filter((v: any) => v != null && isFinite(v)).map((v: number) => v * 100));
  const vmin = Math.min(...vals), vmax = Math.max(...vals);
  const padv = Math.max(0.5, (vmax - vmin) * 0.06);
  // viewBox largo: su un contenitore ~1900px il fattore di scala resta ~1.3
  // (coi 660 di prima i font scalavano giganti — collaudo 2560)
  const W = XR_W, H = 250, L = 64, R = 20, T = 14, B = 26;
  const gmin = grid[0], gmax = grid[grid.length - 1];
  const X = (m: number) => L + (W - L - R) * (m - gmin) / (gmax - gmin);
  const Y = (v: number) => T + (H - T - B) * (1 - (v - (vmin - padv)) / ((vmax + padv) - (vmin - padv)));
  const pick = (clientX: number) => {
    const el = svgRef.current; if (!el) return;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0)) return;
    const vx = (clientX - r.left) / r.width * W;
    let best = 0, bd = Infinity;
    grid.forEach((m, i) => { const d = Math.abs(X(m) - vx); if (d < bd) { bd = d; best = i; } });
    setSel(best);
  };
  return (
    <>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="vsxsvg" role="img"
           aria-label="Curve smile IV per scadenza: premi o passa sul grafico per leggere l'IV"
           style={{ cursor: 'crosshair', touchAction: 'none' }}
           onPointerDown={e => pick(e.clientX)}
           onPointerMove={e => { if (e.pointerType === 'mouse' || e.buttons > 0) pick(e.clientX); }}>
        {[vmin, (vmin + vmax) / 2, vmax].map((v, i) => (
          <g key={i}>
            <line x1={L} x2={W - R} y1={Y(v)} y2={Y(v)} stroke="#131C33" strokeWidth="1" />
            <text x={L - 6} y={Y(v) + 3} fontSize={11 * kT} fontWeight={600} fill="#73829F" textAnchor="end" fontFamily="monospace">{v.toFixed(0)}%</text>
          </g>
        ))}
        <line x1={X(1)} x2={X(1)} y1={T} y2={H - B} stroke="#2A3760" strokeWidth="1" strokeDasharray="2 3" />
        <text x={X(1)} y={H - B + 13} fontSize={11 * kT} fill="#8D9FC4" textAnchor="middle" fontFamily="monospace">ATM</text>
        {[gmin, 0.9, 1.1, gmax].map((m, i) => (
          <text key={i} x={X(m)} y={H - B + 13} fontSize={10 * kT} fontWeight={600} fill="#73829F" textAnchor="middle" fontFamily="monospace">{m.toFixed(2)}</text>
        ))}
        {use.map((s: any, si: number) => {
          const t = use.length > 1 ? si / (use.length - 1) : 0;
          const col = mixc(XR_C0, XR_C1, t);
          let d = '', pen = false;
          s.iv_grid.forEach((v: any, i: number) => {
            if (v == null || !isFinite(v)) { pen = false; return; }
            d += `${pen ? 'L' : 'M'}${X(grid[i]).toFixed(1)},${Y(v * 100).toFixed(1)}`;
            pen = true;
          });
          return (
            <path key={si} d={d} fill="none" stroke={col} strokeWidth={si === 0 ? 2 : 1.4}
                  opacity={0.95 - 0.5 * t}>
              <title>{s.expiry} · {s.days}g</title>
            </path>
          );
        })}
        {/* crosshair: colonna selezionata + nodi vivi su ogni curva che ha il dato */}
        {sel != null && (
          <g pointerEvents="none">
            <line x1={X(grid[sel])} x2={X(grid[sel])} y1={T} y2={H - B} stroke="rgba(255,209,102,.85)" strokeWidth="1" strokeDasharray="4 3" />
            {use.map((s: any, si: number) => {
              const v = s.iv_grid[sel];
              if (v == null || !isFinite(v)) return null;
              const t = use.length > 1 ? si / (use.length - 1) : 0;
              return <circle key={si} cx={X(grid[sel])} cy={Y(v * 100)} r="3.4"
                             fill={mixc(XR_C0, XR_C1, t)} stroke="#070B16" strokeWidth="1.2" />;
            })}
          </g>
        )}
        <text x={W - R} y={T + 12} fontSize="11" fill="#29D3F2" textAnchor="end" fontFamily="monospace">
          FRONT {use[0].expiry} · {use[0].days}g
        </text>
      </svg>
      {/* lettura del crosshair: IV per scadenza al K/S selezionato, buchi = n.d. */}
      <div className="num" style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', padding: '5px 10px 2px', fontSize: 11, alignItems: 'baseline', minHeight: 24 }}>
        {sel == null ? (
          <span style={{ fontWeight: 600, color: '#73829F', fontSize: 10, letterSpacing: '.08em' }}>PREMI O PASSA SUL GRAFICO → IV DI OGNI SCADENZA A QUEL K/S</span>
        ) : (
          <>
            <span style={{ color: '#FFD166', fontWeight: 700 }}>
              K/S {grid[sel].toFixed(3)}{spot != null && spot > 0 ? ' · STRIKE ~' + px(spot * grid[sel]) : ''}
            </span>
            {use.map((s: any, si: number) => {
              const v = s.iv_grid[sel];
              const t = use.length > 1 ? si / (use.length - 1) : 0;
              return (
                <span key={si} style={{ color: mixc(XR_C0, XR_C1, t), opacity: v == null ? 0.45 : 1 }}>
                  {s.expiry.slice(8, 10)}/{s.expiry.slice(5, 7)}{' '}
                  <b>{v == null || !isFinite(v) ? 'n.d.' : (v * 100).toFixed(1) + '%'}</b>
                </span>
              );
            })}
          </>
        )}
      </div>
    </>
  );
}

/* palette del mesh riusata per la vista top-down (coerenza fra le due rese) */
const HEAT_STOPS: [number, [number, number, number]][] = [
  [0, [11, 37, 69]], [0.35, [27, 73, 101]], [0.62, [42, 157, 184]], [0.85, [156, 128, 48]], [1, [212, 175, 55]],
];
function heatColor(t: number) {
  const x = Math.max(0, Math.min(1, t));
  for (let i = 1; i < HEAT_STOPS.length; i++) {
    if (x <= HEAT_STOPS[i][0]) {
      const [a, ca] = HEAT_STOPS[i - 1], [b, cb] = HEAT_STOPS[i];
      return mixc(ca, cb, (x - a) / ((b - a) || 1));
    }
  }
  return mixc(HEAT_STOPS[3][1], HEAT_STOPS[4][1], 1);
}
const kfmt = (n: number) => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);

/* ============================================================
   VISTA DESK (richiesta PM 25/07 live: "due pagine, più lettura") —
   strumenti DERIVATI dal payload con formula dichiarata, come il
   rolling di F2: niente numeri inventati, solo trasformazioni rese.
   ============================================================ */

/* TERM STRUCTURE 2D — curva ATM oro vs RV30 realizzata (il carry visivo) */
function Term2D({ term, earnings, rv30 }: { term: any[]; earnings?: string | null; rv30?: number | null }) {
  // stesso difetto dello smile, stesso rimedio, e l'hook sta PRIMA del
  // return anticipato della riga dopo: l'ordine degli hook non si tocca.
  const svgRef2 = useRef<SVGSVGElement>(null);
  const kT = useScalaTesto(svgRef2, XR_W);
  const pts = (term || []).filter(s => s.days >= 2 && s.atm_iv != null && isFinite(s.atm_iv));
  if (pts.length < 2) return <div className="num" style={{ padding: '12px', fontSize: 9, fontWeight: 600, color: '#73829F' }}>n.d. — servono ≥2 scadenze</div>;
  const W = XR_W, H = 230, L = 64, R = 26, T = 18, B = 28;
  const maxD = pts[pts.length - 1].days;
  const X = (d: number) => L + (W - L - R) * Math.sqrt(Math.max(0, d) / maxD);
  const ivs = pts.map(p => p.atm_iv * 100);
  const rv = rv30 != null && isFinite(rv30) ? rv30 * 100 : null;
  const all = rv != null ? [...ivs, rv] : ivs;
  const vmin = Math.min(...all), vmax = Math.max(...all);
  const pad = Math.max(0.5, (vmax - vmin) * 0.14);
  const Y = (v: number) => T + (H - T - B) * (1 - (v - (vmin - pad)) / ((vmax + pad) - (vmin - pad)));
  const dPath = pts.map((p, i) => `${i ? 'L' : 'M'}${X(p.days).toFixed(1)},${Y(p.atm_iv * 100).toFixed(1)}`).join('');
  const eT = earnings ? Date.parse(earnings + 'T00:00:00Z') : NaN;
  const eDays = isFinite(eT) ? Math.ceil((eT - Date.now()) / 86400000) : null;
  const showE = eDays != null && eDays > 0 && eDays <= maxD;
  let lastLx = -999;
  return (
    <svg ref={svgRef2} viewBox={`0 0 ${W} ${H}`} className="vsxsvg" role="img" aria-label="ATM IV per scadenza contro volatilità realizzata 30 giorni">
      {[vmin, (vmin + vmax) / 2, vmax].map((v, i) => (
        <g key={i}>
          <line x1={L} x2={W - R} y1={Y(v)} y2={Y(v)} stroke="#131C33" strokeWidth="1" />
          <text x={L - 6} y={Y(v) + 3} fontSize={11 * kT} fontWeight={600} fill="#73829F" textAnchor="end" fontFamily="monospace">{v.toFixed(0)}%</text>
        </g>
      ))}
      {rv != null && (
        <g>
          <line x1={L} x2={W - R} y1={Y(rv)} y2={Y(rv)} stroke="#95A1BA" strokeWidth="1" strokeDasharray="5 4" opacity=".7" />
          <text x={W - R} y={Y(rv) - 4} fontSize={11 * kT} fill="#8D9FC4" textAnchor="end" fontFamily="monospace">RV 30G {rv.toFixed(1)}%</text>
        </g>
      )}
      {showE && (
        <g>
          <line x1={X(eDays!)} x2={X(eDays!)} y1={T} y2={H - B} stroke="rgba(255,165,30,.65)" strokeWidth="1" strokeDasharray="4 3" />
          <text x={X(eDays!) + 4} y={T + 9} fontSize={10 * kT} fill="#FFA51E" fontFamily="monospace">E {earnings!.slice(8, 10)}/{earnings!.slice(5, 7)}</text>
        </g>
      )}
      <path d={dPath} fill="none" stroke="#FFD166" strokeWidth="2" />
      {pts.map((p, i) => {
        const x = X(p.days);
        const showL = i === 0 || i === pts.length - 1 || x - lastLx > 60;
        if (showL) lastLx = x;
        return (
          <g key={i}>
            <circle cx={x} cy={Y(p.atm_iv * 100)} r="3.2" fill="#FFD166" stroke="#070B16" strokeWidth="1">
              <title>{p.expiry} · {p.days}g · ATM {(p.atm_iv * 100).toFixed(1)}%</title>
            </circle>
            {showL && <text x={x} y={H - B + 13} fontSize={10 * kT} fontWeight={600} fill="#73829F" textAnchor="middle" fontFamily="monospace">{p.days}g</text>}
            {(i === 0 || i === pts.length - 1) && (
              <text x={x} y={Y(p.atm_iv * 100) - 8} fontSize={11 * kT} fill="#FFD166" textAnchor="middle" fontFamily="monospace">{(p.atm_iv * 100).toFixed(1)}</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/* SURFACE TOP-DOWN // HEAT — la verità cella per cella (buchi = celle scure).
   v4-ter (feedback PM live "le scritte laterali non si leggono"): resa HTML
   con etichette a PX FISSI — la leggibilità non scala più col contenitore. */
function HeatTopDown({ grid, slices }: { grid: number[]; slices: any[] }) {
  const use = (slices || []).filter((s: any) => s.days >= 2);
  if (!grid?.length || !use.length) return <div className="num" style={{ padding: '12px', fontSize: 10, fontWeight: 600, color: '#73829F' }}>n.d.</div>;
  const vals = use.flatMap((s: any) => s.iv_grid.filter((v: any) => v != null && isFinite(v)));
  if (!vals.length) return <div className="num" style={{ padding: '12px', fontSize: 10, fontWeight: 600, color: '#73829F' }}>n.d. — griglia vuota</div>;
  const vmin = Math.min(...vals), vmax = Math.max(...vals);
  const iAtm = grid.indexOf(1.0);
  const LBL = 108;
  return (
    <div style={{ padding: '2px 10px 0' }} role="img" aria-label="Vista dall'alto della superficie IV: scadenze per moneyness">
      {use.map((s: any, r: number) => (
        <div key={r} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
          <span className="num" style={{ flex: `0 0 ${LBL}px`, fontSize: 10, color: '#8D9FC4', textAlign: 'right' }}>
            {s.expiry.slice(8, 10)}/{s.expiry.slice(5, 7)} · {s.days}g
          </span>
          <div style={{ flex: 1, display: 'flex', gap: 1, height: 24 }}>
            {grid.map((m: number, c: number) => {
              const v = s.iv_grid[c];
              const ok = v != null && isFinite(v);
              return (
                <div key={c}
                     title={`${s.expiry} · K/S ${m.toFixed(3)} · ${ok ? 'IV ' + (v * 100).toFixed(1) + '%' : 'n.d. — quota assente (buco dichiarato)'}`}
                     style={{ flex: 1, background: ok ? heatColor((v - vmin) / ((vmax - vmin) || 1)) : '#070B16',
                              boxShadow: c === iAtm ? 'inset 0 0 0 1px rgba(236,241,250,.45)' : 'inset 0 0 0 0.5px #0D1426' }} />
              );
            })}
          </div>
        </div>
      ))}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 3 }}>
        <span style={{ flex: `0 0 ${LBL}px` }} />
        <div style={{ flex: 1, display: 'flex', gap: 1 }}>
          {grid.map((m: number, c: number) => {
            const show = c === 0 || c === grid.length - 1 || c === iAtm || c === Math.floor(grid.length / 4) || c === Math.floor(3 * grid.length / 4);
            return (
              <span key={c} className="num" style={{ flex: 1, fontSize: 10, color: c === iAtm ? '#ECF1FA' : '#73829F', textAlign: 'center' }}>
                {show ? (c === iAtm ? 'ATM' : m.toFixed(2)) : ''}
              </span>
            );
          })}
        </div>
      </div>
      <div className="num" style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '7px 0 2px' }}>
        <span style={{ flex: `0 0 ${LBL}px`, fontSize: 10, fontWeight: 600, color: '#73829F', textAlign: 'right' }}>SCALA IV</span>
        <div style={{ flex: '0 0 190px', height: 8, background: `linear-gradient(90deg, ${heatColor(0)}, ${heatColor(0.35)}, ${heatColor(0.62)}, ${heatColor(0.85)}, ${heatColor(1)})` }} />
        <span style={{ fontSize: 10, color: '#8D9FC4' }}>{(vmin * 100).toFixed(0)}% → {(vmax * 100).toFixed(0)}%</span>
      </div>
    </div>
  );
}

/* FORWARD VOL — varianza forward fra scadenze consecutive: dove la curva
   prezza gli eventi. σ_fwd = √((σ2²·T2 − σ1²·T1)/(T2−T1)); varianza negativa
   = curva invertita, DICHIARATA (non un numero inventato). */
function FwdVolLadder({ term }: { term: any[] }) {
  const pts = (term || []).filter(s => s.days >= 2 && s.atm_iv != null && s.atm_iv > 0);
  if (pts.length < 2) return <div className="num" style={{ padding: '12px', fontSize: 9, fontWeight: 600, color: '#73829F' }}>n.d. — servono ≥2 scadenze</div>;
  const rows: { lab: string; fwd: number | null }[] = [];
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    const T1 = a.days / 365, T2 = b.days / 365;
    const vf = (b.atm_iv * b.atm_iv * T2 - a.atm_iv * a.atm_iv * T1) / (T2 - T1);
    rows.push({ lab: `${a.days}g→${b.days}g`, fwd: vf > 0 ? Math.sqrt(vf) : null });
  }
  const mx = Math.max(...rows.map(r => r.fwd ?? 0), 0.0001);
  return (
    <div style={{ padding: '6px 12px 8px', display: 'flex', flexDirection: 'column', gap: 5 }}>
      {rows.map((r, i) => (
        <div key={i} className="num" style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10 }}>
          <span style={{ width: 82, fontWeight: 600, color: '#73829F' }}>{r.lab}</span>
          <span style={{ flex: 1, height: 6, background: 'rgba(26,36,64,.6)', position: 'relative' }}>
            {r.fwd != null && <i style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: (100 * r.fwd / mx) + '%', background: 'rgba(255,209,102,.6)' }} />}
          </span>
          <span style={{ width: 82, textAlign: 'right', color: r.fwd == null ? '#FFA51E' : '#ECF1FA' }}>
            {r.fwd == null ? 'INVERTITA' : (r.fwd * 100).toFixed(1) + '%'}
          </span>
        </div>
      ))}
    </div>
  );
}

/* OPEN INTEREST — posizionamento put/call per scadenza (dal payload) */
function OiProfile({ term }: { term: any[] }) {
  const pts = (term || []).filter(s => (s.call_oi || 0) + (s.put_oi || 0) > 0);
  if (!pts.length) return <div className="num" style={{ padding: '12px', fontSize: 9, fontWeight: 600, color: '#73829F' }}>n.d. — OI non nel payload</div>;
  const mx = Math.max(...pts.map(p => Math.max(p.call_oi || 0, p.put_oi || 0)), 1);
  return (
    <div style={{ padding: '6px 12px 8px', display: 'flex', flexDirection: 'column', gap: 5 }}>
      {pts.map((p, i) => (
        <div key={i} className="num" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10 }}
             title={`${p.expiry} · put OI ${p.put_oi ?? 'n.d.'} · call OI ${p.call_oi ?? 'n.d.'} · P/C ${p.pc_oi_ratio ?? 'n.d.'}`}>
          <span style={{ width: 48, fontWeight: 600, color: '#73829F' }}>{p.expiry.slice(8, 10)}/{p.expiry.slice(5, 7)}</span>
          <span style={{ flex: 1, display: 'flex', justifyContent: 'flex-end', height: 6, background: 'rgba(26,36,64,.4)' }}>
            <i style={{ width: (100 * (p.put_oi || 0) / mx) + '%', background: 'rgba(255,61,96,.65)' }} />
          </span>
          <span style={{ flex: 1, display: 'flex', height: 6, background: 'rgba(26,36,64,.4)' }}>
            <i style={{ width: (100 * (p.call_oi || 0) / mx) + '%', background: 'rgba(33,224,160,.6)' }} />
          </span>
          <span style={{ width: 48, textAlign: 'right', color: p.pc_oi_ratio != null && p.pc_oi_ratio > 1.5 ? '#FFA51E' : '#8D9FC4' }}>
            {p.pc_oi_ratio != null ? p.pc_oi_ratio.toFixed(2) : 'n.d.'}
          </span>
        </div>
      ))}
      <div className="num" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, fontWeight: 600, color: '#73829F', letterSpacing: '.1em' }}>
        <span>◄ PUT OI (max {kfmt(mx)})</span><span>CALL OI ► · colonna dx = P/C</span>
      </div>
    </div>
  );
}

/* ============================================================
   VOL CONE — endpoint (43) backend `GET /options/vol_cone/{ticker}`:
   realized vol per orizzonte (5/10/21/63 giorni di BORSA, percentili 1y)
   vs term structure implicita corrente; `confronto[]` = percentile
   dell'IV nella distribuzione realized della finestra abbinata
   (calendario→borsa ×252/365, fix M1 review). 404 = pre-riavvio,
   dichiarato: si accende da solo. Solo ticker della lista IV (SPY/MSTR
   oggi): fuori lista = error dichiarato dal backend, reso verbatim.
   ============================================================ */
function VolCone({ cone }: { cone: any }) {
  const [selW, setSelW] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  if (cone?.__loading) {
    return <div className="num" style={{ padding: '12px', fontSize: 11, fontWeight: 600, color: '#73829F' }}>interrogo /options/vol_cone…</div>;
  }
  if (cone === undefined) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 11, fontWeight: 600, color: '#73829F', lineHeight: 1.7 }}>
      n.d. — ENDPOINT /options/vol_cone NON ANCORA ATTIVO SUL BACKEND VIVO<br />
      <span style={{ color: '#B97A00' }}>SI ACCENDE DA SOLO AL RIAVVIO (VOCE (43), commit 588224f)</span>
    </div>;
  }
  if (!cone || cone.error) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 11, color: '#B97A00', lineHeight: 1.7 }}>
      DICHIARATO DAL BACKEND: {String(cone?.error || 'payload vuoto')}
    </div>;
  }
  const wins = (cone.realized?.windows || []).filter((w: any) => !w.error && w.current != null);
  const missing = (cone.realized?.windows || []).filter((w: any) => w.error);
  if (wins.length < 2) {
    return <div className="num" style={{ padding: '12px', fontSize: 11, color: '#B97A00' }}>
      n.d. — {missing.length ? String(missing[0].error) : 'meno di 2 finestre realized utilizzabili'}
    </div>;
  }
  const ivPts = (!cone.implied?.error ? (cone.confronto || []) : []).filter((c: any) => c.atm_iv != null && c.window != null);
  const W = 1400, H = 350, L = 84, R = 36, T = 26, B = 44;
  const maxW = wins[wins.length - 1].window;
  const X = (w: number) => L + (W - L - R) * Math.sqrt(Math.max(0, w) / maxW);
  const allV = [...wins.flatMap((w: any) => [w.min, w.max, w.current]), ...ivPts.map((c: any) => c.atm_iv)]
    .filter((v: any) => v != null && isFinite(v)).map((v: number) => v * 100);
  const vmin = Math.min(...allV), vmax = Math.max(...allV);
  const pad = Math.max(0.5, (vmax - vmin) * 0.1);
  const Y = (v: number) => T + (H - T - B) * (1 - (v - (vmin - pad)) / ((vmax + pad) - (vmin - pad)));
  const area = (lo: string, hi: string) => 'M'
    + wins.map((w: any, i: number) => `${i ? 'L' : ''}${X(w.window).toFixed(1)},${Y(w[hi] * 100).toFixed(1)}`).join('')
    + [...wins].reverse().map((w: any) => `L${X(w.window).toFixed(1)},${Y(w[lo] * 100).toFixed(1)}`).join('') + 'Z';
  const line = (k: string) => wins.map((w: any, i: number) => `${i ? 'L' : 'M'}${X(w.window).toFixed(1)},${Y(w[k] * 100).toFixed(1)}`).join('');
  // assi a passo PULITO (multipli tondi, mai min/mid/max casuali)
  let step = 100;
  for (const c of [2, 5, 10, 20, 25, 50, 100]) { if ((vmax - vmin) / c <= 5) { step = c; break; } }
  const glines: number[] = [];
  for (let v = Math.ceil((vmin - pad) / step) * step; v <= vmax + pad; v += step) glines.push(v);
  // interazione: press/hover → finestra più vicina (stesso idioma dello X-RAY)
  const pick = (clientX: number) => {
    const el = svgRef.current; if (!el) return;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0)) return;
    const vx = (clientX - r.left) / r.width * W;
    let best: number | null = null, bd = Infinity;
    wins.forEach((w: any) => { const d = Math.abs(X(w.window) - vx); if (d < bd) { bd = d; best = w.window; } });
    setSelW(best);
  };
  const sel = wins.find((w: any) => w.window === selW) || null;
  const selIvs = ivPts.filter((c: any) => c.window === selW);
  // marker IV nella stessa finestra: sfalsamento DETERMINISTICO per indice
  const seen: Record<number, number> = {};
  return (
    <>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="vsxsvg" role="img"
           aria-label="Vol cone interattivo: percentili della realized per orizzonte contro IV implicita per scadenza"
           style={{ cursor: 'crosshair', touchAction: 'none' }}
           onPointerDown={e => pick(e.clientX)}
           onPointerMove={e => { if (e.pointerType === 'mouse' || e.buttons > 0) pick(e.clientX); }}>
        {glines.map((v, i) => (
          <g key={i}>
            <line x1={L} x2={W - R} y1={Y(v)} y2={Y(v)} stroke="#131C33" strokeWidth="1" />
            <text x={L - 8} y={Y(v) + 4} fontSize="12" fontWeight={600} fill="#73829F" textAnchor="end" fontFamily="monospace">{v}%</text>
          </g>
        ))}
        <path d={area('min', 'max')} fill="rgba(41,211,242,.06)" />
        <path d={area('p25', 'p75')} fill="rgba(41,211,242,.15)" />
        <path d={line('p50')} fill="none" stroke="#95A1BA" strokeWidth="1.3" strokeDasharray="6 4" />
        <path d={line('current')} fill="none" stroke="#29D3F2" strokeWidth="2.2" />
        {/* colonna selezionata: riga di fede oro */}
        {sel && (
          <g pointerEvents="none">
            <line x1={X(sel.window)} x2={X(sel.window)} y1={T} y2={H - B} stroke="rgba(255,209,102,.85)" strokeWidth="1.2" strokeDasharray="5 4" />
            <circle cx={X(sel.window)} cy={Y(sel.current * 100)} r="7" fill="none" stroke="#FFD166" strokeWidth="1.4" />
          </g>
        )}
        {wins.map((w: any, i: number) => (
          <g key={i}>
            <circle cx={X(w.window)} cy={Y(w.current * 100)} r="4" fill="#29D3F2" stroke="#070B16" strokeWidth="1.2">
              <title>{`realized ${w.window}g: corrente ${(w.current * 100).toFixed(1)}% · min ${(w.min * 100).toFixed(1)} · p50 ${(w.p50 * 100).toFixed(1)} · max ${(w.max * 100).toFixed(1)} · ${w.n_obs} oss.${w.young ? ' · YOUNG' : ''}`}</title>
            </circle>
            <text x={X(w.window)} y={Y(w.current * 100) - 11} fontSize="12" fill="#29D3F2" textAnchor="middle" fontFamily="monospace">
              {(w.current * 100).toFixed(0)}%
            </text>
            <text x={X(w.window)} y={H - B + 17} fontSize="12" fill={w.young ? '#B97A00' : selW === w.window ? '#ECF1FA' : '#73829F'} textAnchor="middle" fontFamily="monospace">
              {w.window}g{w.young ? '*' : ''}
            </text>
          </g>
        ))}
        {ivPts.map((c: any, i: number) => {
          const k = (seen[c.window] = (seen[c.window] ?? -1) + 1);
          const x = X(c.window) + k * 14;
          return (
            <g key={i} transform={`translate(${x},${Y(c.atm_iv * 100)})`} opacity={selW == null || c.window === selW ? 1 : 0.4}>
              <path d="M0,-6 L6,0 L0,6 L-6,0 Z" fill="#FFD166" stroke="#070B16" strokeWidth="1.2">
                <title>{`${c.expiry} · ${c.days}g cal → finestra ${c.window}g borsa · IV ${(c.atm_iv * 100).toFixed(1)}% = ${c.pct_realized_leq_iv}° pct della realized`}</title>
              </path>
              <text x={8} y={4} fontSize="10" fill="#FFD166" fontFamily="monospace">{c.days}g</text>
            </g>
          );
        })}
        <text x={W - R} y={T - 8} fontSize="12" fill="#FFD166" textAnchor="end" fontFamily="monospace">◆ = ATM IV PER SCADENZA (POSATA SULLA FINESTRA ABBINATA)</text>
      </svg>
      {/* lettura interattiva: percentili completi della finestra selezionata */}
      <div className="num" style={{ display: 'flex', flexWrap: 'wrap', gap: '3px 16px', padding: '6px 10px 2px', fontSize: 11, alignItems: 'baseline', minHeight: 24 }}>
        {!sel ? (
          <span style={{ fontWeight: 600, color: '#73829F', fontSize: 10, letterSpacing: '.08em' }}>PREMI O PASSA SUL CONE → PERCENTILI COMPLETI DELLA FINESTRA + IV ABBINATE</span>
        ) : (
          <>
            <span style={{ color: '#FFD166', fontWeight: 700 }}>FINESTRA {sel.window}G</span>
            <span style={{ color: '#29D3F2', fontWeight: 700 }}>CORRENTE {(sel.current * 100).toFixed(1)}%</span>
            <span style={{ color: '#8D9FC4' }}>MIN {(sel.min * 100).toFixed(1)} · P25 {(sel.p25 * 100).toFixed(1)} · <b>MEDIANA {(sel.p50 * 100).toFixed(1)}</b> · P75 {(sel.p75 * 100).toFixed(1)} · MAX {(sel.max * 100).toFixed(1)}</span>
            <span style={{ fontWeight: 600, color: '#73829F' }}>{sel.n_obs} OSS. 1Y{sel.young ? ' · YOUNG (<60)' : ''}</span>
            {selIvs.map((c: any, i: number) => {
              const p = Number(c.pct_realized_leq_iv);
              const col = p >= 80 ? '#FF3D60' : p >= 60 ? '#FFA51E' : p <= 20 ? '#21E0A0' : '#8D9FC4';
              return <span key={i} style={{ color: col }}>◆ {c.days}g: IV {(c.atm_iv * 100).toFixed(1)}% = <b>{c.pct_realized_leq_iv}°</b> PCT</span>;
            })}
          </>
        )}
      </div>
      <div className="num" style={{ display: 'flex', flexWrap: 'wrap', gap: '3px 16px', padding: '2px 10px 2px', fontSize: 11, alignItems: 'baseline' }}>
        {ivPts.map((c: any, i: number) => {
          const p = Number(c.pct_realized_leq_iv);
          const col = p >= 80 ? '#FF3D60' : p >= 60 ? '#FFA51E' : p <= 20 ? '#21E0A0' : '#8D9FC4';
          return (
            <span key={i} style={{ color: col }}>
              {c.days}g · IV {(c.atm_iv * 100).toFixed(1)}% = <b>{c.pct_realized_leq_iv}°</b> pct realized {c.window}g
            </span>
          );
        })}
        {cone.implied?.error && <span style={{ color: '#B97A00' }}>IMPLIED N.D. — {String(cone.implied.error)}</span>}
        {missing.map((w: any, i: number) => <span key={'m' + i} style={{ fontWeight: 600, color: '#73829F' }}>{w.window}g: {String(w.error)}</span>)}
      </div>
    </>
  );
}

/* ============================================================
   GAMMA EXPOSURE // DEALER (richiesta PM 25/07 live) — il payload di
   oggi NON espone OI/gamma per strike: qui NIENTE numeri inventati.
   Pannello CABLATO sul contratto proposto alla chat backend nel ponte
   (campo `gex`: by_strike[]{strike,gex_1pct_usd,call_oi,put_oi} +
   flip_strike + net_gex_1pct_usd + basis + error): si accende DA SOLO
   al primo payload col campo — stesso pattern di fonti_mute F8.
   ============================================================ */
function GexProfile({ gex, spot }: { gex: any; spot?: number }) {
  if (gex === undefined) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 9, fontWeight: 600, color: '#73829F', lineHeight: 1.7 }}>
      n.d. — IL PAYLOAD NON ESPONE ANCORA GAMMA/OI PER STRIKE<br />
      <span style={{ color: '#B97A00' }}>RICHIESTA AL BACKEND NEL PONTE (25/07): CAMPO `gex` DALLA STESSA CHAIN POLYGON — IL PANNELLO SI ACCENDE DA SOLO AL PRIMO PAYLOAD COL CAMPO</span>
    </div>;
  }
  if (!gex || gex.error) {
    return <div className="num" style={{ padding: '12px 12px 14px', fontSize: 9, color: '#B97A00', lineHeight: 1.7 }}>
      DICHIARATO DAL BACKEND: {String(gex?.error || 'gex vuoto')}
    </div>;
  }
  const rows = (gex.by_strike || []).filter((r: any) => r.strike != null && r.gex_1pct_usd != null);
  if (rows.length < 2) {
    return <div className="num" style={{ padding: '12px', fontSize: 9, fontWeight: 600, color: '#73829F' }}>n.d. — by_strike vuoto o insufficiente</div>;
  }
  const mx = Math.max(...rows.map((r: any) => Math.abs(r.gex_1pct_usd)), 1e-9);
  const net = gex.net_gex_1pct_usd;
  return (
    <div style={{ padding: '6px 12px 8px', display: 'flex', flexDirection: 'column', gap: 4 }}>
      {net != null && (
        <div className="num" style={{ fontSize: 10, marginBottom: 2, fontWeight: 600, color: net >= 0 ? '#21E0A0' : '#FF3D60' }}>
          NET GEX {net >= 0 ? '+' : ''}{kfmt(Math.abs(net))} $/1% · {net >= 0 ? 'DEALER LONG Γ (movimenti compressi)' : 'DEALER SHORT Γ (movimenti amplificati)'}
        </div>
      )}
      {rows.map((r: any, i: number) => {
        const neg = r.gex_1pct_usd < 0;
        const w = 50 * Math.abs(r.gex_1pct_usd) / mx;
        const isFlip = gex.flip_strike != null && r.strike === gex.flip_strike;
        const isSpot = spot != null && spot > 0 && Math.abs(r.strike - spot) / spot < 0.005;
        return (
          <div key={i} className="num" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10 }}
               title={`strike ${r.strike} · GEX ${r.gex_1pct_usd >= 0 ? '+' : ''}${kfmt(Math.abs(r.gex_1pct_usd))} $/1%` + (r.call_oi != null ? ` · call OI ${kfmt(r.call_oi)} · put OI ${kfmt(r.put_oi ?? 0)}` : '')}>
            <span style={{ width: 56, color: isFlip ? '#FFA51E' : isSpot ? '#29D3F2' : '#73829F', fontWeight: isFlip || isSpot ? 700 : 400 }}>
              {px(Number(r.strike))}{isFlip ? ' ⚑' : isSpot ? ' ◈' : ''}
            </span>
            <span style={{ flex: 1, height: 6, background: 'rgba(26,36,64,.5)', position: 'relative' }}>
              <span style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: '#2A3760' }} />
              <i style={{ position: 'absolute', top: 0, bottom: 0, ...(neg ? { right: '50%', width: w + '%' } : { left: '50%', width: w + '%' }), background: neg ? 'rgba(255,61,96,.7)' : 'rgba(33,224,160,.65)' }} />
            </span>
          </div>
        );
      })}
      <div className="num" style={{ fontSize: 9, fontWeight: 600, color: '#73829F', letterSpacing: '.08em' }}>
        ⚑ = gamma flip{gex.flip_strike != null ? ' ' + px(Number(gex.flip_strike)) : ''} · ◈ = spot · {gex.basis || ''}
      </div>
    </div>
  );
}

export default function VolSurfacePage() {
  const [ticker, setTicker] = useState('');
  const [input, setInput] = useState('');
  const [view, setView] = useState<'surface' | 'desk' | 'laboratory'>('surface');
  const [data, setData] = useState<any>(null);
  const [cone, setCone] = useState<any>({ error: 'Contesto aggiuntivo non richiesto. Usa Carica contesto per RV, IV rank, GEX e vol cone.' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const requestRef = useRef<AbortController | null>(null);
  const lastExpiries = useRef<string[]>([]);

  useEffect(() => {
    requestRef.current?.abort(); setData(null); setError(null); setLoading(false); lastExpiries.current = [];
    setCone({ error: 'Contesto aggiuntivo non richiesto: premi Carica contesto per una lettura separata.' });
    return () => requestRef.current?.abort();
  }, [ticker]);

  const loadSurface = async (expiries: string[], context = false) => {
    requestRef.current?.abort(); const controller = new AbortController(); requestRef.current = controller;
    setLoading(true); setError(null); setData(null); lastExpiries.current = expiries;
    setView(context ? 'desk' : 'surface');
    try {
      const result = await volRequest<any>(`/options/vol_surface/${encodeURIComponent(ticker)}?expiries=${encodeURIComponent(expiries.join(','))}&include_context=${context}`, undefined, controller.signal);
      if (controller.signal.aborted) return;
      setData(result); if (result.error) setError(result.error);
      if (context) {
        setCone({ __loading: true });
        try {
          const resultCone = await volRequest<any>(`/options/vol_cone/${encodeURIComponent(ticker)}`, undefined, controller.signal);
          if (!controller.signal.aborted) setCone(resultCone);
        } catch (e) { if (!controller.signal.aborted) setCone({ error: e instanceof Error ? e.message : String(e) }); }
      } else {
        setCone({ error: 'Contesto aggiuntivo non richiesto; premi Carica contesto se serve.' });
      }
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  };

  useEffect(() => {
    // il mesh vive solo nella vista SURFACE: al rientro dalla DESK va ridisegnato
    if (view !== 'surface' || !data?.slices?.length || !plotRef.current) return;
    let active = true;
    loadPlotly().then(Plotly => {
      if (!active || !plotRef.current) return;
      const grid: number[] = data.moneyness_grid;
      // 0-1 DTE fuori dal plot: smile distorto dalla microstruttura
      const plottable = (data.slices || []).filter((s: any) => s.days >= 2);
      const use = plottable.length >= 2 ? plottable : data.slices;
      const zraw: (number | null)[][] = use.map((s: any) =>
        s.iv_grid.map((v: number | null) => (v == null ? null : v * 100)));
      const flat = zraw.flat().filter((v): v is number => v != null && isFinite(v));
      // v3: niente taglio della geometria — il p99 satura solo la scala COLORE
      const zhi = pctile(flat, 0.99);
      const zlo = Math.max(0, pctile(flat, 0.01) * 0.9);
      const z = zraw;
      const y = use.map((s: any) => s.days);

      const surface = {
        type: 'surface', x: grid, y, z,
        colorscale: [
          [0, '#0B2545'], [0.35, '#1B4965'], [0.62, '#2A9DB8'],
          [0.85, '#9C8030'], [1, '#D4AF37'],
        ],
        cmin: zlo, cmax: zhi,
        // A missing observation remains a hole in both the mesh and X-RAY.
        connectgaps: false,
        lighting: { ambient: 0.68, diffuse: 0.7, specular: 0.22, roughness: 0.7, fresnel: 0.08 },
        lightposition: { x: -60, y: -120, z: 90 },
        colorbar: {
          tickfont: { color: '#8a8a9e', size: 9, family: 'monospace' },
          title: { text: 'IV %', font: { color: '#8a8a9e', size: 10, family: 'monospace' } },
          thickness: 10, len: 0.75, outlinewidth: 0,
        },
        contours: { z: { show: true, usecolormap: true, project: { z: true }, width: 1 } },
        hovertemplate: 'K/S %{x:.3f} · %{y} giorni<br><b>IV %{z:.1f}%</b><extra></extra>',
        name: '',
      };
      // Cresta ATM in oro: term structure leggibile direttamente sulla superficie.
      // v3: il punto viene dalla SUPERFICIE a K/S=1 (stessa fonte del mesh) — la
      // media call/put di atm_iv la faceva galleggiare staccata; atm_iv resta
      // il fallback quando la griglia a 1.0 è n.d.
      const i1 = grid.indexOf(1.0);
      const atmLine = {
        type: 'scatter3d', mode: 'lines+markers',
        x: use.map(() => 1.0), y,
        z: use.map((s: any) => {
          const g = i1 >= 0 ? s.iv_grid?.[i1] : null;
          return (g != null ? g : s.atm_iv) * 100;
        }),
        line: { color: '#FFD166', width: 6 },
        marker: { size: 3.5, color: '#FFD166' },
        hovertemplate: 'ATM · %{y} giorni<br><b>IV %{z:.1f}%</b><extra></extra>',
        name: 'ATM',
        showlegend: false,
      };

      Plotly.newPlot(plotRef.current, [surface, atmLine], {
        paper_bgcolor: 'rgba(0,0,0,0)',
        scene: {
          xaxis: {
            title: { text: 'Moneyness K/S', font: { size: 10, color: '#8a8a9e', family: 'monospace' } },
            tickfont: { size: 9, color: '#8a8a9e', family: 'monospace' },
            gridcolor: '#1e2638', zerolinecolor: '#1e2638', showbackground: false,
            tickformat: '.2f',
          },
          yaxis: {
            title: { text: 'Giorni a scadenza', font: { size: 10, color: '#8a8a9e', family: 'monospace' } },
            tickfont: { size: 9, color: '#8a8a9e', family: 'monospace' },
            gridcolor: '#1e2638', zerolinecolor: '#1e2638', showbackground: false,
          },
          zaxis: {
            title: { text: 'IV %', font: { size: 10, color: '#8a8a9e', family: 'monospace' } },
            tickfont: { size: 9, color: '#8a8a9e', family: 'monospace' },
            gridcolor: '#1e2638', zerolinecolor: '#1e2638', showbackground: false,
            ticksuffix: '%',
          },
          bgcolor: 'rgba(0,0,0,0)',
          camera: { eye: { x: -1.75, y: -1.45, z: 0.55 } },
          aspectratio: { x: 1.25, y: 1.55, z: 0.75 },
        },
        hoverlabel: {
          bgcolor: '#0e1526', bordercolor: '#B08D2E',
          font: { family: 'monospace', size: 11, color: '#e8e8e8' },
        },
        margin: { l: 0, r: 0, t: 6, b: 0 },
        height: 480,
        showlegend: false,
      }, { displayModeBar: false, responsive: true });
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [data, view]);

  const go = () => { const t = input.trim().toUpperCase(); if (/^[A-Z0-9][A-Z0-9.\-^]{0,24}$/.test(t)) setTicker(t); else setError('Inserisci un ticker valido prima di caricare il catalogo.'); };

  // toni dichiarati: IV-RV oltre ±3pt = premio caro/a sconto (stessa soglia v2)
  const ivrv = data?.iv_rv_spread_front;
  const ivrvTone = ivrv == null ? undefined : ivrv > 0.03 ? 'dn' : ivrv < -0.03 ? 'up' : undefined;
  const slope = data?.term_slope_front_to_60d;
  const nOpt = (data?.slices || []).reduce((a: number, s: any) => a + (s.n_calls || 0) + (s.n_puts || 0), 0);
  const nIll = (data?.slices || []).reduce((a: number, s: any) => a + (s.n_illiquidi_esclusi || 0), 0);
  // celle griglia senza quota liquida: nel mesh sono interpolate SOLO in resa
  // (ordine PM 25/07) — il conteggio resta DICHIARATO in legenda
  const nHoles = (data?.slices || []).filter((s: any) => s.days >= 2)
    .reduce((a: number, s: any) => a + (s.iv_grid || []).filter((v: any) => v == null).length, 0);

  return (
    <div className="obsx bootx animate-fadeIn">

      {/* ══ HERO: il quadro vol in vetrina + comandi ══ */}
      <div className="p3 hero" style={{ '--bd': '0s' } as any}>
        <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
        <div className="p3h am">VOLATILITY // OPTIONS DECK
          <span className="n">· SUPERFICIE IV MULTI-EXPIRY · POLYGON OPRA</span>
          <span className="side num">
            {data?._timestamp ? 'FOTO ' + data._timestamp.slice(0, 16).replace('T', ' ') + ' · ' : ''}COMPOSITE OTM · SMOOTHING: MEDIANA 3PT (FIT SVI = V2)
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'stretch', flexWrap: 'wrap', minHeight: 74 }}>
          <div style={{ padding: '9px 16px 11px' }}>
            <div style={{ fontSize: 10, letterSpacing: '.1em', fontWeight: 600, color: '#A9BAD1' }}>Sottostante / selezione dichiarata</div>
            <div className="num" style={{ fontSize: 22, fontWeight: 700, marginTop: 3, lineHeight: 1.1, color: '#ECF1FA' }}>
              {data?.ticker || ticker || 'Options deck'}
              {data?.spot_est != null && <span style={{ fontSize: 12, fontWeight: 600, marginLeft: 8, color: '#29D3F2' }}>spot ~{px(Number(data.spot_est))}</span>}
            </div>
            <div style={{ fontSize: 9, color: data?.spot_source?.startsWith('PROXY') ? '#B97A00' : '#73829F', marginTop: 4, letterSpacing: '.1em', textTransform: 'uppercase' }}>
              {data ? (data.spot_source?.startsWith('PROXY') ? 'SPOT DA PROXY (YFINANCE KO, DICHIARATO)' : 'spot ' + (data.spot_source || 'n.d.')) + ' · ' + (data.n_expiries ?? '–') + ' SCADENZE' : loading ? 'CARICO LA CHAIN…' : 'PRONTO'}
            </div>
          </div>
          <VStat label="EXPECTED MOVE 1σ"
                 value={data?.expected_move_pct != null ? `±${data.expected_move_pct}%` : 'n.d.'}
                 tone="text-cyan"
                 sub={data?.expected_move_days != null ? `ENTRO ${data.expected_move_days}G · ATM IV × √T` : 'DAL PAYLOAD'} />
          <VStat label="IV − RV FRONT"
                 value={ivrv != null ? `${(ivrv * 100).toFixed(1)}pt` : 'n.d.'}
                 tone={ivrvTone}
                 sub={ivrv != null ? (ivrv > 0.03 ? 'OPZIONI CARE (>+3PT)' : ivrv < -0.03 ? 'OPZIONI A SCONTO (<−3PT)' : 'PREMIO NELLA NORMA') : '—'} />
          <VStat label="REALIZED VOL 30G"
                 value={data?.realized_vol_30d != null ? `${(data.realized_vol_30d * 100).toFixed(1)}%` : 'n.d.'}
                 sub={data?.rv_percentile_1y != null ? `${data.rv_percentile_1y.toFixed(0)}° PCT 1Y` : '—'} />
          <VStat label="TERM SLOPE F→60G"
                 value={slope != null ? `${(slope * 100).toFixed(1)}pt` : 'n.d.'}
                 tone={slope != null ? (slope < 0 ? 'dn' : 'up') : undefined}
                 sub={slope != null ? (slope < 0 ? 'BACKWARDATION · PREMIO SUL FRONT' : 'CONTANGO') : '—'}
                 title="ATM IV a ~60 giorni meno ATM IV front: negativo = curva invertita (evento/stress sul front)" />
          <VStat label="EARNINGS" value={data?.next_earnings || 'n.d.'}
                 tone={data?.next_earnings ? 'text-amber' : undefined}
                 sub={data?.next_earnings ? 'PREMIO EVENTO NELLA CURVA' : 'NESSUNA DATA NOTA'} />
          <div style={{ marginLeft: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', justifyContent: 'center', gap: 6, padding: '8px 14px' }}>
            <span className="tfg">
              <button className={'tb' + (view === 'surface' ? ' on' : '')} onClick={() => setView('surface')}>SURFACE</button>
              <button className={'tb' + (view === 'desk' ? ' on' : '')} onClick={() => setView('desk')}>DESK</button>
              <button className={'tb' + (view === 'laboratory' ? ' on' : '')} onClick={() => setView('laboratory')}>CHAIN / STRATEGIE</button>
            </span>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input value={input} onChange={e => setInput(e.target.value.toUpperCase())}
                     onKeyDown={e => e.key === 'Enter' && go()} spellCheck={false}
                     placeholder="TICKER US" aria-label="Ticker underlying"
                     className="num"
                     style={{ background: '#070B16', border: '1px solid #1A2440', color: '#ECF1FA', fontSize: 10, padding: '4px 8px', width: 110, letterSpacing: '.15em', outline: 'none' }} />
              <button className="tb" onClick={go} disabled={loading} style={{ color: '#29D3F2' }}>
                {loading ? 'SCANSIONE…' : 'CATALOGO'}
              </button>
            </div>
          </div>
        </div>
      </div>

      <VolWorkbench ticker={ticker} mode={view} coverage={data?.coverage} surfaceBusy={loading}
        onSurface={(result, expiries) => {
          requestRef.current?.abort(); setLoading(false); setData(result); setError(result.error || null);
          lastExpiries.current = expiries; setView('surface');
          setCone({ error: 'Contesto aggiuntivo non richiesto; premi Carica contesto se serve.' });
        }} onLaboratory={() => setView('laboratory')} />
      {data?.slices?.length > 0 && <div className="vol-workbench"><div className="vd-actions" style={{ padding: '10px 4px' }}>
        <button className="vd-secondary" disabled={loading} onClick={() => loadSurface(lastExpiries.current, true)}>Carica contesto RV, IV rank, GEX e cone</button>
        <small>Richieste provider aggiuntive; il contesto non viene caricato automaticamente con la superficie.</small>
      </div></div>}

      {loading && (
        <div className="p3" style={{ padding: '26px 12px', textAlign: 'center', '--bd': '.05s' } as any}>
          <div className="num" style={{ fontSize: 11, color: '#A9BAD1' }}>CARICAMENTO CHAIN SELEZIONATE {ticker} — POLYGON OPRA · LE DATE NON RICHIESTE NON VENGONO CARICATE</div>
          <div className="num" style={{ fontSize: 9, fontWeight: 600, color: '#73829F', marginTop: 4 }}>composite OTM · strike illiquidi esclusi e dichiarati · poi cache</div>
        </div>
      )}
      {error && (
        <div className="p3" style={{ padding: '14px 12px', borderLeft: '2px solid #FF3D60' }}>
          <span className="num" style={{ fontSize: 10, fontWeight: 600, color: '#FF3D60' }}>ERRORE DICHIARATO: {error}</span>
        </div>
      )}

      {/* ══ VISTA SURFACE: mesh 3D + sezione interattiva + strumenti di proiezione ══ */}
      {data?.slices?.length > 0 && !loading && view === 'surface' && (
        <div className="vsxgrid">
          {/* ── colonna principale: mesh 3D + sezione smile interattiva ── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>
            <div className="p3 cy scanx" style={{ position: 'relative', '--bd': '.08s' } as any}>
              <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
              <div className="p3h">SUPERFICIE IV // MESH 3D
                <span className="n">· CRESTA ORO = ATM TERM STRUCTURE</span>
                <span className="side num">{nOpt > 0 ? nOpt + ' QUOTE USATE · ' + nIll + ' ILLIQUIDE ESCLUSE · ' : ''}0-1 DTE FUORI DAL PLOT</span>
              </div>
              <div ref={plotRef} />
              {data.slices.length < 2 && <div className="vsxleg">Una sola curva disponibile: la sezione smile è consultabile; per una superficie tridimensionale servono almeno due scadenze.</div>}
              <div className="vsxleg">
                trascina = ruota · hover = K/S, giorni, IV esatti ·
                <span style={{ color: '#E9BA64' }}> {nHoles} celle prive di dati lasciate vuote. Nessun riempimento grafico dei buchi; tra strike osservati il builder interpola e applica una mediana a tre punti, come dichiarato</span> ·
                colore saturato oltre il 99° pct (geometria intatta) · [src: {data._source || 'polygon chains'}]
              </div>
            </div>

            <div className="p3" style={{ '--bd': '.16s' } as any}>
              <div className="p3h">SEZIONE SMILE // X-RAY
                <span className="n">· INTERATTIVA: PREMI E LEGGI L'IV</span>
                <span className="side num">VICINE = CYAN · LONTANE = VIOLA · CROSSHAIR ORO</span>
              </div>
              <div style={{ padding: '6px 8px 0' }}>
                <SmileXray grid={data.moneyness_grid} slices={data.slices} spot={Number(data.spot_est)} />
              </div>
              <div className="vsxleg">smile per scadenza a parità di K/S · qui i buchi del builder RESTANO buchi (sezione fedele, n.d. nella lettura) · ATM = K/S 1.00</div>
            </div>
          </div>

          {/* ── colonna strumenti SURFACE: proiettore di strike (firma) + altimetro IV ── */}
          <div className="vsxrail">
            <div className="p3 cy scanx" style={{ position: 'relative', '--bd': '.12s' } as any}>
              <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
              <div className="p3h">PROIETTORE DI STRIKE
                <span className="n">· EXPECTED MOVE PER SCADENZA</span>
              </div>
              <div style={{ padding: '4px 4px 0' }}>
                <StrikeProjector spot={Number(data.spot_est)} term={data.term_structure} earnings={data.next_earnings} />
              </div>
              <div className="vsxleg">
                banda = spot ± ATM IV×√T (1σ pieno · 2σ tratteggiato) · asse orizzontale in √t ·
                <span style={{ color: '#B97A00' }}> E = earnings</span> · hover sui nodi = strike ·
                è il PREZZO DELLE OPZIONI, non una previsione
              </div>
            </div>

            <div className="p3" style={{ '--bd': '.2s' } as any}>
              <div className="p3h" title={data.iv_history_context?.basis || 'percentile dell\'ATM IV front vs storico raccolto (voce (39))'}>
                ALTIMETRO IV RANK
                <span className="n">· VS STORICO RACCOLTO</span>
              </div>
              <IvAltimeter ctx={data.iv_history_context} />
              <div className="vsxleg">percentile = % giorni storici con ATM IV front ≤ corrente · fonte iv_history (voce (39)) · rank giovane MAI spacciato per maturo</div>
            </div>
          </div>
        </div>
      )}

      {/* ══ VISTA DESK: la lettura vol — term 2D vs RV, top-down fedele, forward vol, OI ══ */}
      {data?.slices?.length > 0 && !loading && view === 'desk' && (
        <div className="vsxgrid">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>
            {/* ordine PM 25/07 live: la LETTURA apre il desk, i numeri sotto */}
            {data.interpretation && (
              <div className="p3" style={{ borderLeft: '2px solid #B08D2E', '--bd': '.05s' } as any}>
                <div className="p3h am">LETTURA // DESK NOTE<span className="n">· GENERATA DAL BUILDER SUI NUMERI QUI SOTTO</span></div>
                <p className="num" style={{ padding: '8px 12px 10px', fontSize: 11, lineHeight: 1.75, color: 'rgba(236,241,250,.92)', whiteSpace: 'pre-wrap', margin: 0 }}>
                  {data.interpretation}
                </p>
              </div>
            )}
            <div className="p3 cy" style={{ position: 'relative', '--bd': '.08s' } as any}>
              <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
              <div className="p3h">TERM STRUCTURE // CURVA ATM
                <span className="n">· ORO = IMPLICITA · TRATTEGGIO = REALIZZATA 30G</span>
                <span className="side num">IMPLICITA SOPRA LA REALIZZATA = CARRY PER CHI VENDE PREMIO</span>
              </div>
              <div style={{ padding: '4px 8px 0' }}>
                <Term2D term={data.term_structure} earnings={data.next_earnings} rv30={data.realized_vol_30d} />
              </div>
              <div className="vsxleg">asse orizzontale in √t · hover sui nodi = expiry e IV esatta · <span style={{ color: '#B97A00' }}>E = earnings</span> · RV 30G dal payload [src: builder]</div>
            </div>

            <div className="p3 cy" style={{ position: 'relative', '--bd': '.12s' } as any}>
              <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
              <div className="p3h">VOL CONE // REALIZED VS IMPLIED
                <span className="n">· LA VOL CHE C'È STATA CONTRO QUELLA PREZZATA</span>
                <span className="side num">FINESTRE 5/10/21/63G DI BORSA · PERCENTILI 1Y</span>
              </div>
              <div style={{ padding: '4px 8px 0' }}>
                <VolCone cone={cone} />
              </div>
              <div className="vsxleg">
                banda chiara = min–max · banda piena = p25–p75 · tratteggio = mediana · linea cyan = realized CORRENTE ·
                ◆ oro = ATM IV per scadenza sulla finestra abbinata (calendario→borsa ×252/365) ·
                * = finestra YOUNG (&lt;60 oss.) · IV ≥80° pct della realized = opzioni care vs storia · [src: vol_cone (43)]
              </div>
            </div>

            <div className="p3" style={{ '--bd': '.16s' } as any}>
              <div className="p3h">SURFACE TOP-DOWN // HEAT
                <span className="n">· LA VERITÀ CELLA PER CELLA</span>
                <span className="side num">CELLE SCURE = QUOTA ASSENTE (BUCO DICHIARATO)</span>
              </div>
              <div style={{ padding: '4px 8px 0' }}>
                <HeatTopDown grid={data.moneyness_grid} slices={data.slices} />
              </div>
              <div className="vsxleg">stessa palette del mesh (blu = IV bassa → oro = alta) · hover su ogni cella = expiry, K/S, IV · qui NIENTE interpolazione</div>
            </div>

            <div className="p3" style={{ '--bd': '.24s' } as any}>
              <div className="p3h">TERM STRUCTURE // ATM + SKEW 25Δ
                <span className="n">· L'ORDINE È LA CURVA</span>
                <span className="side num">RR25 = CALL25 − PUT25 · BF25 = CURVATURA VS ATM</span>
              </div>
              <div className="tscroll">
                <table className="num" style={{ width: '100%', fontSize: 10, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ fontWeight: 600, color: '#73829F', fontSize: 9, letterSpacing: '.14em', textTransform: 'uppercase', borderBottom: '1px solid #1A2440' }}>
                      <th style={{ textAlign: 'left', padding: '5px 10px' }}>Expiry</th>
                      <th style={{ textAlign: 'right' }}>GG</th>
                      <th style={{ textAlign: 'right' }}>ATM IV</th>
                      <th style={{ textAlign: 'left', paddingLeft: 8 }} aria-hidden="true"></th>
                      <th style={{ textAlign: 'right' }}>RR 25Δ</th>
                      <th style={{ textAlign: 'center' }}>SKEW</th>
                      <th style={{ textAlign: 'right' }}>BF 25Δ</th>
                      <th style={{ textAlign: 'right', paddingRight: 10 }}>P/C OI</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(() => {
                      const maxIv = Math.max(...data.term_structure.map((s: any) => s.atm_iv || 0), 0.0001);
                      const maxRr = Math.max(...data.term_structure.map((s: any) => Math.abs(s.rr25 ?? 0)), 0.0001);
                      return data.term_structure.map((s: any) => (
                        <tr key={s.expiry} style={{ borderBottom: '1px solid rgba(26,36,64,.4)' }}>
                          <td style={{ padding: '4px 10px', color: '#8D9FC4' }}>{s.expiry}</td>
                          <td style={{ textAlign: 'right', fontWeight: 600, color: '#73829F' }}>{s.days}</td>
                          <td style={{ textAlign: 'right', color: '#29D3F2' }}>{(s.atm_iv * 100).toFixed(1)}%</td>
                          <td style={{ paddingLeft: 8, width: 90 }}>
                            <span className="vsxbar" style={{ width: Math.max(2, 84 * (s.atm_iv / maxIv)) }} />
                          </td>
                          <td style={{ textAlign: 'right', fontWeight: 600, color: s.rr25 == null ? '#73829F' : s.rr25 < 0 ? '#FF3D60' : '#21E0A0' }}>
                            {s.rr25 == null ? 'n.d.' : (s.rr25 > 0 ? '+' : '') + (s.rr25 * 100).toFixed(2) + 'pt'}
                          </td>
                          <td style={{ textAlign: 'center', width: 70 }}>
                            {s.rr25 != null && (
                              <span className="vsxbip" title={s.rr25 < 0 ? 'put skew' : 'call skew'}>
                                {s.rr25 < 0
                                  ? <i style={{ right: '50%', width: Math.min(31, 31 * Math.abs(s.rr25) / maxRr), background: 'rgba(255,61,96,.7)' }} />
                                  : <i style={{ left: '50%', width: Math.min(31, 31 * Math.abs(s.rr25) / maxRr), background: 'rgba(33,224,160,.7)' }} />}
                              </span>
                            )}
                          </td>
                          <td style={{ textAlign: 'right', color: '#8D9FC4' }}>{s.bf25 == null ? 'n.d.' : (s.bf25 * 100).toFixed(2) + 'pt'}</td>
                          <td style={{ textAlign: 'right', paddingRight: 10, color: s.pc_oi_ratio != null && s.pc_oi_ratio > 1.5 ? '#FFA51E' : '#8D9FC4' }}>
                            {s.pc_oi_ratio == null ? 'n.d.' : s.pc_oi_ratio.toFixed(2)}
                          </td>
                        </tr>
                      ));
                    })()}
                  </tbody>
                </table>
              </div>
              {data.skew_note && (
                <div className="num" style={{ padding: '6px 10px', borderTop: '1px solid rgba(26,36,64,.5)', fontSize: 9, color: '#8D9FC4' }}>
                  <span style={{ fontWeight: 600, color: '#73829F' }}>NOTA SKEW [src: builder] · </span>{data.skew_note}
                </div>
              )}
              <div className="vsxleg">composite OTM (put sotto spot, call sopra) · griglia K/S 0.80–1.20 · P/C OI &gt; 1.5 in ambra (copertura pesante)</div>
            </div>

          </div>

          {/* ── colonna strumenti DESK: forward vol + posizionamento OI ── */}
          <div className="vsxrail">
            <div className="p3 cy" style={{ position: 'relative', '--bd': '.12s' } as any}>
              <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
              <div className="p3h">FORWARD VOL
                <span className="n">· FRA SCADENZE CONSECUTIVE</span>
              </div>
              <FwdVolLadder term={data.term_structure} />
              <div className="vsxleg">σ_fwd = √((σ₂²T₂ − σ₁²T₁)/(T₂−T₁)) · formula dichiarata, input = ATM IV del payload · INVERTITA = varianza forward negativa (backwardation: dichiarata, mai un numero inventato)</div>
            </div>

            <div className="p3" style={{ '--bd': '.2s' } as any}>
              <div className="p3h">OPEN INTEREST
                <span className="n">· POSIZIONAMENTO PER SCADENZA</span>
              </div>
              <OiProfile term={data.term_structure} />
              <div className="vsxleg">rosso = put OI · verde = call OI · P/C &gt; 1.5 in ambra · [src: Polygon chains]</div>
            </div>

            <div className="p3" style={{ '--bd': '.28s' } as any}>
              <div className="p3h">GAMMA EXPOSURE // DEALER
                <span className="n">· PER STRIKE</span>
              </div>
              <GexProfile gex={data.gex} spot={Number(data.spot_est)} />
              <div className="vsxleg">GEX &gt; 0 = dealer long gamma (comprimono i movimenti) · &lt; 0 = short gamma (li amplificano) · ipotesi standard dealer long call / short put: DICHIARATA nel basis del payload quando arriva</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
