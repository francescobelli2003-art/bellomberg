import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { BenchmarkPayload } from '@/lib/api';
import type { EsitoCurva } from '@/lib/curva';
import { allineaBenchmark } from '@/lib/curva-benchmark';
import { fmtEUR, fmtNum, fmtPct } from '@/lib/format';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';
import { Segmenti } from '@/components/nuova/Card';
import { parole } from './parole';

export type Periodo = '1G' | '1S' | '1M' | '1A' | 'Tutto';
/** Cifra grande: NAV = valore quota (TWR), Cash = euro di titoli + liquidità. */
export type VistaHero = 'nav' | 'cash';
/** Punti della serie giornaliera per periodo (giorni di borsa). 1G = ieri → adesso. */
const PUNTI: Record<Periodo, number> = { '1G': 2, '1S': 6, '1M': 22, '1A': 253, Tutto: Number.POSITIVE_INFINITY };

const finito = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const riduciMovimento = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** Cifra con le cifre raggruppate anche a quattro cifre (6.576,18 €); senza valuta per la quota. */
function partiCifra(value: number, euro: boolean) {
  const testo = new Intl.NumberFormat(localeDi(linguaCorrente()), {
    ...(euro ? { style: 'currency', currency: 'EUR' } : {}), minimumFractionDigits: 2, maximumFractionDigits: 2,
    useGrouping: 'always' as unknown as boolean,
  }).formatToParts(value);
  const cut = testo.findIndex(part => part.type === 'decimal');
  const join = (parts: Intl.NumberFormatPart[]) => parts.map(part => part.value).join('');
  return cut < 0 ? { intero: join(testo), resto: '' } : { intero: join(testo.slice(0, cut)), resto: join(testo.slice(cut)) };
}

/** Conteggio all'apertura: una volta sola, da 0 al primo valore valido. */
function useConteggioIniziale(target: number | null) {
  const [shown, setShown] = useState<number | null>(target);
  const done = useRef(false);
  useEffect(() => {
    if (target == null) { setShown(null); return; }
    if (done.current || riduciMovimento() || typeof requestAnimationFrame !== 'function') { done.current = true; setShown(target); return; }
    done.current = true;
    let frame = 0;
    const start = performance.now();
    const step = (now: number) => {
      const k = Math.min(1, (now - start) / 750);
      setShown(target * (1 - Math.pow(1 - k, 3)));
      if (k < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target]);
  return shown;
}

function percorso(punti: Array<[number, number]>) {
  if (!punti.length) return '';
  let d = `M${punti[0][0].toFixed(2)} ${punti[0][1].toFixed(2)}`;
  for (let i = 1; i < punti.length; i++) {
    const a = punti[i - 2] || punti[i - 1], b = punti[i - 1], c = punti[i], e = punti[i + 1] || c;
    d += `C${(b[0] + (c[0] - a[0]) / 6).toFixed(2)} ${(b[1] + (c[1] - a[1]) / 6).toFixed(2)} ${(c[0] - (e[0] - b[0]) / 6).toFixed(2)} ${(c[1] - (e[1] - b[1]) / 6).toFixed(2)} ${c[0].toFixed(2)} ${c[1].toFixed(2)}`;
  }
  return d;
}

export interface DettaglioPatrimonio { etichetta: string; valore: string }

export default function HeroPatrimonio({ patrimonio, quota = null, vista = 'nav', onVista = () => {}, curva, spy, giorno, dettagli, avvisi, periodo, onPeriodo, spyAcceso, onSpy }: {
  patrimonio: number | null;
  /** valore quota corrente (ultimo punto TWR) con base e data d'inizio; null se la quota non c'è */
  quota?: { valore: number; base: number; dal: string } | null;
  vista?: VistaHero;
  onVista?: (vista: VistaHero) => void;
  curva: EsitoCurva;
  spy: BenchmarkPayload | null;
  giorno: { eur: number | null; pct: number | null; multiDay: boolean; windowLabel: string | null; parziale: boolean };
  dettagli: DettaglioPatrimonio[];
  avvisi: Array<{ testo: string; title?: string }>;
  periodo: Periodo;
  onPeriodo: (periodo: Periodo) => void;
  spyAcceso: boolean;
  onSpy: (acceso: boolean) => void;
}) {
  const w = parole();
  const idGradiente = useId().replace(/:/g, '');
  const nav = vista === 'nav';
  const cifraVista = nav ? (quota && finito(quota.valore) ? quota.valore : null) : (finito(patrimonio) ? patrimonio : null);
  const mostrato = useConteggioIniziale(cifraVista);
  const viva = curva.stato === 'viva' ? curva : null;

  const serie = useMemo(() => {
    if (!viva || viva.valori.length < 2) return null;
    const n = Math.min(viva.valori.length, PUNTI[periodo]);
    const da = viva.valori.length - n;
    const date = viva.date.slice(da), valori = viva.valori.slice(da), euro = viva.euro.slice(da);
    const bench = spy && !spy.error ? allineaBenchmark(date, valori, spy.dates, spy.index) : null;
    return { date, valori, euro, bench };
  }, [viva, periodo, spy]);

  // Il viewBox è la misura VERA del riquadro (px): niente preserveAspectRatio="none" (regola di casa:
  // deforma), la curva riempie il riquadro e crocino/punto in % restano allineati. Prima della misura
  // (SSR, test) vale 1000×300, il rapporto del CSS.
  const boxRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 1000, h: 300 });
  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver !== 'function') return;
    const osserva = new ResizeObserver(([voce]) => {
      const { width, height } = voce.contentRect;
      if (width > 0 && height > 0) setBox(b => (b.w === Math.round(width) && b.h === Math.round(height) ? b : { w: Math.round(width), h: Math.round(height) }));
    });
    osserva.observe(el);
    return () => osserva.disconnect();
  }, []);

  const geometria = useMemo(() => {
    if (!serie) return null;
    const W = box.w, H = box.h;
    const conSpy = spyAcceso && serie.bench ? serie.bench.valori.filter(finito) : [];
    const tutti = [...serie.valori, ...conSpy];
    const lo = Math.min(...tutti), hi = Math.max(...tutti);
    const pad = (hi - lo) * 0.12 || Math.max(Math.abs(hi) * 0.01, 1);
    const X = (i: number) => (i / (serie.valori.length - 1)) * W;
    const Y = (v: number) => H - ((v - lo + pad) / (hi - lo + 2 * pad)) * H * 0.9;
    const linea = percorso(serie.valori.map((v, i) => [X(i), Y(v)]));
    const benchPunti = spyAcceso && serie.bench
      ? serie.bench.valori.map((v, i) => [X(i), finito(v) ? Y(v) : NaN] as [number, number]).filter(p => finito(p[1]))
      : [];
    return { W, H, X, Y, linea, area: linea ? `${linea}L${W} ${H}L0 ${H}Z` : '', bench: percorso(benchPunti) };
  }, [serie, spyAcceso, box]);

  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => setHover(null), [serie]);
  const onMove = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!serie || !boxRef.current) return;
    const rect = boxRef.current.getBoundingClientRect();
    const i = Math.round(((event.clientX - rect.left) / Math.max(1, rect.width)) * (serie.valori.length - 1));
    setHover(Math.max(0, Math.min(serie.valori.length - 1, i)));
  };

  const fmtData = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString(localeDi(linguaCorrente()), { day: 'numeric', month: 'short' });
  const assi = serie ? [0, Math.floor((serie.date.length - 1) / 2), serie.date.length - 1] : [];

  // Pastiglia: 1G = P&L di oggi in euro e %; altri periodi = rendimento della quota.
  let pill: { valore: number | null; testo: string; title?: string };
  if (periodo === '1G' || !serie) {
    const suffisso = giorno.multiDay && giorno.windowLabel ? giorno.windowLabel : w.today;
    const parti = [fmtEUR(giorno.eur, true) + (giorno.parziale ? ' ±' : '')];
    if (finito(giorno.pct) && !giorno.multiDay) parti.push(fmtPct(giorno.pct));
    pill = { valore: giorno.eur, testo: `${parti.join(' · ')} ${suffisso}`,
      title: giorno.multiDay ? w.windowHint : giorno.parziale ? w.partialHint : undefined };
  } else {
    const ch = (serie.valori[serie.valori.length - 1] / serie.valori[0] - 1) * 100;
    const spyCh = serie.bench ? serie.bench.variazionePct : null;
    pill = { valore: ch, testo: `${fmtPct(ch)} ${w.inPeriod[periodo] || ''}`.trim(),
      title: `${w.chartHint}${spyCh != null ? ` · SPY ${fmtPct(spyCh)}` : ''}` };
  }

  const cifra = finito(mostrato) ? partiCifra(mostrato, !nav) : null;
  const sotto = nav
    ? (quota ? w.heroNavSub(fmtNum(quota.base, 0), new Date(quota.dal + 'T12:00:00').toLocaleDateString(localeDi(linguaCorrente()), { day: 'numeric', month: 'short', year: 'numeric' })) : null)
    : w.heroCashSub;
  // Nel tooltip «i» c'è sempre anche la cifra dell'altra vista.
  const righeInfo = nav ? [{ etichetta: w.heroCash, valore: fmtEUR(patrimonio) }, ...dettagli] : dettagli;
  const viste = [{ id: 'nav' as const, testo: w.heroNav, title: w.heroNavHint }, { id: 'cash' as const, testo: w.heroCash, title: w.heroCashHint }];
  const etichettePeriodo: Record<Periodo, string> = { '1G': w.period_1G, '1S': w.period_1S, '1M': w.period_1M, '1A': w.period_1A, Tutto: w.periodAll };
  const periodi = (['1G', '1S', '1M', '1A', 'Tutto'] as const).map(id => ({ id, testo: etichettePeriodo[id] }));

  const hp = hover != null && serie && geometria ? hover : null;
  return (
    <section className="bbn-card bbn-hero" aria-label={w.wealth}>
      <div className="bbn-hero-label">
        <span>{nav ? w.heroNav : w.heroCash}</span>
        <span className="bbn-info" tabIndex={0} role="img" aria-label={w.wealthInfo}
          title={righeInfo.map(d => `${d.etichetta}: ${d.valore}`).join('\n')}>i</span>
        <Segmenti etichetta={w.heroView} valore={vista} onChange={onVista} opzioni={viste} className="bbn-hero-vista" />
      </div>
      <div className="bbn-hero-value num" aria-live="off">
        {cifra ? <>{cifra.intero}<span className="bbn-hero-cents">{cifra.resto}</span></> : nav ? fmtNum(null) : fmtEUR(null)}
        {sotto && <span className="bbn-hero-sub">{sotto}</span>}
      </div>
      <div className="bbn-hero-row">
        <PastigliaVariazione valore={pill.valore} grande title={pill.title}>{pill.testo}</PastigliaVariazione>
        {avvisi.map(avviso => <span key={avviso.testo} className="bbn-warn-pill" role="status" title={avviso.title}>{avviso.testo}</span>)}
        <span className="bbn-grow" />
        <button type="button" className="bbn-toggle-chip" aria-pressed={spyAcceso} title={serie?.bench ? w.spyHint : w.spyMissing}
          disabled={!serie?.bench} onClick={() => onSpy(!spyAcceso)}><i aria-hidden="true" />{w.spy}</button>
        <Segmenti etichetta={w.periods} valore={periodo} onChange={onPeriodo} opzioni={periodi} />
      </div>
      <div className="bbn-chart" ref={boxRef} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {geometria && serie ? <>
          <svg viewBox={`0 0 ${geometria.W} ${geometria.H}`} aria-hidden="true" key={periodo} className="bbn-chart-svg">
            <defs>
              <linearGradient id={idGradiente} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="var(--bbn-chart-top)" />
                <stop offset="1" stopColor="var(--bbn-chart-bottom)" />
              </linearGradient>
            </defs>
            <path className="bbn-chart-area" d={geometria.area} fill={`url(#${idGradiente})`} />
            {geometria.bench && <path className="bbn-chart-spy" d={geometria.bench} />}
            <path className="bbn-chart-line" d={geometria.linea} />
          </svg>
          {hp != null && <>
            <div className="bbn-chart-cross" style={{ left: `${(hp / (serie.valori.length - 1)) * 100}%` }} />
            <div className="bbn-chart-dot" style={{ left: `${(hp / (serie.valori.length - 1)) * 100}%`, top: `${(geometria.Y(serie.valori[hp]) / geometria.H) * 100}%` }} />
            <div className="bbn-chart-tip" style={{ left: `clamp(80px, ${(hp / (serie.valori.length - 1)) * 100}%, calc(100% - 80px))` }}>
              <b className="num">{nav ? fmtNum(serie.valori[hp], 2) : fmtEUR(serie.euro[hp])}</b>
              <span>{fmtData(serie.date[hp])} · {nav ? fmtEUR(serie.euro[hp]) : `${w.unitValue.toLowerCase()} ${fmtNum(serie.valori[hp], 2)}`}
                {spyAcceso && serie.bench && finito(serie.bench.valori[hp]) && serie.bench.valori[serie.bench.da] > 0
                  ? ` · SPY ${fmtPct((serie.bench.valori[hp] / serie.bench.valori[serie.bench.da] - 1) * 100)}` : ''}</span>
            </div>
          </>}
        </> : <p className="bbn-empty" role="status">{curva.stato === 'viva' ? w.noPositions : curva.frase}</p>}
      </div>
      {serie && <div className="bbn-chart-axis num" aria-hidden="true">{assi.map((i, k) => <span key={k}>{fmtData(serie.date[i])}</span>)}</div>}
    </section>
  );
}
