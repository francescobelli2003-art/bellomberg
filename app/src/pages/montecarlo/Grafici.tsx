import { useState } from 'react';
import { Pin } from 'lucide-react';
import { useT } from '@/i18n/provider';
import type { MonteCarloResult } from '@/lib/api';
import { useBox } from '@/lib/useBox';
import { deterministico, fmtEUR, fmtInt, fmtNum, fmtPctS } from './formato';
import { Aiuto, PKEYS } from './Viste';

/* I due grafici della plancia, su dati VERI del payload: bande del cono (fan_bands),
   tracce campione (sample_paths) e istogramma a scadenza (terminal_hist). Niente valori
   interpolati: dove il campo manca il buco si dichiara. Gli SVG sono misurati in pixel
   reali (useBox), mai stirati con preserveAspectRatio. Colori e caratteri dal CSS. */

export type Finestra = { lo: number; hi: number };
const MAX_TRACCE = 40;   // il campione resta leggero: le bande sono il dato, le tracce l'esempio

/** Scenario deterministico: le «bande» sono UNA traiettoria (p5..p95 coincidono) e non sono
 *  percentili. Lo dice il motore (fan_bands.deterministic) o il blocco dello scenario: basta
 *  uno dei due perché il grafico smetta di etichettarle come percentili. */
const unaTraiettoria = (r: MonteCarloResult) => !!r.fan_bands?.deterministic || deterministico(r);

function tacche(lo: number, hi: number, target: number): number[] {
  if (!(hi > lo)) return [];
  const raw = (hi - lo) / target, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) ?? mag * 10;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
  return out;
}
const etichettaAsse = (v: number, hi: number) => hi >= 10000
  ? fmtNum(v / 1000, Math.abs(v / 1000 - Math.round(v / 1000)) > 1e-9 ? 1 : 0) + 'k'
  : fmtInt(v);

/** Etichette a destra (percentili a scadenza): se due si toccano, si scostano senza
 *  cambiare il valore scritto né la tacca che le ancora. */
function scosta(ys: number[], gap: number) {
  const out = [...ys];
  for (let i = 1; i < out.length; i++) if (out[i] - out[i - 1] < gap) out[i] = out[i - 1] + gap;
  return out;
}

/* ─────────────── CONO DELLE TRAIETTORIE ─────────────── */
export function Cono({ r, view, tracce, soglie, onTracce, onSoglie }: {
  r: MonteCarloResult; view: Finestra | null; tracce: boolean; soglie: boolean;
  onTracce: () => void; onSoglie: () => void;
}) {
  const tr = useT();
  const [ref, box] = useBox<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [pin, setPin] = useState<number | null>(null);
  const fb = r.fan_bands;
  const disegnate = Math.min(MAX_TRACCE, r.sample_paths?.length ?? 0);
  const det = unaTraiettoria(r);
  return <section className="bbn-card mc-cone" data-deterministic={det || undefined}>
    <div className="bbn-card-head"><h2>{tr('montecarlo.coneTitle')}</h2><Aiuto testo={tr('montecarlo.coneHelp')} /><span className="bbn-grow" />
      <button type="button" className="mc-toggle" aria-pressed={tracce} onClick={onTracce}>{tr('montecarlo.togglePaths')}</button>
      {/* scenario deterministico: VaR ed ES sono null per costruzione e le soglie non
          disegnerebbero niente; il tasto resta visibile ma spento, e dice perché. aria-disabled
          e non disabled: resta raggiungibile da tastiera, così il motivo si legge anche senza mouse */}
      <button type="button" className="mc-toggle" aria-pressed={det ? false : soglie} onClick={det ? undefined : onSoglie} aria-disabled={det || undefined}
              title={det ? tr('montecarlo.tailsDetOff') : undefined} aria-describedby={det ? 'mc-tails-off' : undefined}>{tr('montecarlo.toggleTails')}</button>
      {det && <span id="mc-tails-off" className="sr-only">{tr('montecarlo.tailsDetOff')}</span>}
    </div>
    <div className="mc-legend">
      {det
        // le bande coincidono: nessuna legenda di percentili, e la frase del motore lo dichiara sul grafico
        ? <><span><i className="mc-sw is-line" />{tr('montecarlo.detPath')}</span><span><i className="mc-sw is-start" />{tr('montecarlo.legendStart')}</span>
            <span className="mc-det-bands" role="note" data-det-bands>{fb?.label || tr('montecarlo.detBands')}</span></>
        : <><span><i className="mc-sw is-b1" />p5–p95</span><span><i className="mc-sw is-b2" />p10–p90</span><span><i className="mc-sw is-b3" />p25–p75</span>
            <span><i className="mc-sw is-line" />{tr('montecarlo.legendMedian')}</span><span><i className="mc-sw is-start" />{tr('montecarlo.legendStart')}</span></>}
      <span className="bbn-grow" />
      <span>{fb ? tr('montecarlo.coneMeta', { points: fb.days.length, paths: fmtInt(disegnate), sims: fmtInt(r.n_sims) }) : null}
        {fb && tracce && disegnate > 0 && !r.sample_paths_days ? tr('montecarlo.uniformDays') : ''}</span>
    </div>
    {!fb || !fb.days?.length || !view
      ? <div className="mc-missing" role="note">{tr('montecarlo.coneMissingA')} <b>{tr('montecarlo.fanBandsName')}</b>{tr('montecarlo.coneMissingB', { field: 'fan_bands' })}</div>
      : <div className="mc-chart" ref={ref}>
          <ConoSvg r={r} view={view} w={box.w} h={box.h} tracce={tracce ? disegnate : 0} soglie={soglie && !det} det={det}
            idx={hover ?? pin} pinned={pin != null} onHover={setHover} onPick={i => setPin(p => (p != null ? null : i))} />
        </div>}
  </section>;
}

function ConoSvg({ r, view, w, h, tracce, soglie, det, idx, pinned, onHover, onPick }: {
  r: MonteCarloResult; view: Finestra; w: number; h: number; tracce: number; soglie: boolean; det: boolean;
  idx: number | null; pinned: boolean; onHover: (i: number | null) => void; onPick: (i: number | null) => void;
}) {
  const tr = useT();
  const fb = r.fan_bands!;
  if (w < 80 || h < 80) return <svg />;
  const days = fb.days, n = days.length;
  const L = 58, R = w - 96, T = 14, B = h - 40;
  const d0 = days[0], d1 = days[n - 1];
  const y = (v: number) => B - ((v - view.lo) / (view.hi - view.lo)) * (B - T);
  const x = (d: number) => L + ((d - d0) / Math.max(1, d1 - d0)) * (R - L);
  const line = (a: number[]) => days.map((d, i) => `${i ? 'L' : 'M'}${x(d).toFixed(1)} ${y(a[i]).toFixed(1)}`).join(' ');
  const band = (lo: number[], hi: number[]) => line(hi) +
    days.map((_, i) => { const j = n - 1 - i; return `L${x(days[j]).toFixed(1)} ${y(lo[j]).toFixed(1)}`; }).join(' ') + 'Z';
  const nav = r.base_nav_eur;
  const yT = tacche(view.lo, view.hi, Math.max(3, Math.round((B - T) / 70)));
  const xT = Array.from({ length: 6 }, (_, i) => Math.round(d0 + ((d1 - d0) * i) / 5));
  const paths = (r.sample_paths || []).slice(0, tracce), pdays = r.sample_paths_days;
  const code: [number | null | undefined, string][] = [[r.var_95_pct, 'VaR 95%'], [r.es_95_pct, 'ES 95%'], [r.es_99_pct, 'ES 99%']];
  // scenario deterministico: una traiettoria sola, quindi un'etichetta sola (la p50, uguale
  // alle altre) e col nome della traiettoria, mai «P95…P5» impilate sullo stesso valore
  const chiavi = det ? ['p50'] as const : PKEYS;
  const nome = (k: string) => det ? tr('montecarlo.detPath') : k.toUpperCase();
  const fine = (det ? ['p50'] as const : ['p95', 'p75', 'p50', 'p25', 'p5'] as const).map(k => ({ k, v: fb[k][n - 1] }));
  const fineY = scosta(fine.map(f => y(f.v)), 28);
  // soglie di coda: la linea sta sul valore vero, la targhetta si scosta se tocca la vicina
  const soglieIn = code.filter(([v]) => v != null && isFinite(v)).map(([v, lab]) => ({ lab, e: nav * (1 + v! / 100) }))
    .filter(t => t.e >= view.lo && t.e <= view.hi).sort((a, b) => b.e - a.e);
  const soglieY = scosta(soglieIn.map(t => y(t.e)), 22);
  const soglieVisibili = soglieIn.map((t, i) => ({ ...t, py: soglieY[i] }));

  // l'indice lo calcola l'EVENTO (rapporto pixel reali / misura: regge lo zoom della pagina)
  const daEvento = (e: React.MouseEvent<SVGSVGElement>): number | null => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) * w) / Math.max(1, rect.width);
    if (px < L || px > R) return null;
    const dd = d0 + ((px - L) / Math.max(1, R - L)) * (d1 - d0);
    let best = 0, bd = Infinity;
    days.forEach((v, i) => { const t = Math.abs(v - dd); if (t < bd) { bd = t; best = i; } });
    return best;
  };
  const avanti = idx != null && idx / Math.max(1, n - 1) > 0.55;
  const partenza = tr('montecarlo.startLabel', { v: fmtEUR(nav) });

  return <>
    <svg width={w} height={h} role="img" aria-label={tr('montecarlo.coneTitle')}
         onMouseMove={e => onHover(daEvento(e))} onMouseLeave={() => onHover(null)} onClick={e => onPick(daEvento(e))}>
      <defs><clipPath id="mc-plot"><rect x={L} y={T} width={Math.max(0, R - L)} height={Math.max(0, B - T)} /></clipPath></defs>
      {yT.map(v => <g key={v}>
        <line className="mc-grid" x1={L} x2={R} y1={y(v)} y2={y(v)} />
        <text className="mc-ax" x={L - 10} y={y(v) + 4} textAnchor="end">{etichettaAsse(v, view.hi)}</text>
      </g>)}
      {xT.map(d => <text key={d} className="mc-ax" x={x(d)} y={B + 18} textAnchor="middle">{d}</text>)}
      <text className="mc-ax" x={(L + R) / 2} y={B + 34} textAnchor="middle">{tr('montecarlo.axisDays')}</text>
      <g clipPath="url(#mc-plot)">
        {!det && <>
          <path className="mc-band is-b1" d={band(fb.p5, fb.p95)} />
          <path className="mc-band is-b2" d={band(fb.p10, fb.p90)} />
          <path className="mc-band is-b3" d={band(fb.p25, fb.p75)} />
        </>}
        {paths.map((p, i) => {
          const dd = (j: number) => (pdays && pdays[j] != null ? pdays[j] : d0 + ((d1 - d0) * j) / Math.max(1, p.length - 1));
          return <path key={i} className="mc-trace" d={p.map((v, j) => `${j ? 'L' : 'M'}${x(dd(j)).toFixed(1)} ${y(v * nav).toFixed(1)}`).join(' ')} />;
        })}
        <line className="mc-start" x1={L} x2={R} y1={y(nav)} y2={y(nav)} />
        <path className="mc-median" d={line(fb.p50)} />
        {soglie && soglieVisibili.map(({ lab, e, py }) => {
          const testo = `${lab} · ${fmtEUR(e)}`;
          return <g key={lab} data-tail={lab}>
            <line className="mc-tail" x1={L} x2={R} y1={y(e)} y2={y(e)} />
            <rect className="mc-plate" x={L + 8} y={py - 10} width={testo.length * 6.6 + 16} height={20} rx={10} />
            <rect className="mc-plate is-bad" x={L + 8} y={py - 10} width={testo.length * 6.6 + 16} height={20} rx={10} />
            <text className="mc-plate-t is-bad" x={L + 16} y={py + 4}>{testo}</text>
          </g>;
        })}
        {idx != null && <>
          <line className="mc-cross" x1={x(days[idx])} x2={x(days[idx])} y1={T} y2={B} />
          {chiavi.map(k => <circle key={k} className={k === 'p50' ? 'mc-dot is-med' : 'mc-dot'} cx={x(days[idx])} cy={y(fb[k][idx])} r={k === 'p50' ? 4.5 : 3} />)}
        </>}
      </g>
      <rect className="mc-plate is-raised" x={L + 8} y={y(nav) - 26} width={partenza.length * 6.6 + 16} height={20} rx={10} />
      <text className="mc-plate-t" x={L + 16} y={y(nav) - 12}>{partenza}</text>
      {fine.map(({ k, v }, i) => <g key={k} className={k === 'p50' ? 'mc-end is-med' : 'mc-end'}>
        <line x1={R} x2={R + 6} y1={y(v)} y2={y(v)} />
        <text className="mc-end-k" x={R + 10} y={fineY[i] - 3}>{nome(k)}</text>
        <text className="mc-end-v" x={R + 10} y={fineY[i] + 11}>{fmtEUR(v)}</text>
      </g>)}
    </svg>
    {idx != null && <div className={'mc-readout' + (avanti ? ' is-left' : '')} style={avanti ? { left: L + 12 } : { right: w - R + 12 }}>
      <div className="mc-rh">{tr('montecarlo.readDay', { d: days[idx], t: d1 })}
        {pinned && <span className="bbn-pill is-piatto"><Pin aria-hidden="true" />{tr('montecarlo.pinned')}</span>}</div>
      {chiavi.map(k => {
        const v = fb[k][idx], d = nav ? (v / nav - 1) * 100 : null;
        return <div key={k} className={'mc-rr' + (k === 'p50' ? ' is-med' : '')}>
          <span>{nome(k)}</span><b className="num">{fmtEUR(v)}</b>
          <b className={'num ' + (k === 'p50' || d == null ? '' : d < 0 ? 'mc-down' : 'mc-up')}>{fmtPctS(d)}</b>
        </div>;
      })}
      <div className="mc-rf">{pinned ? tr('montecarlo.clickUnpin') : tr('montecarlo.clickPin')}</div>
    </div>}
  </>;
}

/* ─────────────── DISTRIBUZIONE A SCADENZA ─────────────── */
export function Distribuzione({ r, view, fuori }: { r: MonteCarloResult; view: Finestra | null; fuori: { out: number; tot: number } | null }) {
  const tr = useT();
  const [ref, box] = useBox<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const th = r.terminal_hist;
  const tot = th ? th.counts.reduce((a, b) => a + b, 0) : 0;
  const pctFuori = fuori && fuori.tot > 0 ? (fuori.out / fuori.tot) * 100 : null;
  // scenario deterministico: il motore manda comunque l'istogramma, ma è una sola colonna
  // (tutte le traiettorie sono la stessa) e disegnarlo farebbe credere a una distribuzione.
  // Stato vuoto DICHIARATO al posto del grafico.
  const det = unaTraiettoria(r);
  return <section className="bbn-card mc-dist" data-deterministic={det || undefined}>
    <div className="bbn-card-head"><h2>{tr('montecarlo.distTitle')}</h2><Aiuto testo={tr('montecarlo.distHelp')} /><span className="bbn-grow" />
      <span className="bbn-card-note">{det ? tr('montecarlo.detNote') : th ? tr('montecarlo.bins', { a: th.counts.length }) : tr('montecarlo.na')}</span></div>
    {det
      ? <div className="mc-missing is-det" role="note" data-dist-det>{tr('montecarlo.distDet')}</div>
      : !th || !th.counts?.length || !view
      ? <div className="mc-missing" role="note">{tr('montecarlo.distMissingA')} <b>{tr('montecarlo.terminalHistName')}</b>{tr('montecarlo.distMissingB', { field: 'terminal_hist' })}</div>
      : <>
        <div className="mc-chart" ref={ref}><DistribuzioneSvg r={r} view={view} w={box.w} h={box.h} hover={hover} onHover={setHover} /></div>
        <div className="mc-dist-foot">
          {hover != null && th.edges_eur[hover + 1] != null
            ? <><span>{tr('montecarlo.distRange', { lo: fmtEUR(th.edges_eur[hover]), hi: fmtEUR(th.edges_eur[hover + 1]) })}</span>
                <b>{tr('montecarlo.distCount', { n: fmtInt(th.counts[hover]), p: fmtNum(tot ? (th.counts[hover] / tot) * 100 : null, 2) + '%' })}</b></>
            : <span>{tr('montecarlo.distHint')}</span>}
          {pctFuori != null && pctFuori > 0 && <span className="mc-down">
            {tr('montecarlo.outside', { out: fmtInt(fuori!.out), tot: fmtInt(fuori!.tot), p: fmtNum(pctFuori, 2) + '%' })}</span>}
        </div>
      </>}
  </section>;
}

function DistribuzioneSvg({ r, view, w, h, hover, onHover }: {
  r: MonteCarloResult; view: Finestra; w: number; h: number; hover: number | null; onHover: (i: number | null) => void;
}) {
  const th = r.terminal_hist!;
  if (w < 60 || h < 60) return <svg />;
  const L = 0, R = w - 84, T = 4, B = h - 4;
  const y = (v: number) => B - ((v - view.lo) / (view.hi - view.lo)) * (B - T);
  const mx = Math.max(1, ...th.counts);
  const nav = r.base_nav_eur;
  const etichette = (['p95', 'p50', 'p5'] as const).map(k => ({ k, v: r.percentiles_eur?.[k] })).filter(e => e.v != null && isFinite(e.v)) as { k: string; v: number }[];
  const ey = scosta(etichette.map(e => y(e.v)), 28);
  const muovi = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const py = ((e.clientY - rect.top) * h) / Math.max(1, rect.height);
    let best: number | null = null;
    th.counts.forEach((_, i) => { if (py >= y(th.edges_eur[i + 1]) && py <= y(th.edges_eur[i])) best = i; });
    onHover(best);
  };
  return <svg width={w} height={h} onMouseMove={muovi} onMouseLeave={() => onHover(null)}>
    {th.counts.map((c, i) => {
      const lo = th.edges_eur[i], hi = th.edges_eur[i + 1], mid = (lo + hi) / 2;
      if (mid < view.lo || mid > view.hi) return null;
      const yt = y(hi), yb = y(lo);
      return <rect key={i} className={'mc-bin ' + (mid < nav ? 'is-down' : 'is-up') + (hover === i ? ' is-on' : '')}
        x={L} y={yt + 0.5} width={Math.max(1.5, (c / mx) * (R - L - 4))} height={Math.max(1, yb - yt - 1.5)} rx={2} />;
    })}
    <line className="mc-start" x1={L} x2={w - 2} y1={y(nav)} y2={y(nav)} />
    {etichette.map(({ k, v }, i) => <g key={k} className={k === 'p50' ? 'mc-end is-med' : 'mc-end'}>
      <line x1={R} x2={R + 6} y1={y(v)} y2={y(v)} />
      <text className="mc-end-k" x={R + 10} y={ey[i] - 3}>{k.toUpperCase()}</text>
      <text className="mc-end-v" x={R + 10} y={ey[i] + 11}>{fmtEUR(v)}</text>
    </g>)}
  </svg>;
}
