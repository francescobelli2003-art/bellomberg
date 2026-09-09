import { useEffect, useState } from 'react';
import { Bellomberg, OhlcBar } from '@/lib/api';
import TerminalChart from '@/components/TerminalChart';
import { Cpu } from 'lucide-react';

/**
 * F1/F15 v3 — pannello grafico TV-grade CONDIVISO (toolbar + TerminalChart).
 * RANGE (quanto indietro) e TIMEFRAME candela (5M..1Mese) SEPARATI come su
 * TradingView, con matrice di validità sui limiti reali di yfinance
 * (5/15m max ~60g · 1H max ~2a). 4H = resample dichiarato dall'1H (il
 * provider non lo espone). fill = altezza reattiva al contenitore.
 * Va montato dentro .f1c/.obsx (usa tbar/tfg/tb del design OBSIDIAN COMMAND).
 */
const RANGES: ReadonlyArray<readonly [string, string, number]> = [
  ['1G', '1d', 1], ['5G', '5d', 5], ['1M', '1mo', 30], ['3M', '3mo', 91], ['6M', '6mo', 182], ['1A', '1y', 365], ['5A', '5y', 1825], ['MAX', 'max', 36500],
];
const INTERVALS: ReadonlyArray<readonly [string, string]> = [
  ['5M', '5m'], ['15M', '15m'], ['1H', '1h'], ['4H', '4h'], ['1G', '1d'], ['1S', '1wk'], ['1ME', '1mo'],
];
// limiti provider (yfinance): fuori da questi il fetch fallirebbe o tornerebbe vuoto
function intervalOk(interval: string, rangeDays: number): boolean {
  if (interval === '5m' || interval === '15m') return rangeDays <= 60;
  if (interval === '1h' || interval === '4h') return rangeDays <= 730;
  if (interval === '1wk') return rangeDays >= 30;
  if (interval === '1mo') return rangeDays >= 182;
  return true; // 1d
}

// resample OHLCV client-side (per il 4H dall'1H): o=primo, h=max, l=min, c=ultimo, v=somma
function resample(bars: OhlcBar[], group: number): OhlcBar[] {
  const out: OhlcBar[] = [];
  for (let i = 0; i < bars.length; i += group) {
    const chunk = bars.slice(i, i + group);
    if (!chunk.length) break;
    out.push({
      t: chunk[0].t,
      o: chunk[0].o,
      h: Math.max(...chunk.map(b => b.h)),
      l: Math.min(...chunk.map(b => b.l)),
      c: chunk[chunk.length - 1].c,
      v: chunk.reduce((s, b) => s + (b.v || 0), 0),
    });
  }
  return out;
}

export default function TvChartPanel({ ticker, height = 360, fill = false, defaultRange = 5, defaultInterval = 4 }: {
  ticker: string; height?: number; fill?: boolean; defaultRange?: number; defaultInterval?: number;
}) {
  const [ri, setRi] = useState(defaultRange);
  const [ii, setIi] = useState(defaultInterval);
  const [mode, setMode] = useState<'candle' | 'area' | 'line'>('candle');
  const [log, setLog] = useState(false);
  const [showSma, setShowSma] = useState(true);
  const [showVwap, setShowVwap] = useState(false);
  const [showRsi, setShowRsi] = useState(false);
  const [bars, setBars] = useState<OhlcBar[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // al passaggio del breakpoint impilato (grid<->flex) il chart viene RIMONTATO
  // pulito: evita il race dei binding interni di lightweight-charts sul reflow
  const [stacked, setStacked] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width:1160px)').matches);
  useEffect(() => {
    const mq = window.matchMedia('(max-width:1160px)');
    const on = () => setStacked(mq.matches);
    try { mq.addEventListener('change', on); } catch { return; }
    return () => { try { mq.removeEventListener('change', on); } catch {} };
  }, []);

  const rangeDays = RANGES[ri][2];
  // se il cambio range invalida il timeframe, ripiega su 1G (dichiarato dal bottone che si accende)
  useEffect(() => {
    if (!intervalOk(INTERVALS[ii][1], rangeDays)) setIi(4);
  }, [ri]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!ticker) return;
    let m = true;
    setBars(null); setErr(null);
    const period = RANGES[ri][1];
    const iv = INTERVALS[ii][1];
    const fetchIv = iv === '4h' ? '1h' : iv;
    Bellomberg.ohlc(ticker, period, fetchIv)
      .then(r => {
        if (!m) return;
        if (r.error || !r.bars?.length) { setErr(r.error || 'nessun dato'); return; }
        setBars(iv === '4h' ? resample(r.bars, 4) : r.bars);
      })
      .catch(e => { if (m) setErr(String(e?.message || e)); });
    return () => { m = false; };
  }, [ticker, ri, ii]);

  const ind = (on: boolean) => 'tb ind' + (on ? '' : ' off');
  const boxStyle = fill
    ? { flex: 1, minHeight: 220, padding: '4px 4px 0', display: 'flex', flexDirection: 'column' as const }
    : { flex: 1, minHeight: 0, padding: '4px 4px 0' };

  return (
    <>
      <div className="tbar num">
        <span className="tlab">RANGE</span>
        <span className="tfg">
          {RANGES.map(([lab], i) => (
            <button key={lab} onClick={() => setRi(i)} className={'tb' + (i === ri ? ' on' : '')}>{lab}</button>
          ))}
        </span>
        <span className="tlab">TF</span>
        <span className="tfg">
          {INTERVALS.map(([lab, iv], i) => {
            const okIv = intervalOk(iv, rangeDays);
            return (
              <button key={lab} onClick={() => okIv && setIi(i)} disabled={!okIv}
                      title={okIv ? (iv === '4h' ? '4H = resample dall’1H (il provider non lo espone)' : '') : 'timeframe non disponibile su questo range (limite dati provider)'}
                      className={'tb' + (i === ii ? ' on' : '') + (okIv ? '' : ' dis')}>{lab}</button>
            );
          })}
        </span>
        <span className="tfg">
          <button onClick={() => setMode('candle')} className={'tb' + (mode === 'candle' ? ' on' : '')}>CANDELE</button>
          <button onClick={() => setMode('area')} className={'tb' + (mode === 'area' ? ' on' : '')}>AREA</button>
          <button onClick={() => setMode('line')} className={'tb' + (mode === 'line' ? ' on' : '')}>LINEA</button>
        </span>
        <button onClick={() => setShowSma(s => !s)} className={ind(showSma)}>SMA 20/50</button>
        <button onClick={() => setShowVwap(s => !s)} className={ind(showVwap)}>VWAP</button>
        <button onClick={() => setShowRsi(s => !s)} className={ind(showRsi)}>RSI 14</button>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 3, alignItems: 'center' }}>
          {INTERVALS[ii][1] === '4h' && <span className="tlab" style={{ color: '#B97A00' }}>4H = RESAMPLE 1H</span>}
          <span className="tfg">
            <button onClick={() => setLog(false)} className={'tb' + (!log ? ' on' : '')}>LIN</button>
            <button onClick={() => setLog(true)} className={'tb' + (log ? ' on' : '')}>LOG</button>
          </span>
        </span>
      </div>
      <div style={boxStyle}>
        {err
          ? <div className="flex items-center justify-center text-crimson text-2xs font-mono" style={{ height: fill ? '100%' : height, minHeight: 200 }}>{err}</div>
          : !bars
            ? <div className="flex items-center justify-center text-faint text-2xs font-mono" style={{ height: fill ? '100%' : height, minHeight: 200 }}><Cpu size={12} className="animate-pulse mr-2" /> caricamento {ticker}...</div>
            : <TerminalChart key={stacked ? 'stk' : 'wide'} bars={bars} mode={mode} height={height} fill={fill} log={log} showSma={showSma} showVwap={showVwap} showRsi={showRsi} />}
      </div>
    </>
  );
}
