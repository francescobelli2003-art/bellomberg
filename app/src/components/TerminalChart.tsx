import { useEffect, useRef, useState } from 'react';
import type { OhlcBar } from '@/lib/api';

/**
 * UI v3 T2 - TerminalChart: candele/area professionali su lightweight-charts (motore TradingView OSS).
 * Import DINAMICO: se il pacchetto non e' installato in app/, il componente degrada con istruzione
 * invece di rompere il dev server. Compatibile v4 (addCandlestickSeries) e v5 (addSeries(CandlestickSeries)).
 */
type Mode = 'candle' | 'area' | 'line' | 'baseline';

/* serie aggiuntiva sovrapposta al pannello prezzi (es. cost basis tratteggiato
   in F2): punti {t: epoch s UTC, v: valore}, stile linea, label in legenda */
export interface ChartOverlay {
  /* v: null = gap (whitespace lightweight-charts): la linea si interrompe —
     per overlay a tratti (es. run di carry del benchmark ufficiale in F2) */
  points: { t: number; v: number | null }[];
  color: string;
  dashed?: boolean;
  label?: string;
  /* false = niente badge valore sull'asse per questo overlay (default true, invariato) */
  lastValue?: boolean;
}

const P = {
  grid: '#121a2e', text: '#8D9FC4', border: '#1a2440',
  up: '#21e0a0', down: '#ff3d60', line: '#29d3f2',
  sma20: '#ffa51e', sma50: '#9b7bff', vwap: 'rgba(41,211,242,0.55)', rsi: '#9b7bff',
};

function sma(bars: OhlcBar[], n: number) {
  const out: { time: number; value: number }[] = [];
  let acc = 0;
  for (let i = 0; i < bars.length; i++) {
    acc += bars[i].c;
    if (i >= n) acc -= bars[i - n].c;
    if (i >= n - 1) out.push({ time: bars[i].t, value: acc / n });
  }
  return out;
}

// VWAP rolling 30 barre (richiede volumi; tipico prezzo (H+L+C)/3)
function vwap(bars: OhlcBar[], n = 30) {
  const out: { time: number; value: number }[] = [];
  for (let i = 0; i < bars.length; i++) {
    let pv = 0, vv = 0;
    for (let k = Math.max(0, i - n + 1); k <= i; k++) {
      const tp = (bars[k].h + bars[k].l + bars[k].c) / 3;
      pv += tp * bars[k].v; vv += bars[k].v;
    }
    if (vv > 0) out.push({ time: bars[i].t, value: pv / vv });
  }
  return out;
}

// RSI 14 di Wilder
function rsi14(bars: OhlcBar[], n = 14) {
  const out: { time: number; value: number }[] = [];
  if (bars.length <= n) return out;
  let g = 0, l = 0;
  for (let i = 1; i <= n; i++) {
    const d = bars[i].c - bars[i - 1].c;
    if (d >= 0) g += d; else l -= d;
  }
  let ag = g / n, al = l / n;
  out.push({ time: bars[n].t, value: al === 0 ? 100 : 100 - 100 / (1 + ag / al) });
  for (let i = n + 1; i < bars.length; i++) {
    const d = bars[i].c - bars[i - 1].c;
    ag = (ag * (n - 1) + Math.max(0, d)) / n;
    al = (al * (n - 1) + Math.max(0, -d)) / n;
    out.push({ time: bars[i].t, value: al === 0 ? 100 : 100 - 100 / (1 + ag / al) });
  }
  return out;
}

const fmtPx = (v: number) =>
  v >= 1000 ? v.toLocaleString('it-IT', { maximumFractionDigits: 0 })
  : v >= 10 ? v.toFixed(2) : v.toFixed(4);

export default function TerminalChart({ bars, mode = 'candle', height = 360, fill = false, log = false, showSma = true, showVwap = false, showRsi = false, overlays, valueLegend = false }: {
  bars: OhlcBar[]; mode?: Mode; height?: number;
  /** fill: il grafico riempie il contenitore (altezza REATTIVA via ResizeObserver, stile TradingView) */
  fill?: boolean; log?: boolean; showSma?: boolean; showVwap?: boolean; showRsi?: boolean;
  /** overlays: serie linea sovrapposte (memoizzare nel caller: e' una dep dell'effetto) */
  overlays?: ReadonlyArray<ChartOverlay>;
  /** valueLegend: legenda a solo valore (serie NAV/TWR con o=h=l=c, la OHLC sarebbe rumore) */
  valueLegend?: boolean;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const el = boxRef.current;
    if (!el || !bars.length) return;
    let chart: any = null;
    let ro: ResizeObserver | null = null;
    let dead = false;
    let cleanupExtra: (() => void) | null = null;

    (async () => {
      let lw: any;
      try {
        lw = await import('lightweight-charts');
      } catch {
        if (!dead) setErr('motore grafico mancante: nella cartella  app/  esegui  npm install lightweight-charts  e ricarica');
        return;
      }
      if (dead || !boxRef.current) return;
      setErr(null);

      const hNow = () => (fill ? Math.max(200, el.clientHeight || height) : height);
      chart = lw.createChart(el, {
        width: el.clientWidth, height: hNow(),
        layout: {
          background: { color: 'transparent' }, textColor: P.text,
          fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
          attributionLogo: false,
        },
        grid: { vertLines: { color: P.grid }, horzLines: { color: P.grid } },
        rightPriceScale: { borderColor: P.border, mode: log ? 1 : 0 },
        timeScale: { borderColor: P.border, rightOffset: 3, minBarSpacing: 2 },
        crosshair: {
          mode: 0,
          vertLine: { color: 'rgba(41,211,242,0.45)', width: 1 as any, style: 3, labelBackgroundColor: '#0c111e' },
          horzLine: { color: 'rgba(41,211,242,0.45)', width: 1 as any, style: 3, labelBackgroundColor: '#0c111e' },
        },
      });

      // v5: addSeries(lw.CandlestickSeries, opts) - v4: addCandlestickSeries(opts)
      const v5 = typeof chart.addSeries === 'function' && lw.CandlestickSeries;
      const mk = (kind: string, opts: any) =>
        v5 ? chart.addSeries(lw[kind + 'Series'], opts) : chart['add' + kind + 'Series'](opts);

      let main: any;
      if (mode === 'candle') {
        main = mk('Candlestick', {
          upColor: P.up, downColor: P.down, borderUpColor: P.up, borderDownColor: P.down,
          wickUpColor: P.up, wickDownColor: P.down,
        });
        main.setData(bars.map(b => ({ time: b.t as any, open: b.o, high: b.h, low: b.l, close: b.c })));
      } else if (mode === 'line') {
        main = mk('Line', { color: P.line, lineWidth: 2 as any });
        main.setData(bars.map(b => ({ time: b.t as any, value: b.c })));
      } else if (mode === 'baseline') {
        // P/L attorno allo zero: verde sopra, rosso sotto (BaselineSeries nativa)
        main = mk('Baseline', {
          baseValue: { type: 'price', price: 0 },
          topLineColor: P.up, topFillColor1: 'rgba(33,224,160,0.25)', topFillColor2: 'rgba(33,224,160,0.02)',
          bottomLineColor: P.down, bottomFillColor1: 'rgba(255,61,96,0.02)', bottomFillColor2: 'rgba(255,61,96,0.25)',
          lineWidth: 2 as any,
        });
        main.setData(bars.map(b => ({ time: b.t as any, value: b.c })));
        try {
          main.createPriceLine({ price: 0, color: 'rgba(102,115,142,0.5)', lineWidth: 1 as any, lineStyle: 3, axisLabelVisible: false });
        } catch {}
      } else {
        main = mk('Area', {
          lineColor: P.line, topColor: 'rgba(41,211,242,0.22)', bottomColor: 'rgba(41,211,242,0)',
          lineWidth: 2 as any,
        });
        main.setData(bars.map(b => ({ time: b.t as any, value: b.c })));
      }

      // volumi: istogramma su scala overlay in basso
      const hasVol = bars.some(b => b.v > 0);
      if (hasVol) {
        const vol = mk('Histogram', { priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
        try { chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } }); } catch {}
        vol.setData(bars.map(b => ({
          time: b.t as any, value: b.v,
          color: b.c >= b.o ? 'rgba(33,224,160,0.32)' : 'rgba(255,61,96,0.32)',
        })));
      }

      // overlays del caller (es. cost basis tratteggiato): lineStyle 2 = dashed;
      // punti con v=null diventano whitespace (la linea si interrompe, non ponte)
      for (const ov of overlays || []) {
        if (!ov.points.length) continue;
        mk('Line', {
          color: ov.color, lineWidth: 1.5 as any, lineStyle: ov.dashed ? 2 : 0,
          priceLineVisible: false, lastValueVisible: ov.lastValue !== false,
        }).setData(ov.points.map(p => (p.v == null ? { time: p.t as any } : { time: p.t as any, value: p.v })));
      }

      // medie mobili
      if (showSma && bars.length > 22) mk('Line', { color: P.sma20, lineWidth: 1 as any, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(sma(bars, 20) as any);
      if (showSma && bars.length > 55) mk('Line', { color: P.sma50, lineWidth: 1 as any, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(sma(bars, 50) as any);

      // VWAP rolling (solo se ci sono volumi veri)
      if (showVwap && hasVol && bars.length > 5) {
        mk('Line', { color: P.vwap, lineWidth: 1 as any, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }).setData(vwap(bars) as any);
      }

      // RSI 14: pannello separato su v5 (panes); su v4 il pannello non esiste -> non mostrato
      if (showRsi && bars.length > 20 && v5) {
        try {
          const r = chart.addSeries(lw.LineSeries, {
            color: P.rsi, lineWidth: 1 as any, priceLineVisible: false, lastValueVisible: true,
            priceFormat: { type: 'custom', formatter: (v: number) => v.toFixed(0), minMove: 1 },
          }, 1);
          r.setData(rsi14(bars) as any);
          try {
            r.createPriceLine({ price: 70, color: 'rgba(255,61,96,0.4)', lineWidth: 1 as any, lineStyle: 3, axisLabelVisible: false });
            r.createPriceLine({ price: 30, color: 'rgba(33,224,160,0.4)', lineWidth: 1 as any, lineStyle: 3, axisLabelVisible: false });
          } catch {}
          try { chart.panes()[1].setHeight(Math.max(56, Math.round(hNow() * 0.18))); } catch {}
        } catch {}
      }

      // legenda OHLC viva (senza re-render React: scrive nel DOM)
      const setLegend = (b: OhlcBar | null) => {
        const lg = legendRef.current;
        if (!lg) return;
        const x = b || bars[bars.length - 1];
        const prev = b ? bars[Math.max(0, bars.indexOf(b) - 1)] : bars[Math.max(0, bars.length - 2)];
        if (valueLegend) {
          // serie a valore (NAV/TWR/P&L/underwater): valore + delta, mai piu' di 2 decimali
          const fv = (v: number) => Math.abs(v) >= 1000
            ? v.toLocaleString('it-IT', { maximumFractionDigits: 0 }) : v.toFixed(2);
          const d = prev ? x.c - prev.c : 0;
          const cl = d >= 0 ? '#21e0a0' : '#ff3d60';
          lg.innerHTML =
            '<span style="color:' + cl + '">' + fv(x.c) + '</span>' +
            (prev && prev !== x ? ' <span style="color:' + cl + '">' + (d >= 0 ? '+' : '−') + fv(Math.abs(d)) + '</span>' : '');
          return;
        }
        const chg = prev && prev.c ? ((x.c / prev.c) - 1) * 100 : 0;
        const cls = x.c >= x.o ? '#21e0a0' : '#ff3d60';
        lg.innerHTML =
          '<span style="color:#8D9FC4">O</span> ' + fmtPx(x.o) +
          ' <span style="color:#8D9FC4">H</span> ' + fmtPx(x.h) +
          ' <span style="color:#8D9FC4">L</span> ' + fmtPx(x.l) +
          ' <span style="color:#8D9FC4">C</span> <span style="font-weight:600;color:' + cls + '">' + fmtPx(x.c) + '</span>' +
          ' <span style="font-weight:600;color:' + (chg >= 0 ? '#21e0a0' : '#ff3d60') + '">' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</span>' +
          (x.v ? ' <span style="color:#8D9FC4">V</span> ' + Intl.NumberFormat('it-IT', { notation: 'compact' }).format(x.v) : '');
      };
      setLegend(null);
      const byTime = new Map(bars.map(b => [b.t, b]));
      chart.subscribeCrosshairMove((param: any) => {
        const t = param?.time;
        setLegend(typeof t === 'number' ? (byTime.get(t) || null) : null);
      });

      chart.timeScale().fitContent();
      // ── cintura anti-zoom/resize (ctrl+scroll compreso) ──────────────────
      // 1) guardia taglie-zero: durante il reflow dello zoom il RO puo' sparare
      //    0px e incastrare il canvas — mai applicare misure degeneri;
      // 2) window.resize: lo zoom del browser lo emette anche quando il RO tace;
      // 3) matchMedia dppx: cambio devicePixelRatio → ri-applica e ri-binda.
      const applySize = () => {
        if (dead || !chart || !boxRef.current) return;
        const w = boxRef.current.clientWidth;
        const h = fill ? Math.max(200, boxRef.current.clientHeight) : height;
        if (w < 40 || h < 40) return;
        try { chart.applyOptions(fill ? { width: w, height: h } : { width: w }); } catch {}
      };
      // schedulato via rAF: mai layout sincrono dentro il RO (niente "loop completed"),
      // e una sola applicazione per frame anche sotto raffiche di resize/zoom
      let rafId = 0;
      const applySizeRaf = () => { cancelAnimationFrame(rafId); rafId = requestAnimationFrame(applySize); };
      ro = new ResizeObserver(applySizeRaf);
      ro.observe(el);
      window.addEventListener('resize', applySizeRaf);
      let dprMq: MediaQueryList | null = null;
      const onDpr = () => { applySizeRaf(); bindDpr(); };
      const bindDpr = () => {
        try { dprMq?.removeEventListener('change', onDpr); } catch {}
        try {
          dprMq = window.matchMedia('(resolution: ' + window.devicePixelRatio + 'dppx)');
          dprMq.addEventListener('change', onDpr);
        } catch { dprMq = null; }
      };
      bindDpr();
      cleanupExtra = () => {
        cancelAnimationFrame(rafId);
        window.removeEventListener('resize', applySizeRaf);
        try { dprMq?.removeEventListener('change', onDpr); } catch {}
      };
    })();

    // cleanup blindato: ogni passo isolato; la remove() e' DIFFERITA di un frame
    // perche' lightweight-charts puo' avere un draw interno gia' schedulato in raf —
    // rimuovere subito lo fa atterrare su un oggetto disposed (bug noto della lib)
    return () => {
      dead = true;
      try { ro?.disconnect(); } catch {}
      try { cleanupExtra?.(); } catch {}
      const dying = chart;
      chart = null;
      if (dying) requestAnimationFrame(() => { try { dying.remove(); } catch {} });
    };
  }, [bars, mode, height, fill, log, showSma, showVwap, showRsi, overlays, valueLegend]);

  const hStyle = fill ? { height: '100%', minHeight: 200 } : { height };
  if (!bars.length) {
    return <div className="flex items-center justify-center text-faint text-2xs font-mono" style={hStyle}>nessun dato</div>;
  }
  if (err) {
    return <div className="flex items-center justify-center text-amber text-2xs font-mono px-6 text-center" style={hStyle}>{err}</div>;
  }
  return (
    <div className="relative" style={fill ? { height: '100%', display: 'flex', flexDirection: 'column', minHeight: 200 } : undefined}>
      <div ref={legendRef}
           className="absolute top-1.5 left-2 z-10 font-mono text-2xs tabular-nums pointer-events-none"
           style={{ color: '#ecf1fa', textShadow: '0 1px 4px rgba(0,0,0,0.8)' }} />
      <div className="absolute top-1.5 right-14 z-10 font-mono text-3xs pointer-events-none flex gap-3"
           style={{ textShadow: '0 1px 4px rgba(0,0,0,0.8)' }}>
        {showSma && <span style={{ color: P.sma20 }}>SMA20</span>}
        {showSma && <span style={{ color: P.sma50 }}>SMA50</span>}
        {showVwap && <span style={{ color: '#29d3f2' }}>VWAP</span>}
        {showRsi && <span style={{ color: P.rsi }}>RSI14</span>}
        {(overlays || []).map((o, i) => o.label ? <span key={'ov' + i} style={{ color: o.color }}>{o.label}</span> : null)}
      </div>
      <div ref={boxRef} style={fill ? { flex: 1, minHeight: 200 } : { height }} />
    </div>
  );
}
