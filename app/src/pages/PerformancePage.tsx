import { useEffect, useMemo, useRef, useState } from 'react';
import { Cpu, AlertCircle, AlertTriangle } from 'lucide-react';
import {
  Bellomberg, NavHistory, DrawdownsResult, LiquidityResult,
  ConcentrationResult, VarContributionResult, TwrPayload, OhlcBar,
  AdvancedMetrics, PortfolioSnapshot, AttributionPayload, BenchmarkPayload
} from '@/lib/api';
import { fmtEUR } from '@/lib/format';
import { computeDailyPnl } from '@/lib/dailypl';
import { portfolioValues } from '@/lib/portfolio-values';
import { FlashVal } from '@/components/Flash';
import TerminalChart, { ChartOverlay } from '@/components/TerminalChart';
import './dashboard-command.css';

/* ============================================================
   F2 v2 "OBSIDIAN" (23/07, mandato PM revisione pagina-per-pagina):
   vestito OBSIDIAN COMMAND (.obsx), PLUMBING DATI INVARIATO — stessi
   7 endpoint e stessi calcoli di slice/TWR di prima. Novita': P&L GG
   dell'intero portafoglio in vetrina (stessa semantica di F1, live
   30/60s) e B-UI13: gli SVG a mano distorti (NAV vs cost basis, P/L)
   sostituiti dal motore lightweight-charts condiviso (TerminalChart).
   ============================================================ */

type RangeKey = '1D' | '1W' | '1M' | '3M' | '1Y' | '5Y';
const RANGE_DAYS: Record<RangeKey, number> = {
  '1D': 1, '1W': 7, '1M': 30, '3M': 90, '1Y': 252, '5Y': 1260,
};
const RANGE_LABEL: Record<RangeKey, string> = {
  '1D': '1G', '1W': '1S', '1M': '1M', '3M': '3M', '1Y': '1A', '5Y': '5A',
};

// n.d. DICHIARATO su valore assente (regola 14/07); EUR via helper unico lib/format (B-UI15)
const eur = (v: number | null | undefined, sign = false) =>
  v != null && isFinite(v) ? fmtEUR(v, sign) : 'n.d.';
const pct = (v: number | null | undefined, sign = true) =>
  v != null && isFinite(v) ? (sign && v > 0 ? '+' : '') + v.toFixed(2) + '%' : 'n.d.';

function sliceFromEnd<T>(arr: T[], n: number): T[] {
  if (n >= arr.length) return arr.slice();
  return arr.slice(arr.length - n);
}

// fix 13/07: rimosso computeMaxDD locale (calcolato su NAV grezzo contaminato dai
// flussi e comunque NON renderizzato: le card Max DD leggono twr.metrics dal backend).

function fmtDateIt(iso: string | null | undefined): string {
  if (!iso) return '-';
  const p = iso.split('-');
  return p.length === 3 ? `${p[2]}/${p[1]}` : iso;
}

// data 'YYYY-MM-DD' -> epoch SECONDI UTC (stessa convenzione di twrBars, fix 30c)
const dayT = (d: string) => Math.floor(Date.parse(d + 'T00:00:00Z') / 1000);
// epoch qualsiasi -> mezzanotte UTC del giorno (allineamento del fallback CALC)
const dayKey = (t: number) => Math.floor(t / 86400) * 86400;

/* TWR mensile da una serie indice generica (fine mese / fine mese precedente − 1
   = TWR GIPS del mese, flussi gia' esclusi nell'indice); righe per anno, YTD
   sulla stessa base (fine anno precedente). Usata per book E benchmark. */
type MonthlyYear = { year: string; cells: (number | null)[]; ytd: number | null };
function monthlyFromSeries(dates: string[], values: number[]): { years: MonthlyYear[]; lastYm: string } | null {
  const n = Math.min(dates.length, values.length);
  if (n < 2) return null;
  const lastOfMonth = new Map<string, number>();
  const order: string[] = [];
  for (let i = 0; i < n; i++) {
    const ym = dates[i].slice(0, 7);
    if (!lastOfMonth.has(ym)) order.push(ym);
    lastOfMonth.set(ym, values[i]);
  }
  if (!order.length) return null;
  const years: MonthlyYear[] = [];
  let prevMonthVal = values[0];
  let yearStartVal = values[0];
  let cur: MonthlyYear | null = null;
  for (const ym of order) {
    const [y, m] = ym.split('-');
    if (!cur || cur.year !== y) {
      if (cur) { cur.ytd = yearStartVal > 0 ? (prevMonthVal / yearStartVal - 1) * 100 : null; yearStartVal = prevMonthVal; }
      cur = { year: y, cells: Array(12).fill(null), ytd: null };
      years.push(cur);
    }
    const v = lastOfMonth.get(ym)!;
    cur.cells[parseInt(m, 10) - 1] = prevMonthVal > 0 ? (v / prevMonthVal - 1) * 100 : null;
    prevMonthVal = v;
  }
  if (cur) cur.ytd = yearStartVal > 0 ? (prevMonthVal / yearStartVal - 1) * 100 : null;
  return { years, lastYm: order[order.length - 1] };
}

// media e dev.std di una finestra (rolling 30g)
const avg = (a: number[]) => a.reduce((s, v) => s + v, 0) / a.length;
const std = (a: number[]) => { const m = avg(a); return Math.sqrt(a.reduce((s, v) => s + (v - m) * (v - m), 0) / a.length); };

// cella statistica hairline (idioma OBSIDIAN, come la griglia stats di F15)
function K({ label, value, tone, sub }: {
  label: string; value: string; tone?: string; sub?: string;
}) {
  return (
    <div style={{ padding: '7px 12px', borderRight: '1px solid rgba(26,36,64,.6)', borderBottom: '1px solid rgba(26,36,64,.6)', minWidth: 0 }}>
      <div style={{ fontSize: 9, letterSpacing: '.18em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={label}>{label}</div>
      <div className={'num ' + (tone || '')} style={{ fontSize: 12, fontWeight: 500, marginTop: 2, color: tone ? undefined : '#ECF1FA', whiteSpace: 'nowrap' }}>{value}</div>
      {sub && <div style={{ fontSize: 9, fontWeight: 600, color: '#73829F', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={sub}>{sub}</div>}
    </div>
  );
}

/* banda-fonte del dato (F2 v3): un segmento colorato per ogni run di regimes —
   run-length reale dal payload, nessuna assunzione "un solo passaggio" */
function RegimeRibbon({ dates, regimes, officialSince }: {
  dates: string[]; regimes: ('reconstructed' | 'official')[]; officialSince: string | null | undefined;
}) {
  const n = Math.min(dates.length, regimes.length);
  if (n < 2) return null;
  const stops: string[] = [];
  let start = 0;
  for (let i = 1; i <= n; i++) {
    if (i === n || regimes[i] !== regimes[start]) {
      const c = regimes[start] === 'official' ? 'rgba(33,224,160,.38)' : 'rgba(255,165,30,.30)';
      stops.push(`${c} ${(start / n * 100).toFixed(2)}%, ${c} ${(i / n * 100).toFixed(2)}%`);
      start = i;
    }
  }
  const firstOff = regimes.findIndex(r => r === 'official');
  const pct = firstOff > 0 ? (firstOff / n) * 100 : null;
  return (
    <div className="ribx num" title="fonte del dato sotto ogni punto della serie TWR: ambra = ricostruita (chiusure), verde = ufficiale (snapshot NAV)">
      <div className="band" style={{ background: `linear-gradient(90deg, ${stops.join(', ')})` }} />
      {firstOff !== 0 && <span className="rlab" style={{ left: 2, color: '#8A6210' }}>SERIE RICOSTRUITA (CHIUSURE)</span>}
      {pct != null && (
        <>
          <span className="rdiv" style={{ left: pct + '%' }} />
          <span className="rlab" style={{ left: `min(${pct.toFixed(2)}% + 7px, 82%)`, color: '#21E0A0' }}>
            UFFICIALE{officialSince ? ' DAL ' + fmtDateIt(officialSince) : ''}
          </span>
        </>
      )}
      {firstOff === 0 && <span className="rlab" style={{ left: 2, color: '#21E0A0' }}>SERIE UFFICIALE (SNAPSHOT NAV)</span>}
    </div>
  );
}

/* heatmap mensile (F2 v3): righe per anno×serie (BOOK / SPY / Δ), celle a
   intensita' |ritorno|, mese in corso = MTD dichiarato, colonna YTD */
type HeatRow = { label: string; sub: string; cells: (number | null)[]; ytd: number | null; unit: '%' | ' pt' };
type YearGroup = { year: string; rows: HeatRow[] };
function MonthlyHeatmap({ groups, lastYm }: { groups: YearGroup[]; lastYm: string }) {
  const MESI = ['GEN', 'FEB', 'MAR', 'APR', 'MAG', 'GIU', 'LUG', 'AGO', 'SET', 'OTT', 'NOV', 'DIC'];
  const cell = (v: number | null, mtd: boolean, unit: string, key: string) => {
    if (v == null) return <td key={key} style={{ background: 'rgba(154,166,192,.035)' }}><span className="pl" style={{ color: '#232D4A' }}>—</span></td>;
    const a = Math.min(0.10 + Math.abs(v) / 15, 0.52);
    const rgb = v >= 0 ? '33,224,160' : '255,61,96';
    return (
      <td key={key} style={{ background: `rgba(${rgb},${a})` }} title={(mtd ? 'mese in corso (MTD, parziale dichiarato) · ' : '') + 'rendimento del mese dalla serie indice'}>
        <div className="pv" style={{ color: v >= 0 ? '#C4F7E3' : '#FFD4DC' }}>{(v >= 0 ? '+' : '') + v.toFixed(1) + (unit === '%' ? '%' : '')}</div>
        {mtd && <div className="pl">MTD</div>}
      </td>
    );
  };
  return (
    <table className="hmx num">
      <thead>
        <tr>
          <th>SERIE</th>
          {MESI.map(m => <th key={m}>{m}</th>)}
          <th className="ytdh">YTD</th>
        </tr>
      </thead>
      <tbody>
        {groups.map(g => g.rows.map((r, ri) => (
          <tr key={g.year + '·' + r.label}>
            <td className="rowlab" style={ri === 0 ? { borderTop: '1px solid #2A3760' } : undefined}>{r.label}<small>{r.sub}</small></td>
            {r.cells.map((v, i) => cell(v, g.year + '-' + String(i + 1).padStart(2, '0') === lastYm, r.unit, g.year + '-' + i))}
            <td className="ytdc" style={{ background: `rgba(${(r.ytd ?? 0) >= 0 ? '33,224,160' : '255,61,96'},.18)` }}>
              {r.ytd != null ? (
                <>
                  <div className="pv" style={{ color: r.ytd >= 0 ? '#C4F7E3' : '#FFD4DC' }}>{(r.ytd >= 0 ? '+' : '') + r.ytd.toFixed(2) + (r.unit === '%' ? '%' : ' pt')}</div>
                  <div className="pl">YTD</div>
                </>
              ) : <span className="pl">n.d.</span>}
            </td>
          </tr>
        )))}
      </tbody>
    </table>
  );
}

// sparkline zero-dep per il pannello ROLLING (stesso spirito di NavSpark in F1)
function Spark({ pts, color, h = 24 }: { pts: number[]; color: string; h?: number }) {
  if (pts.length < 2) return <span className="pl" style={{ fontWeight: 600, color: '#73829F' }}>n.d.</span>;
  const W = 160, mn = Math.min(...pts), mx = Math.max(...pts), span = (mx - mn) || 1;
  let d = '';
  for (let i = 0; i < pts.length; i++) {
    const x = i / (pts.length - 1) * W, y = 2 + (1 - (pts[i] - mn) / span) * (h - 4);
    d += (i ? ' L' : 'M') + x.toFixed(1) + ',' + y.toFixed(1);
  }
  return (
    <svg width={W} height={h} viewBox={`0 0 ${W} ${h}`} style={{ flexShrink: 0 }}>
      <path d={d} fill="none" stroke={color} strokeWidth="1.2" />
      <circle cx={W} cy={2 + (1 - (pts[pts.length - 1] - mn) / span) * (h - 4)} r="2" fill={color} />
    </svg>
  );
}

/* ATTRIBUTION (fase 1b backend, voce (36)): contribution Carino per posizione /
   bucket economico / valuta. Endpoint attivo dal riavvio del backend: 404 =
   buco dichiarato che si accende da solo (stesso pattern del P&L GG). */
const ATTR_PERIODS = ['MTD', '30D', 'YTD', 'INCEPTION'] as const;
type AttrPeriod = typeof ATTR_PERIODS[number];

// barra bipolare attorno allo zero (scala = max |contributo| del gruppo, dichiarata)
function BiBar({ v, max }: { v: number; max: number }) {
  const half = Math.min(50, Math.abs(v) / (max || 1) * 50);
  return (
    <div style={{ position: 'relative', height: 5, background: 'rgba(26,36,64,.85)', minWidth: 90 }}
         title={'fondo scala barre: ±' + max.toFixed(2) + ' pt (max |contributo| del gruppo)'}>
      <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: '#2A3760' }} />
      <div style={{
        position: 'absolute', top: 0, bottom: 0,
        left: v >= 0 ? '50%' : (50 - half) + '%', width: half + '%',
        background: v >= 0 ? '#21E0A0' : '#FF3D60', opacity: 0.8,
      }} />
    </div>
  );
}

function AttributionPanel() {
  const [period, setPeriod] = useState<AttrPeriod>('YTD');
  const [data, setData] = useState<AttributionPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let m = true;
    setLoading(true); setErr(null); setData(null);
    Bellomberg.attribution(period)
      .then(d => { if (m) setData(d); })
      .catch(e => {
        if (!m) return;
        const st = e?.response?.status;
        setErr(st === 404
          ? 'endpoint /portfolio/attribution non ancora attivo sul backend vivo: si accende al riavvio (fase 1b, voce (36))'
          : String(e?.response?.data?.detail || e?.message || e));
      })
      .finally(() => { if (m) setLoading(false); });
    return () => { m = false; };
  }, [period]);

  const maxPos = data?.by_position?.length ? Math.max(...data.by_position.map(p => Math.abs(p.contribution_pct))) : 1;
  const maxBk = data?.by_bucket?.length ? Math.max(...data.by_bucket.map(b => Math.abs(b.contribution_pct))) : 1;
  const maxCy = data?.by_currency?.length ? Math.max(...data.by_currency.map(c => Math.abs(c.contribution_pct))) : 1;
  const rec = data?.reconciliation;

  return (
    <div className="p3 cy">
      <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
      <div className="p3h">ATTRIBUTION // CONTRIBUTION
        <span className="n">· CARINO · CAPITALE INVESTITO (CASH ESCLUSO) · NON BRINSON</span>
        <span className="tfg" style={{ marginLeft: 8 }}>
          {ATTR_PERIODS.map(p => (
            <button key={p} className={'tb' + (p === period ? ' on' : '')} onClick={() => setPeriod(p)}>{p}</button>
          ))}
        </span>
        <span className="side num">
          {data && !data.error && data.portfolio_return_pct != null && (
            <>RITORNO {data.period?.label}: <span className={data.portfolio_return_pct >= 0 ? 'up' : 'dn'}>{pct(data.portfolio_return_pct)}</span>
              {rec && rec.delta_pp != null && (
                <span title={rec.note || ''}> · VS TWR UFF {pct(rec.official_twr_pct)} (Δ {rec.delta_pp >= 0 ? '+' : ''}{rec.delta_pp.toFixed(2)} pp — basi diverse dichiarate)</span>
              )}
              {rec?.error && <span title={rec.note || ''}> · RICONCILIAZIONE N.D. (DICHIARATO)</span>}
            </>
          )}
        </span>
      </div>

      {loading && (
        <div className="font-mono text-2xs text-faint" style={{ padding: '18px 12px', textAlign: 'center' }}>
          <Cpu size={12} className="inline animate-pulse mr-2" />calcolo attribution {period}… (prima chiamata: scarico candele, poi cache 10 min)
        </div>
      )}
      {err && !loading && (
        <div className="font-mono text-2xs text-amber" style={{ padding: '14px 12px' }}>⚠ {err}</div>
      )}
      {data?.error && !loading && (
        <div className="font-mono text-2xs text-amber" style={{ padding: '14px 12px' }}>⚠ attribution: {data.error}</div>
      )}

      {data && !data.error && !loading && (
        <>
          <div className="grid grid-cols-1 xl:grid-cols-3" style={{ gap: 0 }}>
            {/* BY POSITION: chi ha fatto il rendimento */}
            <div style={{ borderRight: '1px solid rgba(26,36,64,.6)' }}>
              <div style={{ fontSize: 9, letterSpacing: '.18em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase', padding: '6px 12px 2px' }}>BY POSITION · CONTRIBUTO IN PT DEL PERIODO</div>
              <table className="num">
                <thead><tr><th>Ticker</th><th style={{ textAlign: 'left' }}>—</th><th>Contrib</th><th>Locale</th><th>FX</th><th>Peso med</th></tr></thead>
                <tbody>
                  {(data.by_position || []).map(p => (
                    <tr key={p.ticker}>
                      <td style={{ color: '#ECF1FA' }}>{p.ticker}<span style={{ fontWeight: 600, color: '#73829F', marginLeft: 5, fontSize: 9 }}>{p.currency}</span></td>
                      <td style={{ width: 110 }}><BiBar v={p.contribution_pct} max={maxPos} /></td>
                      <td className={p.contribution_pct >= 0 ? 'up' : 'dn'} style={{ fontWeight: 600 }}>{(p.contribution_pct >= 0 ? '+' : '') + p.contribution_pct.toFixed(2)}</td>
                      <td className="text-muted">{(p.local_pct >= 0 ? '+' : '') + p.local_pct.toFixed(2)}</td>
                      <td className={Math.abs(p.fx_pct) < 0.005 ? 'text-muted' : p.fx_pct >= 0 ? 'up' : 'dn'}>{(p.fx_pct >= 0 ? '+' : '') + p.fx_pct.toFixed(2)}</td>
                      <td className="text-muted">{p.avg_weight_pct.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* BY BUCKET economico (asse unico fase 1b) */}
            <div style={{ borderRight: '1px solid rgba(26,36,64,.6)' }}>
              <div style={{ fontSize: 9, letterSpacing: '.18em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase', padding: '6px 12px 2px' }}>BY BUCKET ECONOMICO · ASSE UNICO</div>
              <table className="num">
                <tbody>
                  {(data.by_bucket || []).map(b => (
                    <tr key={b.bucket} title={b.tickers.join(' · ')}>
                      <td style={{ color: '#8D9FC4', maxWidth: 190, overflow: 'hidden', textOverflow: 'ellipsis' }}>{b.bucket}</td>
                      <td style={{ width: 130 }}><BiBar v={b.contribution_pct} max={maxBk} /></td>
                      <td className={b.contribution_pct >= 0 ? 'up' : 'dn'} style={{ fontWeight: 600 }}>{(b.contribution_pct >= 0 ? '+' : '') + b.contribution_pct.toFixed(2)}</td>
                      <td className="text-muted" style={{ fontSize: 8 }}>{b.tickers.length} TKR</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* BY CURRENCY + split totale */}
            <div>
              <div style={{ fontSize: 9, letterSpacing: '.18em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase', padding: '6px 12px 2px' }}>BY CURRENCY · CONTRIBUTO (DI CUI FX)</div>
              <table className="num">
                <tbody>
                  {(data.by_currency || []).map(c => (
                    <tr key={c.currency} title={c.tickers.join(' · ')}>
                      <td style={{ color: '#8D9FC4' }}>{c.currency}</td>
                      <td style={{ width: 130 }}><BiBar v={c.contribution_pct} max={maxCy} /></td>
                      <td className={c.contribution_pct >= 0 ? 'up' : 'dn'} style={{ fontWeight: 600 }}>{(c.contribution_pct >= 0 ? '+' : '') + c.contribution_pct.toFixed(2)}</td>
                      <td className={Math.abs(c.fx_contribution_pct) < 0.005 ? 'text-muted' : c.fx_contribution_pct >= 0 ? 'up' : 'dn'} style={{ fontSize: 9 }}>
                        fx {(c.fx_contribution_pct >= 0 ? '+' : '') + c.fx_contribution_pct.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.totals && (
                <div className="num" style={{ padding: '6px 12px', borderTop: '1px solid rgba(26,36,64,.6)', fontSize: 9, color: '#8D9FC4' }}>
                  SPLIT TOTALE: locale <span className={data.totals.local_pct >= 0 ? 'up' : 'dn'}>{(data.totals.local_pct >= 0 ? '+' : '') + data.totals.local_pct.toFixed(2)}</span>
                  {' '}· FX <span className={data.totals.fx_pct >= 0 ? 'up' : 'dn'}>{(data.totals.fx_pct >= 0 ? '+' : '') + data.totals.fx_pct.toFixed(2)}</span>
                  {' '}· cross <span className="text-muted">{(data.totals.cross_pct >= 0 ? '+' : '') + data.totals.cross_pct.toFixed(2)}</span>
                </div>
              )}
            </div>
          </div>
          {(data.excluded?.length || data.notes?.length) ? (
            <div style={{ padding: '5px 12px', borderTop: '1px solid #1A2440', fontSize: 9, color: '#8D9FC4', display: 'flex', flexDirection: 'column', gap: 2 }}>
              {(data.excluded || []).map(x => (
                <div key={x.ticker}>• escluso {x.partial ? 'PARZIALE ' : ''}{x.ticker}: {x.days_excluded}/{x.days_total} giorni ({Object.keys(x.reasons).join(', ')}) — dichiarato</div>
              ))}
              {(data.notes || []).map((n, i) => <div key={i}>• {n}</div>)}
            </div>
          ) : null}
          {data.basis && (
            <div style={{ padding: '4px 12px 6px', borderTop: '1px solid rgba(26,36,64,.4)', fontSize: 9, fontWeight: 600, color: '#73829F', letterSpacing: '.04em' }} title={data.basis}>
              BASE DICHIARATA: {data.basis}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// statistica dell'hero (fianco del P&L GG)
function HeroStat({ label, value, tone, sub }: {
  label: string; value: string; tone?: string; sub?: string;
}) {
  return (
    <div style={{ padding: '10px 16px', borderLeft: '1px solid rgba(26,36,64,.6)', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
      <div style={{ fontSize: 9, letterSpacing: '.2em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{label}</div>
      <div className={'num ' + (tone || '')} style={{ fontSize: 17, fontWeight: 300, marginTop: 2, color: tone ? undefined : '#ECF1FA', whiteSpace: 'nowrap' }}>{value}</div>
      {sub && <div style={{ fontSize: 9, fontWeight: 600, color: '#73829F', marginTop: 2, whiteSpace: 'nowrap' }}>{sub}</div>}
    </div>
  );
}

export default function PerformancePage() {
  const [navHist, setNavHist] = useState<NavHistory | null>(null);
  const [drawdowns, setDrawdowns] = useState<DrawdownsResult | null>(null);
  const [liquidity, setLiquidity] = useState<LiquidityResult | null>(null);
  const [concentration, setConcentration] = useState<ConcentrationResult | null>(null);
  const [varContrib, setVarContrib] = useState<VarContributionResult | null>(null);
  const [twr, setTwr] = useState<TwrPayload | null>(null);
  const [adv, setAdv] = useState<AdvancedMetrics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<RangeKey>('3M');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  // F2 v3.1 — due viste come F8 wire/desk (richiesta PM): TEARSHEET = performance
  // pura · BOOK & RISK = contabilita' e rischio (la coda scorrevole di prima)
  const [view, setView] = useState<'tearsheet' | 'risk'>('tearsheet');

  const loadAll = async (force = false) => {
    setLoading(true); setError(null);
    try {
      const [nh, dd, lq, cc, vc, tw, am] = await Promise.all([
        Bellomberg.navHistory(force).catch(e => ({ error: e?.message || 'failed' } as any)),
        Bellomberg.drawdowns(force).catch(e => ({ error: e?.message || 'failed' } as any)),
        Bellomberg.liquidity().catch(e => ({ error: e?.message || 'failed' } as any)),
        Bellomberg.concentration().catch(e => ({ error: e?.message || 'failed' } as any)),
        Bellomberg.varContribution().catch(e => ({ error: e?.message || 'failed' } as any)),
        Bellomberg.twr(force).catch(e => ({ error: e?.message || 'failed' } as any)),
        Bellomberg.metricsAdvanced().catch(e => ({ error: e?.message || 'failed' } as any)),
      ]);
      setNavHist(nh as NavHistory);
      setDrawdowns(dd as DrawdownsResult);
      setLiquidity(lq as LiquidityResult);
      setConcentration(cc as ConcentrationResult);
      setVarContrib(vc as VarContributionResult);
      setTwr(tw as TwrPayload);
      setAdv(am as AdvancedMetrics);
      setLastUpdated(new Date());
    } catch (e: any) {
      setError(e?.message || 'load failed');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadAll(false); }, []);

  // Auto-refresh every 5 minutes (force=false uses backend cache, refreshing every 10min anyway)
  useEffect(() => {
    const i = setInterval(() => loadAll(false), 5 * 60 * 1000);
    return () => clearInterval(i);
  }, []);

  // P&L GG in vetrina (richiesta PM 23/07 "anche in F2"): /portfolio ogni 30s +
  // forzatura /prices/update ogni 60s, STESSE cinture di F1 (throttle server 30s
  // rispettato, anti-accavallamento, pausa a finestra nascosta). Una sola pagina
  // e' montata alla volta: nessun raddoppio di forzature con F1.
  const [snap, setSnap] = useState<PortfolioSnapshot | null>(null);
  const forcing = useRef(false);
  useEffect(() => {
    let m = true;
    const read = () => Bellomberg.portfolio().then(p => { if (m) setSnap(p); }).catch(() => {});
    read();
    const a = setInterval(() => { if (!document.hidden) read(); }, 30 * 1000);
    const f = setInterval(() => {
      if (document.hidden || forcing.current) return;
      forcing.current = true;
      Bellomberg.updatePrices().then(read).catch(() => {})
        .finally(() => { forcing.current = false; });
    }, 60 * 1000);
    return () => { m = false; clearInterval(a); clearInterval(f); };
  }, []);
  // formula condivisa F1/F2 (lib/dailypl): n.d. dichiarato, % solo a copertura completa
  const daily = snap && Array.isArray(snap.positions) ? computeDailyPnl(snap.positions) : null;
  const dayTot = daily?.dayTot ?? null;
  const gcol = dayTot == null ? '#8D9FC4' : dayTot >= 0 ? '#21E0A0' : '#FF3D60';

  // ============ SLICE BY RANGE ============
  const slice = useMemo(() => {
    if (!navHist || !navHist.nav_eur || navHist.nav_eur.length === 0) {
      return {
        dates: [] as string[], nav: [] as number[],
        cost_basis: [] as number[], pnl: [] as number[],
        realized: [] as number[], dividends: [] as number[], totalPnl: [] as number[],
        navStart: 0, navEnd: 0, cbStart: 0, cbEnd: 0, pnlStart: 0, pnlEnd: 0,
        realizedEnd: 0, dividendsEnd: 0, totalPnlEnd: 0,
        itdReturnPctVsCB: 0, itdTotalReturnPctVsCB: 0,
        deltaPnlEur: 0, deltaTotalPnlEur: 0,
      };
    }
    const allDates = navHist.dates;
    const allNav = navHist.nav_eur;
    const allCB = navHist.cost_basis_eur || allNav.map(() => 0);
    const allPnl = navHist.pnl_eur || allNav.map((v, i) => v - (allCB[i] || 0));
    const allReal = navHist.realized_sales_eur || allNav.map(() => 0);   // realized da vendite (bugfix #164)
    const allDiv = navHist.dividend_income_eur || allNav.map(() => 0);
    const allTotal = navHist.total_return_eur || allPnl.map((p, i) => p + (allReal[i] || 0) + (allDiv[i] || 0));
    const n = RANGE_DAYS[range];
    const dates = sliceFromEnd(allDates, n);
    const nav = sliceFromEnd(allNav, n);
    const cost_basis = sliceFromEnd(allCB, n);
    const pnl = sliceFromEnd(allPnl, n);
    const realized = sliceFromEnd(allReal, n);
    const dividends = sliceFromEnd(allDiv, n);
    const totalPnl = sliceFromEnd(allTotal, n);
    const navStart = nav[0] || 0;
    const navEnd = nav[nav.length - 1] || 0;
    const cbStart = cost_basis[0] || 0;
    const cbEnd = cost_basis[cost_basis.length - 1] || 0;
    const pnlStart = pnl[0] || 0;
    const pnlEnd = pnl[pnl.length - 1] || 0;
    const realizedEnd = realized[realized.length - 1] || 0;
    const dividendsEnd = dividends[dividends.length - 1] || 0;
    const totalPnlEnd = totalPnl[totalPnl.length - 1] || 0;
    // ITD return % (always vs cost basis from inception)
    const itdReturnPctVsCB = cbEnd > 0 ? (pnlEnd / cbEnd * 100) : 0;
    const itdTotalReturnPctVsCB = cbEnd > 0 ? (totalPnlEnd / cbEnd * 100) : 0;
    // fix #30: il finto TWR frontend (dPnL/CostBasis: base sbagliata + numeratore
    // monco, audit 02 par.2.3) e' stato RIMOSSO. Il TWR vero, flow-adjusted GIPS,
    // arriva dal backend (GET /portfolio/analytics/twr) ed e' calcolato in twrSlice.
    // Period P/L delta (how much P/L grew in this window, mark-to-market)
    const deltaPnlEur = pnlEnd - pnlStart;
    // Period total P/L delta (unrealized + dividends accrued in window)
    const deltaTotalPnlEur = totalPnlEnd - (totalPnl[0] || 0);
    return { dates, nav, cost_basis, pnl, realized, dividends, totalPnl,
             navStart, navEnd, cbStart, cbEnd, pnlStart, pnlEnd,
             realizedEnd, dividendsEnd, totalPnlEnd,
             itdReturnPctVsCB, itdTotalReturnPctVsCB,
             deltaPnlEur, deltaTotalPnlEur };
  }, [navHist, range]);

  // ============ TWR SLICE (fix #30): indice TWR dal backend, sliced sul range ============
  // Proprieta' del TWR index: il rapporto tra due punti = TWR del periodo (flussi gia' esclusi).
  const twrSlice = useMemo(() => {
    if (!twr || twr.error || !twr.twr_index || twr.twr_index.length < 2) return null;
    const n = RANGE_DAYS[range];
    const idx = sliceFromEnd(twr.twr_index, n);
    const dts = sliceFromEnd(twr.dates || [], n);
    const regs = sliceFromEnd(twr.regimes || [], n);
    const periodTwrPct = idx[0] > 0 ? (idx[idx.length - 1] / idx[0] - 1) * 100 : 0;
    return { idx, dts, regs, periodTwrPct };
  }, [twr, range]);

  // fix 30c: TWR index -> bars piatti (o=h=l=c, v=0) per TerminalChart, t = epoch SECONDI UTC.
  // Serie ITD completa: la navigazione (zoom rotella / pan drag / crosshair) e' del motore grafico.
  const twrBars = useMemo<OhlcBar[]>(() => {
    if (!twr || twr.error || !twr.dates || !twr.twr_index) return [];
    const n = Math.min(twr.dates.length, twr.twr_index.length);
    const out: OhlcBar[] = [];
    for (let i = 0; i < n; i++) {
      const t = dayT(twr.dates[i]);
      const val = Number(twr.twr_index[i]);
      if (!isFinite(t) || !isFinite(val)) continue;
      out.push({ t, o: val, h: val, l: val, c: val, v: 0 });
    }
    return out;
  }, [twr]);

  // F2 v3.2 — BENCHMARK UFFICIALE BACKEND (voce (38)): serie SPY in EUR
  // TOTAL-RETURN (dividendi inclusi, coerente col book TWR total-return),
  // gia' allineata dal backend al calendario TWR con carry-forward CONTATO
  // (carried_flags per data) e rebased 100 sul primo giorno comune. Sostituisce
  // lo SPY×EURUSD client-side PROVVISORIO di v3.1 (decaduto: mai due misure
  // zitte dello stesso oggetto). 404 = backend pre-riavvio: buco dichiarato
  // che si accende da solo (stesso pattern di attribution / P&L GG).
  const [bench, setBench] = useState<BenchmarkPayload | null>(null);
  const [benchFb, setBenchFb] = useState<{ t: number; v: number }[] | null>(null);
  const [benchErr, setBenchErr] = useState<string | null>(null);
  useEffect(() => {
    let m = true;
    // FALLBACK PROVVISORIO DICHIARATO (richiesta PM live 25/07: "non c'e' piu'
    // il benchmark"): finche' l'endpoint (38) non risponde (backend pre-riavvio,
    // 404) torna lo SPY×EURUSD client-side di v3.1 ETICHETTATO CALC·PROVVISORIO
    // — proxy dichiarato, mai zitto (regola 14/07); decade DA SOLO al riavvio.
    const loadFallback = (why: string) => {
      Promise.all([Bellomberg.ohlc('SPY', '1y', '1d'), Bellomberg.ohlc('EURUSD=X', '1y', '1d')])
        .then(([spy, fx]) => {
          if (!m) return;
          if (spy.error || !spy.bars?.length || fx.error || !fx.bars?.length) {
            setBenchErr(why + ' · fallback CALC KO: ' + (spy.error || fx.error || 'serie SPY/FX vuota')); return;
          }
          const fxByDay = new Map<number, number>();
          for (const b of fx.bars) fxByDay.set(dayKey(b.t), b.c);
          const out: { t: number; v: number }[] = [];
          let lastFx: number | null = null;
          for (const b of spy.bars) {
            const k = dayKey(b.t);
            const f = fxByDay.get(k);
            if (f != null && f > 0) lastFx = f;
            if (lastFx != null) out.push({ t: k, v: b.c / lastFx });
          }
          if (out.length < 2) { setBenchErr(why + ' · fallback CALC KO: allineamento vuoto'); return; }
          setBenchFb(out); setBenchErr(null);
        })
        .catch(e => { if (m) setBenchErr(why + ' · fallback CALC KO: ' + String(e?.message || e)); });
    };
    Bellomberg.benchmark('SPY')
      .then(r => {
        if (!m) return;
        if (r.error || !r.dates?.length || !r.index?.length) {
          // il backend DICHIARA la serie n.d.: si rispetta, niente proxy sopra
          setBenchErr(r.error || 'serie benchmark vuota dal backend'); return;
        }
        setBench(r); setBenchErr(null);
      })
      .catch(e => {
        if (!m) return;
        const st = e?.response?.status;
        loadFallback(st === 404
          ? 'serie uff. si accende al riavvio backend (38)'
          : 'endpoint uff. KO: ' + String(st || e?.message || e));
      });
    return () => { m = false; };
  }, []);

  const benchIsOfficial = !!bench;

  // forma per chart/heatmap. Ramo UFFICIALE: la serie arriva GIA' allineata e
  // rebased dal backend (testa senza dato esclusa via leading_dropped, chip).
  // Ramo FALLBACK CALC: allineamento client-side v3.1 al calendario TWR
  // (carry-forward) + rebase sulla prima data — carried=false (flag non noti).
  const benchOnTwr = useMemo(() => {
    if (bench?.dates && bench.index && bench.dates.length >= 2) {
      const out: { d: string; t: number; r: number; carried: boolean }[] = [];
      for (let i = 0; i < bench.dates.length; i++) {
        const t = dayT(bench.dates[i]);
        const v = Number(bench.index[i]);
        if (!isFinite(t) || !isFinite(v)) continue;
        out.push({ d: bench.dates[i], t, r: v, carried: bench.carried_flags?.[i] === true });
      }
      return out.length >= 2 ? out : null;
    }
    if (!benchFb || !twr || twr.error || !twr.dates || twr.dates.length < 2) return null;
    const out: { d: string; t: number; v: number }[] = [];
    let j = 0; let last: number | null = null;
    for (const d of twr.dates) {
      const t = dayT(d);
      if (!isFinite(t)) continue;
      while (j < benchFb.length && benchFb[j].t <= t) { last = benchFb[j].v; j++; }
      if (last != null) out.push({ d, t, v: last });
    }
    if (out.length < 2) return null;
    const base = out[0].v;
    if (!(base > 0)) return null;
    return out.map(p => ({ d: p.d, t: p.t, r: p.v / base * 100, carried: false }));
  }, [bench, benchFb, twr]);

  // overlay sul grafico TWR: serie ufficiale tratteggiata + run di CARRY in
  // ambra sopra (weekend/festivi US: benchmark FERMO, non piatto — la
  // dichiarazione di qualita'-dato resa visibile nel grafico, come il ribbon).
  // Il run parte dal punto nativo precedente (il segmento si deve disegnare);
  // fuori dai run v=null = gap whitespace; badge d'asse spento (uno solo).
  const twrOverlays = useMemo<ChartOverlay[]>(() => {
    if (!benchOnTwr) return [];
    const ovs: ChartOverlay[] = [{
      points: benchOnTwr.map(p => ({ t: p.t, v: p.r })),
      color: 'rgba(154,166,192,.75)', dashed: true,
      label: benchIsOfficial ? 'SPY (EUR)·TR UFF.' : 'SPY (EUR)·CALC',
    }];
    if (benchOnTwr.some(p => p.carried)) {
      ovs.push({
        points: benchOnTwr.map((p, i) => ({
          t: p.t, v: (p.carried || benchOnTwr[i + 1]?.carried) ? p.r : null,
        })),
        color: 'rgba(255,165,30,.6)', dashed: true, label: 'CARRY', lastValue: false,
      });
    }
    return ovs;
  }, [benchOnTwr, benchIsOfficial]);

  // ITD del benchmark sulla finestra della serie TWR (per l'hero VS SPY)
  const spyItd = benchOnTwr ? benchOnTwr[benchOnTwr.length - 1].r - 100 : null;
  const bookItd = twr?.metrics?.twr_total_pct ?? null;
  const vsSpyPt = (spyItd != null && bookItd != null) ? bookItd - spyItd : null;

  // F2 v3 — UNDERWATER: drawdown continuo dal picco, derivato dall'indice TWR
  // (coerenza garantita col grafico sopra: stessa serie, stesso asse temporale)
  const uwBars = useMemo<OhlcBar[]>(() => {
    let peak = -Infinity;
    return twrBars.map(b => {
      peak = Math.max(peak, b.c);
      const dd = peak > 0 ? (b.c / peak - 1) * 100 : 0;
      return { t: b.t, o: dd, h: dd, l: dd, c: dd, v: 0 };
    });
  }, [twrBars]);

  // F2 v3 — MENSILI: book dall'indice TWR, benchmark dalla serie UFFICIALE
  // backend (gia' allineata); Δ = differenza in punti per anno/mese. MTD dichiarato.
  const monthly = useMemo<{ groups: YearGroup[]; lastYm: string } | null>(() => {
    if (!twr || twr.error || !twr.dates || !twr.twr_index) return null;
    const book = monthlyFromSeries(twr.dates, twr.twr_index);
    if (!book) return null;
    const benchM = benchOnTwr ? monthlyFromSeries(benchOnTwr.map(p => p.d), benchOnTwr.map(p => p.r)) : null;
    const groups: YearGroup[] = book.years.map(by => {
      const rows: HeatRow[] = [{ label: 'BOOK ' + by.year, sub: 'TWR GIPS', cells: by.cells, ytd: by.ytd, unit: '%' }];
      const sy = benchM?.years.find(y => y.year === by.year);
      if (sy) {
        rows.push({ label: 'SPY (EUR)', sub: benchIsOfficial ? 'BENCH · TR UFF.' : 'BENCH · CALC', cells: sy.cells, ytd: sy.ytd, unit: '%' });
        rows.push({
          label: 'Δ VS SPY', sub: 'PUNTI',
          cells: by.cells.map((v, i) => (v != null && sy.cells[i] != null) ? v - (sy.cells[i] as number) : null),
          ytd: (by.ytd != null && sy.ytd != null) ? by.ytd - sy.ytd : null, unit: ' pt',
        });
      }
      return { year: by.year, rows };
    });
    return { groups, lastYm: book.lastYm };
  }, [twr, benchOnTwr, benchIsOfficial]);

  // F2 v3.1 — ROLLING 30G sulla serie TWR: vol annualizzata, Sharpe (rf del
  // payload); v3.2: tracking error vs serie UFFICIALE sui SOLI GIORNI NATIVI
  // (carry ESCLUSI dal pairing, stessa semantica del beta backend voce (38):
  // benchmark fermo nel weekend vs book che si muove = rumore, non tracking)
  const rolling = useMemo(() => {
    if (!twr || twr.error || !twr.twr_index || twr.twr_index.length < 40) return null;
    const idx = twr.twr_index;
    const rets: number[] = [];
    for (let i = 1; i < idx.length; i++) rets.push(idx[i - 1] > 0 ? idx[i] / idx[i - 1] - 1 : 0);
    const rf = twr.metrics?.risk_free_used ?? 0;
    const W = 30;
    const vol: number[] = [], sharpe: number[] = [];
    for (let i = W; i <= rets.length; i++) {
      const win = rets.slice(i - W, i);
      const sd = std(win), mu = avg(win);
      vol.push(sd * Math.sqrt(252) * 100);
      sharpe.push(sd > 0 ? (mu * 252 - rf) / (sd * Math.sqrt(252)) : 0);
    }
    // coppie (book, bench) per DATA sui soli giorni nativi; il ritorno bench
    // del giorno dopo un carry = movimento dall'ultima chiusura nativa
    let te: number[] | null = null;
    if (benchOnTwr && twr.dates) {
      const bIdx = new Map<string, number>();
      benchOnTwr.forEach((p, k) => bIdx.set(p.d, k));
      const pairs: { r: number; b: number }[] = [];
      for (let i = 1; i < twr.dates.length; i++) {
        const k = bIdx.get(twr.dates[i]);
        if (k == null || k === 0) continue;
        const p = benchOnTwr[k], q = benchOnTwr[k - 1];
        if (p.carried || !(q.r > 0)) continue;
        pairs.push({ r: rets[i - 1], b: p.r / q.r - 1 });
      }
      if (pairs.length >= W) {
        te = [];
        for (let i = W; i <= pairs.length; i++) {
          const win = pairs.slice(i - W, i);
          te.push(std(win.map(x => x.r - x.b)) * Math.sqrt(252) * 100);
        }
      }
    }
    const tail = (a: number[]) => a.slice(-90);
    return {
      vol: tail(vol), volNow: vol[vol.length - 1],
      sharpe: tail(sharpe), sharpeNow: sharpe[sharpe.length - 1],
      te: te ? tail(te) : null, teNow: te && te.length ? te[te.length - 1] : null,
    };
  }, [twr, benchOnTwr]);

  // B-UI13: serie NAV / cost basis / P&L come bars piatti per il motore
  // lightweight-charts (via l'SVG a mano distorto: crosshair/zoom/pan nativi)
  const navBars = useMemo<OhlcBar[]>(() => {
    const out: OhlcBar[] = [];
    for (let i = 0; i < slice.dates.length; i++) {
      const t = dayT(slice.dates[i]);
      const v = Number(slice.nav[i]);
      if (!isFinite(t) || !isFinite(v)) continue;
      out.push({ t, o: v, h: v, l: v, c: v, v: 0 });
    }
    return out;
  }, [slice]);

  const cbOverlays = useMemo<ChartOverlay[]>(() => {
    const points: { t: number; v: number }[] = [];
    for (let i = 0; i < slice.dates.length; i++) {
      const t = dayT(slice.dates[i]);
      const v = Number(slice.cost_basis[i]);
      if (!isFinite(t) || !isFinite(v)) continue;
      points.push({ t, v });
    }
    return [{ points, color: '#FFA51E', dashed: true, label: 'COST BASIS' }];
  }, [slice]);

  const pnlBars = useMemo<OhlcBar[]>(() => {
    const out: OhlcBar[] = [];
    for (let i = 0; i < slice.dates.length; i++) {
      const t = dayT(slice.dates[i]);
      const v = Number(slice.pnl[i]);
      if (!isFinite(t) || !isFinite(v)) continue;
      out.push({ t, o: v, h: v, l: v, c: v, v: 0 });
    }
    return out;
  }, [slice]);

  const rl = RANGE_LABEL[range];

  return (
    <div className="obsx animate-fadeIn">

      {/* ══ HERO: P&L GG in vetrina + TWR + range ══ */}
      <div className="p3 hero">
        <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
        <div className="p3h am">PERFORMANCE // TWR ANALYTICS
          <span className="n">· CONTABILITA DA FONDO</span>
          <span className="side num">
            {lastUpdated ? 'AGG ' + lastUpdated.toLocaleTimeString('it-IT', { hour12: false }) + ' · ' : ''}AUTO 5MIN · P&L GG LIVE 30/60S
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'stretch', flexWrap: 'wrap', minHeight: 74 }}>
          <div style={{ padding: '9px 16px 11px' }}>
            <div style={{ fontSize: 9, letterSpacing: '.22em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase' }}>P&L GG // INTERO PORTAFOGLIO</div>
            <div className="num"
                 style={{ fontSize: 22, fontWeight: 700, marginTop: 3, lineHeight: 1.1, color: gcol }}
                 title={dayTot == null
                   ? 'prev_close/FX non ancora nel payload: riavviare il backend per il P&L daily'
                   : 'P&L di giornata dell’INTERO portafoglio vs chiusura precedente (live, refresh 30/60s)'
                     + (daily?.dayPartial ? ' — PARZIALE: posizioni senza prev_close/FX escluse (n.d.)' : '')
                     + (daily?.dayTotPct != null ? ' · % sul valore investito di ieri · barra: fondo scala 5%' : '')}>
              <FlashVal k="__plgg" value={dayTot}>{dayTot != null ? fmtEUR(dayTot, true) : 'n.d.'}</FlashVal>
              {daily?.dayTotPct != null && <span style={{ fontSize: 12, fontWeight: 600, marginLeft: 7 }}>{pct(daily.dayTotPct)}</span>}
              {daily?.dayPartial && <span style={{ fontSize: 11, fontWeight: 600, marginLeft: 7, color: '#B97A00' }}>±PARZ</span>}
            </div>
            <div className="meter" style={{ width: 180, marginTop: 6 }}>
              <i style={{ width: Math.min(100, Math.abs(daily?.dayTotPct ?? 0) * 20) + '%', background: dayTot == null ? '#414B68' : gcol }} />
            </div>
            <div style={{ fontSize: 9, fontWeight: 600, color: '#73829F', marginTop: 4, letterSpacing: '.1em', textTransform: 'uppercase' }}>vs chiusura prec. · fondo scala 5%</div>
          </div>
          <HeroStat label={'TWR ' + rl} value={twrSlice ? pct(twrSlice.periodTwrPct) : 'n.d.'}
                    tone={twrSlice ? (twrSlice.periodTwrPct >= 0 ? 'up' : 'dn') : undefined}
                    sub="FLOW-ADJUSTED (GIPS)" />
          <HeroStat label="TWR ITD" value={pct(twr?.metrics?.twr_total_pct)}
                    tone={(twr?.metrics?.twr_total_pct ?? 0) >= 0 ? 'up' : 'dn'}
                    sub="DALL'INIZIO" />
          <HeroStat label="VS SPY ITD"
                    value={vsSpyPt != null ? (vsSpyPt >= 0 ? '+' : '') + vsSpyPt.toFixed(2) + ' pt' : 'n.d.'}
                    tone={vsSpyPt != null ? (vsSpyPt >= 0 ? 'up' : 'dn') : undefined}
                    sub={vsSpyPt != null
                      ? 'BOOK ' + pct(bookItd) + ' · SPY € ' + pct(spyItd) + (benchIsOfficial ? ' · TR UFF.' : ' · CALC PROVV.')
                        + ((bench?.leading_dropped ?? 0) > 0 ? ' DA ' + fmtDateIt(bench?.base_date) : '')
                      : (benchErr ? 'BENCHMARK N.D. — ' + benchErr.slice(0, 44) : 'CARICO BENCHMARK…')} />
          <HeroStat label="NAV LIVE" value={eur(portfolioValues(snap).nav)}
                    sub={snap ? portfolioValues(snap).note || 'INV + CASH · ' + (snap.n_positions || snap.positions?.length || 0) + ' POS' : 'CARICAMENTO'} />
          <div style={{ marginLeft: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', justifyContent: 'center', gap: 6, padding: '8px 14px' }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              <span className="tfg">
                <button className={'tb' + (view === 'tearsheet' ? ' on' : '')} onClick={() => setView('tearsheet')}>TEARSHEET</button>
                <button className={'tb' + (view === 'risk' ? ' on' : '')} onClick={() => setView('risk')}>BOOK &amp; RISK</button>
              </span>
              <span className="tlab">RANGE</span>
              <span className="tfg">
                {(['1D', '1W', '1M', '3M', '1Y', '5Y'] as RangeKey[]).map(r => (
                  <button key={r} onClick={() => setRange(r)} className={'tb' + (range === r ? ' on' : '')}>{RANGE_LABEL[r]}</button>
                ))}
              </span>
              <button className="tb" onClick={() => loadAll(true)} disabled={loading} style={{ color: '#29D3F2' }}>
                {loading ? 'CALCOLO…' : '↻ REFRESH'}
              </button>
            </div>
            {navHist && !navHist.error && (
              <div className="num" style={{ fontSize: 9, fontWeight: 600, color: '#73829F', letterSpacing: '.1em', textTransform: 'uppercase' }}>
                {slice.dates.length} giorni mostrati{slice.dates.length < RANGE_DAYS[range] ? ' (solo ' + slice.dates.length + ' disponibili)' : ''}
              </div>
            )}
          </div>
        </div>
      </div>

      {error && (
        <div className="border border-crimson/40 bg-crimson/5 px-3 py-2 font-mono text-2xs text-crimson flex items-center gap-2">
          <AlertCircle size={12} /> {error}
        </div>
      )}

      {loading && !navHist && (
        <div className="p3" style={{ padding: '28px 12px', textAlign: 'center' }}>
          <div className="font-mono text-2xs text-muted"><Cpu size={13} className="inline animate-pulse mr-2" />
            Ricostruzione NAV + cost basis da trade_history + yfinance…</div>
          <div className="font-mono text-3xs text-faint mt-1">~30-60s alla prima run, poi cache 10 min</div>
        </div>
      )}

      {view === 'tearsheet' && <>

      {/* ══ RITORNI (contabilita' da fondo) ══ */}
      {navHist && !navHist.error && (
        <div className="p3">
          <div className="p3h">RITORNI // CONTABILITA DA FONDO
            <span className="n">· RANGE {rl}</span>
            <span className="side">ITD SU COSTO — NON FLOW-ADJ., V. TWR</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8">
            <K label="Market Value" value={eur(slice.navEnd)} sub={'cost basis ' + eur(slice.cbEnd)} />
            <K label={'Return ' + rl + ' (TWR)'}
               value={twrSlice ? pct(twrSlice.periodTwrPct) : 'n.d.'}
               tone={twrSlice ? (twrSlice.periodTwrPct >= 0 ? 'up' : 'dn') : undefined}
               sub={twrSlice ? 'flow-adjusted (GIPS)' : 'TWR engine non disponibile'} />
            <K label="Max DD ITD (TWR)"
               value={pct(twr?.metrics?.max_drawdown_pct ?? drawdowns?.max_drawdown_pct, false)}
               tone="dn"
               sub={twr?.metrics ? 'su indice TWR flow-adjusted' : 'serie grezza (non flow-adjusted)'} />
            <K label="Total Return ITD (su costo)"
               value={pct(slice.itdTotalReturnPctVsCB)}
               tone={slice.itdTotalReturnPctVsCB >= 0 ? 'up' : 'dn'}
               sub={eur(slice.totalPnlEnd, true) + ' vs cost basis — NON flow-adj., v. TWR'} />
            <K label="Unrealized P/L" value={eur(slice.pnlEnd, true)}
               tone={slice.pnlEnd >= 0 ? 'up' : 'dn'} sub="mark-to-market ITD" />
            <K label="Realized P/L" value={eur(slice.realizedEnd, true)}
               tone={slice.realizedEnd > 0 ? 'up' : slice.realizedEnd < 0 ? 'dn' : 'text-muted'}
               sub="da vendite (avg cost)" />
            <K label="Dividendi" value={eur(slice.dividendsEnd, true)}
               tone={slice.dividendsEnd > 0 ? 'up' : 'text-muted'} sub="cash income ITD" />
            <K label="Cash disponibile" value={eur(navHist.cash_eur)} tone="text-cyan" sub="per nuovi trade" />
          </div>
        </div>
      )}

      {/* ══ TWR INDEX (fix #30: contabilita' da fondo, flussi esterni esclusi) ══ */}
      {twr && !twr.error && twrSlice && (
        <div className="p3 cy">
          <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
          <div className="p3h">TWR INDEX (GIPS)
            <span className="n">· FLUSSI ESTERNI ESCLUSI</span>
            {twr.regime_summary?.official_since ? (
              <span className="chip g">SERIE UFFICIALE DA SNAPSHOT DAL {fmtDateIt(twr.regime_summary.official_since)}</span>
            ) : (
              <span className="chip a">SERIE RICOSTRUITA (CHIUSURE, PRE-SNAPSHOT)</span>
            )}
            {/* F2 v3.2 — idea chat backend resa: coverage e carry MAI zitti */}
            {bench && benchOnTwr && (
              <span className="chip n"
                    title={"serie benchmark ufficiale backend [src: " + (bench.src || 'benchmark_series') + "] — TOTAL-RETURN (dividendi inclusi), EUR, allineata al calendario TWR; giorni CARRY = benchmark fermo (weekend/festivi US), resi in ambra sull'overlay"
                      + ((bench.leading_dropped ?? 0) > 0 ? '; testa senza dato esclusa: ' + bench.leading_dropped + 'g' : '')}>
                BENCH {bench.ticker || 'SPY'} TR · COV {bench.coverage_pct != null ? bench.coverage_pct.toFixed(1) + '%' : 'n.d.'} · CARRY {bench.carried_days ?? 'n.d.'}G
              </span>
            )}
            <span className="side num">BASE 100 = {twr.dates?.[0] ?? '-'} · {twrSlice.dts[0]} → {twrSlice.dts[twrSlice.dts.length - 1]}</span>
          </div>

          {/* metriche calcolate SULLA serie TWR (backend) */}
          <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
            <K label={'TWR ' + rl} value={pct(twrSlice.periodTwrPct)}
               tone={twrSlice.periodTwrPct >= 0 ? 'up' : 'dn'} sub="periodo selezionato" />
            <K label="TWR ITD" value={pct(twr.metrics?.twr_total_pct)}
               tone={(twr.metrics?.twr_total_pct ?? 0) >= 0 ? 'up' : 'dn'} sub="dall'inizio" />
            <K label="TWR ann." value={pct(twr.metrics?.twr_annualized_pct)}
               tone={(twr.metrics?.twr_annualized_pct ?? 0) >= 0 ? 'up' : 'dn'} sub="annualizzato" />
            <K label="Max DD (TWR)" value={pct(twr.metrics?.max_drawdown_pct, false)}
               tone="dn" sub={'corrente: ' + pct(twr.metrics?.current_drawdown_pct, false)} />
            <K label="Sharpe (TWR)" value={twr.metrics?.sharpe != null ? twr.metrics.sharpe.toFixed(2) : 'n.d.'}
               tone="text-cyan" sub={'rf ' + ((twr.metrics?.risk_free_used ?? 0) * 100).toFixed(2) + '%'} />
            <K label="Vol ann. (TWR)" value={pct(twr.metrics?.vol_annual_pct, false)}
               sub="su rendimenti TWR" />
            <K label="IRR (money-w.)" value={pct(twr.metrics?.irr_annual_pct)}
               tone={(twr.metrics?.irr_annual_pct ?? 0) >= 0 ? 'up' : 'dn'} sub="rendimento del TUO capitale" />
            {/* fix 13/07: metriche istituzionali da /portfolio/metrics/advanced —
                calcolate sulla STESSA serie TWR ufficiale, benchmark SPY in EUR per data */}
            {adv && !adv.error && (
              <>
                <K label="Sortino" value={adv.sortino != null ? adv.sortino.toFixed(2) : 'n.d.'}
                   tone="text-cyan" sub="downside risk (serie TWR)" />
                <K label="Calmar" value={adv.calmar != null ? adv.calmar.toFixed(2) : 'n.d.'}
                   tone="text-cyan" sub="CAGR / max DD" />
                <K label={'Beta vs ' + (adv.benchmark_ticker || 'SPY')}
                   value={adv.benchmark?.beta != null ? adv.benchmark.beta.toFixed(2) : 'n.d.'}
                   sub={adv.benchmark_alignment ? 'benchmark in EUR, per data' : 'benchmark n.d.'} />
                <K label="Alpha ann." value={pct(adv.benchmark?.alpha_annual_pct)}
                   tone={(adv.benchmark?.alpha_annual_pct ?? 0) >= 0 ? 'up' : 'dn'}
                   sub={'vs ' + (adv.benchmark_ticker || 'SPY') + ' (EUR)'} />
              </>
            )}
          </div>

          {/* indice TWR base 100 + benchmark ufficiale rebased (overlay tratteggiato, run carry in ambra) */}
          <div style={{ padding: '4px 8px 0', borderTop: '1px solid rgba(26,36,64,.6)' }}>
            <TerminalChart bars={twrBars} mode="area" height={300} valueLegend overlays={twrOverlays} />
          </div>
          {/* F2 v3: banda-fonte del dato, run-length dai regimes del payload */}
          {twr.regimes && twr.regimes.length > 1 && (
            <RegimeRibbon dates={twr.dates || []} regimes={twr.regimes} officialSince={twr.regime_summary?.official_since} />
          )}
          {/* F2 v3: UNDERWATER — il dolore reso visibile, derivato dall'indice */}
          {uwBars.length > 1 && (
            <div style={{ padding: '2px 8px 2px', borderTop: '1px solid rgba(26,36,64,.6)', marginTop: 4 }}>
              <div style={{ fontSize: 9, letterSpacing: '.18em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase', padding: '4px 4px 2px' }}>
                UNDERWATER // DRAWDOWN CONTINUO DAL PICCO
                <span style={{ marginLeft: 10 }}>MAX <span className="dn">{pct(twr.metrics?.max_drawdown_pct, false)}</span></span>
                <span style={{ marginLeft: 10 }}>CORRENTE <span className="dn">{pct(twr.metrics?.current_drawdown_pct, false)}</span></span>
              </div>
              <TerminalChart bars={uwBars} mode="baseline" height={110} showSma={false} valueLegend />
            </div>
          )}
          <div style={{ textAlign: 'center', fontSize: 9, fontWeight: 600, color: '#73829F', letterSpacing: '.1em', textTransform: 'uppercase', padding: '3px 0 4px' }}>
            ITD completa · scroll = zoom · drag = pan · banda = fonte del dato (<span style={{ color: '#B97A00' }}>ricostruita</span> → <span style={{ color: '#21E0A0' }}>ufficiale da snapshot NAV</span>) · underwater derivato dall'indice TWR
          </div>

          {/* riga as-of + riconciliazione NAV live vs ultimo snapshot (breach dal payload, come F1) */}
          <div className="num" style={{ padding: '5px 12px', borderTop: '1px solid #1A2440', fontSize: 9, fontWeight: 600, color: '#73829F', display: 'flex', flexWrap: 'wrap', gap: '2px 16px' }}>
            <span>as-of: <span style={{ color: '#29D3F2' }}>{twr.as_of?.computed_at?.replace('T', ' ') ?? '-'}</span></span>
            {twr.reconciliation && (
              <span>
                NAV live <span style={{ color: '#ECF1FA' }}>{eur(twr.reconciliation.nav_live_eur)}</span>
                {' '}vs snapshot {twr.reconciliation.last_snapshot_date}{' '}
                <span style={{ color: '#ECF1FA' }}>{eur(twr.reconciliation.last_snapshot_nav_eur)}</span>
                {twr.reconciliation.delta_pct != null && (
                  <span style={{ color: (twr.reconciliation.breach ?? Math.abs(twr.reconciliation.delta_pct) > 1) ? '#FFA51E' : '#21E0A0' }}
                        title={twr.reconciliation.tolerance_pct != null ? 'allarme dal backend: tolleranza ' + twr.reconciliation.tolerance_pct + '%' : 'soglia locale 1% (backend pre-riavvio senza campo breach)'}>
                    {' '}(Δ {pct(twr.reconciliation.delta_pct)}){twr.reconciliation.breach ? ' ⚠ BREACH' : ''}
                  </span>
                )}
              </span>
            )}
            {twr.metrics?.irr_basis && <span>IRR: {twr.metrics.irr_basis}</span>}
          </div>
          {twr.notes && twr.notes.length > 0 && (
            <div style={{ padding: '4px 12px', borderTop: '1px solid rgba(26,36,64,.4)', fontSize: 9, color: '#8D9FC4' }}>
              {twr.notes.map((n, i) => <div key={i}>• {n}</div>)}
            </div>
          )}
        </div>
      )}

      {twr?.error && (
        <div className="border border-amber/40 bg-amber/5 px-3 py-2 font-mono text-2xs text-amber flex items-center gap-2">
          <AlertTriangle size={12} /> TWR engine: {twr.error} — mostra solo metriche su serie grezza.
        </div>
      )}

      {/* ══ F2 v3: MONTHLY RETURNS — heatmap book + benchmark + Δ ══ */}
      {monthly && monthly.groups.length > 0 && (
        <div className="p3">
          <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
          <div className="p3h am">MONTHLY RETURNS // HEATMAP
            <span className="n">· TWR MENSILE (GIPS) DALL'INDICE</span>
            <span className="side num">
              {benchOnTwr
                ? (benchIsOfficial
                    ? 'SPY = SERIE UFFICIALE BACKEND — TOTAL-RETURN, DIVIDENDI INCLUSI [src: ' + (bench?.src || 'benchmark_series') + '] · '
                    : 'SPY = SPY×EURUSD CLIENT-SIDE [src: yfinance] — PROVVISORIO FINO AL RIAVVIO BACKEND (VOCE (38)) · ')
                : (benchErr ? 'BENCHMARK N.D. (' + benchErr.slice(0, 44) + ') · ' : '')}
              MESE IN CORSO = MTD DICHIARATO
            </span>
          </div>
          <MonthlyHeatmap groups={monthly.groups} lastYm={monthly.lastYm} />
        </div>
      )}

      {/* ══ F2 v3.1: ROLLING 30G — vol · Sharpe · tracking error ══ */}
      {rolling && (
        <div className="p3">
          <div className="p3h">ROLLING 30G <span className="n">· SULLA SERIE TWR (RENDIMENTI GIORNALIERI DALL'INDICE)</span>
            <span className="side num">SPARK = ULTIMI 90G · RF {((twr?.metrics?.risk_free_used ?? 0) * 100).toFixed(2)}% · {benchIsOfficial ? 'TE VS SPY-EUR TR UFF. · GIORNI NATIVI (CARRY ESCLUSI)' : 'TE VS SPY-EUR CALC · PROVVISORIO'}</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '0 24px', padding: '8px 12px' }}>
            <div className="num" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span className="tlab" style={{ width: 110 }}>VOL 30G ANN.</span>
              <Spark pts={rolling.vol} color="#95A1BA" />
              <span style={{ fontSize: 12, fontWeight: 600, marginLeft: 'auto' }}>{rolling.volNow.toFixed(1)}%</span>
            </div>
            <div className="num" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span className="tlab" style={{ width: 110 }}>SHARPE 30G</span>
              <Spark pts={rolling.sharpe} color="#21E0A0" />
              <span className={rolling.sharpeNow >= 0 ? 'up' : 'dn'} style={{ fontSize: 12, fontWeight: 600, marginLeft: 'auto' }}>{rolling.sharpeNow.toFixed(2)}</span>
            </div>
            <div className="num" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span className="tlab" style={{ width: 110 }}>TRACK.ERR 30G</span>
              {rolling.te ? (
                <>
                  <Spark pts={rolling.te} color="#9B7BFF" />
                  <span style={{ fontSize: 12, fontWeight: 600, marginLeft: 'auto', color: '#9B7BFF' }}>{rolling.teNow!.toFixed(1)}%</span>
                </>
              ) : (
                <span className="tlab" style={{ fontWeight: 600, color: '#73829F' }}>n.d. — serve la serie benchmark ufficiale (voce (38))</span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ══ F2 v4: ATTRIBUTION (fase 1b backend, voce (36)) — chi ha fatto il rendimento ══ */}
      <AttributionPanel />

      </>}

      {view === 'risk' && <>

      {/* ══ NAV vs COST BASIS (B-UI13: motore lightweight-charts, via l'SVG distorto) ══ */}
      {navBars.length > 1 && (
        <div className="p3 cy">
          <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
          <div className="p3h">NAV VS COST BASIS // {rl}
            <span className="n">· <span style={{ color: '#29D3F2' }}>NAV</span> / <span style={{ color: '#FFA51E' }}>COST BASIS (capitale investito)</span></span>
            <span className="side num">{slice.dates[0]} → {slice.dates[slice.dates.length - 1]} · CROSSHAIR NATIVO</span>
          </div>
          <div style={{ padding: '4px 8px 6px' }}>
            <TerminalChart bars={navBars} mode="area" height={280} showSma={false} valueLegend overlays={cbOverlays} />
          </div>
        </div>
      )}

      {/* ══ UNREALIZED P/L (B-UI13: BaselineSeries verde/rosso attorno allo zero) ══ */}
      {pnlBars.length > 1 && (
        <div className="p3">
          <div className="p3h">UNREALIZED P/L // {rl}
            <span className="side num">
              da <span style={{ color: '#ECF1FA' }}>{eur(slice.pnlStart)}</span> → <span className={slice.pnlEnd >= 0 ? 'up' : 'dn'}>{eur(slice.pnlEnd, true)}</span>
            </span>
          </div>
          <div style={{ padding: '4px 8px 6px' }}>
            <TerminalChart bars={pnlBars} mode="baseline" height={190} showSma={false} valueLegend />
          </div>
        </div>
      )}

      {/* ══ DRAWDOWNS ══ */}
      {drawdowns && !drawdowns.error && (
        <div className="grid grid-cols-3 gap-2">
          <div className="p3 col-span-2">
            <div className="p3h">TOP-5 DRAWDOWNS STORICI (ITD)
              <span className="n">· SERIE GREZZA (NON FLOW-ADJUSTED)</span>
            </div>
            {drawdowns.top_5_drawdowns.length === 0 ? (
              <div className="text-muted font-mono text-2xs text-center" style={{ padding: '16px 12px' }}>
                Nessun episodio di drawdown completato.
                {drawdowns.current_drawdown ? ' In DD ora — v. pannello a destra.' : ''}
              </div>
            ) : (
              <table className="num">
                <thead>
                  <tr>
                    <th>Peak</th><th>Trough</th><th>Recovery</th>
                    <th>Depth</th><th>Days to Trough</th><th>Recovery Days</th>
                  </tr>
                </thead>
                <tbody>
                  {drawdowns.top_5_drawdowns.map((dd, i) => (
                    <tr key={i}>
                      <td className="text-muted" style={{ textAlign: 'left' }}>{dd.peak_date}</td>
                      <td className="text-muted">{dd.trough_date}</td>
                      <td className="text-muted">{dd.recovery_date || '-'}</td>
                      <td className="dn">{dd.depth_pct.toFixed(2)}%</td>
                      <td>{dd.duration_to_trough_days}g</td>
                      <td className="up">{dd.recovery_days != null ? dd.recovery_days + 'g' : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="p3">
            <div className="p3h am">CURRENT DRAWDOWN <span className="n">· SERIE GREZZA</span></div>
            <div className="num" style={{ padding: '10px 12px', fontSize: 10 }}>
              {drawdowns.current_drawdown ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  <div className="dn" style={{ fontSize: 22, fontWeight: 300 }}>
                    {drawdowns.current_drawdown.depth_from_peak_pct.toFixed(2)}%
                  </div>
                  <div style={{ fontSize: 9, fontWeight: 600, color: '#73829F', letterSpacing: '.16em', textTransform: 'uppercase' }}>dal picco</div>
                  <div className="text-muted" style={{ marginTop: 6 }}>
                    Picco: <span style={{ color: '#ECF1FA' }}>{drawdowns.current_drawdown.peak_date}</span>
                  </div>
                  <div className="text-muted">
                    Giorni dal picco: <span style={{ color: '#FFA51E' }}>{drawdowns.current_drawdown.days_since_peak}</span>
                  </div>
                  <div className="text-muted">
                    Profondita' max finora: <span className="dn">{drawdowns.current_drawdown.max_depth_pct.toFixed(2)}%</span>
                  </div>
                </div>
              ) : (
                <div className="up" style={{ padding: '10px 0', display: 'flex', alignItems: 'center', gap: 8 }}>
                  Non in drawdown. NAV al picco.
                </div>
              )}
              <div style={{ marginTop: 10, paddingTop: 6, borderTop: '1px solid rgba(26,36,64,.6)', fontSize: 9, fontWeight: 600, color: '#73829F' }}>
                Pain Index: <span style={{ color: '#ECF1FA' }}>{drawdowns.pain_index.toFixed(4)}</span>
              </div>
              <div style={{ fontSize: 9, fontWeight: 600, color: '#73829F' }}>
                Avg DD: <span style={{ color: '#ECF1FA' }}>{drawdowns.avg_drawdown_pct.toFixed(2)}%</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ══ LIQUIDITY ══ */}
      {liquidity && !liquidity.error && (
        <div className="p3">
          <div className="p3h">LIQUIDITY SCORES
            <span className="n">· GIORNI PER LIQUIDARE AL {(liquidity.assumption_pct_of_volume * 100).toFixed(0)}% DEL VOLUME MEDIO</span>
            <span className="side num">
              <span className="up">●{liquidity.n_green}</span>
              <span className="text-amber" style={{ marginLeft: 8 }}>●{liquidity.n_yellow}</span>
              <span className="dn" style={{ marginLeft: 8 }}>●{liquidity.n_red}</span>
            </span>
          </div>
          <table className="num">
            <thead>
              <tr>
                <th>Ticker</th><th>Position EUR</th><th>Avg Daily Vol EUR</th>
                <th>Days to Liquidate</th><th style={{ textAlign: 'center' }}>Score</th>
              </tr>
            </thead>
            <tbody>
              {liquidity.items.map(it => {
                const scoreCls = it.score === 'green' ? 'up'
                  : it.score === 'yellow' ? 'text-amber'
                  : it.score === 'red' ? 'dn'
                  : 'text-muted';
                return (
                  <tr key={it.ticker}>
                    <td style={{ color: '#ECF1FA' }}>{it.ticker}</td>
                    <td className="text-muted">{eur(it.position_eur)}</td>
                    <td className="text-muted">{it.avg_daily_volume_eur ? eur(it.avg_daily_volume_eur) : 'n.d.'}</td>
                    <td style={{ color: '#ECF1FA' }}>{it.days_to_liquidate != null ? it.days_to_liquidate.toFixed(2) + 'g' : 'n.d.'}</td>
                    <td className={scoreCls} style={{ textAlign: 'center', textTransform: 'uppercase', letterSpacing: '.12em' }}>● {it.score}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div style={{ padding: '5px 12px', borderTop: '1px solid #1A2440', fontSize: 9, fontWeight: 600, color: '#73829F' }}>{liquidity.note}</div>
        </div>
      )}

      {/* ══ CONCENTRATION ══ */}
      {concentration && !concentration.error && (
        <div className="grid grid-cols-3 gap-2">
          {(['by_ticker', 'by_region', 'by_currency'] as const).map(dim => {
            const d = concentration[dim];
            const title = dim === 'by_ticker' ? 'BY TICKER' :
                          dim === 'by_region' ? 'BY REGION' : 'BY CURRENCY';
            const cls = d.classification === 'diversified' ? 'up'
              : d.classification === 'moderate' ? 'text-amber' : 'dn';
            return (
              <div key={dim} className="p3">
                <div className="p3h">HHI {title}</div>
                <div className="num" style={{ padding: '8px 12px', fontSize: 10 }}>
                  <div style={{ fontSize: 22, fontWeight: 300, color: '#ECF1FA' }}>{Math.round(d.hhi)}</div>
                  <div className={cls} style={{ fontSize: 8, letterSpacing: '.18em', textTransform: 'uppercase' }}>
                    {d.classification}
                  </div>
                  {dim === 'by_ticker' && 'effective_n' in d && (
                    <div className="text-muted" style={{ marginTop: 6, fontSize: 9 }}>
                      Effective N: <span style={{ color: '#29D3F2' }}>{(d as any).effective_n.toFixed(2)}</span>
                    </div>
                  )}
                  {dim === 'by_ticker' && 'top_5_pct' in d && (
                    <div className="text-muted" style={{ fontSize: 9 }}>
                      Top-5: <span style={{ color: '#FFA51E' }}>{(d as any).top_5_pct.toFixed(1)}%</span>
                    </div>
                  )}
                  {dim !== 'by_ticker' && (
                    <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 2, fontSize: 9 }}>
                      {Object.entries((d as any).weights_pct).map(([k, v]) => (
                        <div key={k} style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span className="text-muted">{k}</span>
                          <span style={{ color: '#ECF1FA' }}>{(v as number).toFixed(1)}%</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ══ VAR CONTRIBUTION ══ */}
      {varContrib && !varContrib.error && (
        <div className="p3">
          <div className="p3h">COMPONENT VAR (JORION 2006)
            <span className="n">· DAILY {(varContrib.confidence_level * 100).toFixed(0)}%</span>
            <span className="side num">
              Portfolio VaR: <span className="dn">{varContrib.portfolio_var_pct_daily.toFixed(2)}% = {eur(varContrib.portfolio_var_eur_daily)}</span>
              {' '}· Vol ann: <span style={{ color: '#29D3F2' }}>{varContrib.portfolio_vol_annual_pct.toFixed(2)}%</span>
            </span>
          </div>
          <table className="num">
            <thead>
              <tr>
                <th>Ticker</th><th>Weight</th><th>Component VaR €</th>
                <th>% of Total VaR</th><th>Marginal VaR</th><th style={{ textAlign: 'left' }}>Bar</th>
              </tr>
            </thead>
            <tbody>
              {varContrib.items.map(it => (
                <tr key={it.ticker}>
                  <td style={{ color: '#ECF1FA' }}>{it.ticker}</td>
                  <td className="text-muted">{it.weight_pct.toFixed(2)}%</td>
                  <td className="dn">{eur(it.component_var_eur)}</td>
                  <td className="text-amber">{it.contribution_pct_of_total_var.toFixed(1)}%</td>
                  <td className="text-muted">{it.marginal_var_pct_per_1pct_weight.toFixed(4)}</td>
                  <td style={{ width: 180 }}>
                    <div style={{ height: 5, background: 'rgba(26,36,64,.85)', border: '1px solid #1A2440', overflow: 'hidden' }}>
                      <div style={{ height: '100%', background: '#FF3D60', opacity: 0.7, width: Math.min(100, Math.abs(it.contribution_pct_of_total_var)) + '%' }} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ padding: '5px 12px', borderTop: '1px solid #1A2440', fontSize: 9, fontWeight: 600, color: '#73829F' }}>{varContrib.methodology}</div>
        </div>
      )}

      </>}

      {navHist?.error && (
        <div className="border border-amber/40 bg-amber/5 px-3 py-2 font-mono text-2xs text-amber flex items-start gap-2">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <div>
            <b>Storico NAV non disponibile:</b> {navHist.error}<br />
            <span className="text-muted">
              Registra i trade dalla pagina Cassa/Trade, o importali con <code>python scripts/importa_trade_csv.py miei_trade.csv --apply</code>, poi refresh.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
