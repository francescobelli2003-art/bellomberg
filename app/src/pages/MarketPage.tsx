import { useEffect, useRef, useState } from 'react';
import { externalWebUrl } from '../../electron/security';
import { Bellomberg, MktSearchHit, MktQuote, MktNewsItem, MktFinancials, MktHolders, FinBlock, MktOverview, MktOverviewRow } from '@/lib/api';
import TvChartPanel from '@/components/TvChartPanel';
import { Search, Cpu, Heart, Activity, RefreshCw, AlertOctagon } from 'lucide-react';
import { isInPulse, togglePulse } from '@/lib/pulse';
import './dashboard-command.css';

/** UI v3 T3 - SECURITY TERMINAL: cerca e analizza QUALSIASI titolo globale (non solo il book).
 *  F15 v3 23/07: vestito OBSIDIAN COMMAND (.obsx) + grafico TV-grade condiviso (TvChartPanel). */

const QUICK = ['NVDA', 'AAPL', 'MSFT', 'TSLA', 'AMZN', 'META', 'GOOGL', 'SPY', 'QQQ', 'MSTR'];

const cn = (v?: number | null, dec = 2) =>
  v == null || !isFinite(v) ? '-' : Intl.NumberFormat('it-IT', { notation: 'compact', maximumFractionDigits: dec }).format(v);
const fx = (v?: number | null, dec = 2) =>
  v == null || !isFinite(v) ? '-' : v.toLocaleString('it-IT', { minimumFractionDigits: dec, maximumFractionDigits: dec });

function timeAgo(p?: string | number) {
  if (p == null) return '';
  const t = typeof p === 'number' ? (p > 2e10 ? p : p * 1000) : Date.parse(p);
  if (!isFinite(t)) return '';
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (m < 60) return m + 'm fa';
  const h = Math.round(m / 60);
  if (h < 48) return h + 'h fa';
  return Math.round(h / 24) + 'g fa';
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ padding: '7px 12px', borderRight: '1px solid rgba(26,36,64,.6)', borderBottom: '1px solid rgba(26,36,64,.6)' }}>
      <div style={{ fontSize: 9, letterSpacing: '.2em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase' }}>{label}</div>
      <div className={'num ' + (tone || '')} style={{ fontSize: 12, fontWeight: 500, marginTop: 2, color: tone ? undefined : '#ECF1FA' }}>{value}</div>
    </div>
  );
}

const gv = (r: Record<string, any>, ...keys: string[]) => { for (const k of keys) if (r[k] != null) return r[k]; return null; };
const numv = (v: any) => v == null ? '-' : Intl.NumberFormat('it-IT', { notation: 'compact', maximumFractionDigits: 1 }).format(Number(v));
const pctv = (v: any) => v == null ? '-' : (Number(v) <= 1 ? (Number(v) * 100).toFixed(2) : Number(v).toFixed(2)) + '%';

function FinTable({ b }: { b: FinBlock }) {
  const labels = Object.keys(b.rows || {});
  if (!labels.length) return <div className="text-faint text-2xs font-mono py-6 text-center">dati non disponibili</div>;
  return (
    <div className="overflow-x-auto">
      <table className="table-bbg">
        <thead><tr><th>Voce</th>{b.years.map((y, i) => <th key={i} className="text-right">{y}</th>)}</tr></thead>
        <tbody>
          {labels.map(l => (
            <tr key={l}>
              <td className="text-gold">{l}</td>
              {b.rows[l].map((v, i) => (
                <td key={i} className={'text-right tabular-nums ' + (v != null && v < 0 ? 'text-crimson' : 'text-text')}>
                  {v == null ? '-' : l.includes('EPS') ? v.toFixed(2) : numv(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const COUNTRY_LABELS: Record<string, string> = { US: 'USA', IT: 'Italia', DE: 'Germania', FR: 'Francia', UK: 'Regno Unito', JP: 'Giappone', CN: 'Cina / HK', IN: 'India', BR: 'Brasile' };

function OvwSection({ title, rows, onSelect, empty }: { title: string; rows: MktOverviewRow[]; onSelect: (t: string) => void; empty: string }) {
  return (
    <div className="p3">
      <div className="p3h">{title}</div>
      <div className="overflow-x-auto">
        <table className="num">
          <tbody>
            {rows.length === 0 ? (
              <tr><td className="text-faint text-3xs font-mono py-3 px-3">{empty}</td></tr>
            ) : rows.map(r => {
              const c = r.change_pct;
              return (
                <tr key={r.ticker} onClick={() => onSelect(r.ticker)} className="cursor-pointer">
                  <td style={{ color: '#8D9FC4' }}>{r.name}</td>
                  <td style={{ color: '#29D3F2' }}>{r.price == null ? '-' : r.price.toLocaleString('it-IT', { maximumFractionDigits: 2 })}</td>
                  <td className={c == null ? 'text-muted' : c >= 0 ? 'up' : 'dn'} style={{ fontWeight: 600 }}>
                    {c == null ? '-' : (c >= 0 ? '+' : '') + c.toFixed(2) + '%'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MarketOverview({ onSelect }: { onSelect: (t: string) => void }) {
  const [country, setCountry] = useState('US');
  const [data, setData] = useState<MktOverview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let m = true; setLoading(true); setErr(null);
    Bellomberg.mktOverview(country)
      .then(d => { if (m) setData(d); })
      .catch(e => { if (m) { setData(null); setErr(e?.response?.data?.detail || e?.message || String(e)); } })
      .finally(() => { if (m) setLoading(false); });
    return () => { m = false; };
  }, [country, retry]);
  const ccs = data?.countries || ['US', 'IT', 'DE', 'FR', 'UK', 'JP', 'CN', 'IN', 'BR'];
  // Regola 14/07: il buco si dichiara — mai "caricamento..." perenne su errore
  const empty = loading ? 'caricamento...' : err ? 'n.d. — overview in errore (v. sopra)' : 'nessun dato dal backend';
  return (
    <>
      <div className="p3" style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: '5px 12px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 9, letterSpacing: '.2em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase' }}>Azioni per paese</span>
        <span className="tfg">
          {ccs.map(cc => (
            <button key={cc} onClick={() => setCountry(cc)} className={'tb' + (country === cc ? ' on' : '')}>
              {COUNTRY_LABELS[cc] || cc}
            </button>
          ))}
        </span>
        {loading && <Cpu size={11} className="animate-pulse text-faint ml-1" />}
      </div>
      {err && !loading && (
        <div className="p3 border-crimson flex items-center gap-3 px-3 py-2 font-mono text-2xs text-crimson">
          <AlertOctagon size={12} />
          <span>OVERVIEW NON DISPONIBILE — {err}</span>
          <button onClick={() => setRetry(r => r + 1)} className="btn btn-cyan ml-auto">
            <RefreshCw size={11} /> RETRY
          </button>
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
        <OvwSection title="INDICI PRINCIPALI" rows={data?.indici || []} onSelect={onSelect} empty={empty} />
        <OvwSection title={'AZIONI // ' + (COUNTRY_LABELS[country] || country)} rows={data?.azioni || []} onSelect={onSelect} empty={empty} />
        <OvwSection title="COMMODITIES" rows={data?.commodities || []} onSelect={onSelect} empty={empty} />
        <OvwSection title="VALUTE" rows={data?.valute || []} onSelect={onSelect} empty={empty} />
        <OvwSection title="OBBLIGAZIONI // TASSI" rows={data?.obbligazioni || []} onSelect={onSelect} empty={empty} />
        <OvwSection title="FUTURES" rows={data?.futures || []} onSelect={onSelect} empty={empty} />
      </div>
    </>
  );
}

export default function MarketPage() {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<MktSearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [tk, setTk] = useState('');
  const [quote, setQuote] = useState<MktQuote | null>(null);
  const [quoteErr, setQuoteErr] = useState<string | null>(null);
  const [news, setNews] = useState<MktNewsItem[] | null>(null);
  const [newsErr, setNewsErr] = useState<string | null>(null);
  const [showSummary, setShowSummary] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const [fav, setFav] = useState<boolean | null>(null);
  const [fin, setFin] = useState<MktFinancials | null>(null);
  const [holders, setHolders] = useState<MktHolders | null>(null);
  const [holdersErr, setHoldersErr] = useState<string | null>(null);
  const [finTab, setFinTab] = useState<'income' | 'balance' | 'cashflow'>('income');

  // ricerca con debounce
  useEffect(() => {
    if (!q.trim()) { setHits([]); setOpen(false); return; }
    let alive = true;
    setSearching(true);
    const t = setTimeout(() => {
      Bellomberg.mktSearch(q.trim())
        .then(r => { if (alive) { setHits(r.results || []); setOpen(true); } })
        .catch(() => { if (alive) setHits([]); })
        .finally(() => { if (alive) setSearching(false); });
    }, 300);
    return () => { alive = false; clearTimeout(t); };
  }, [q]);

  const select = (sym: string) => {
    setTk(sym.toUpperCase());
    setQ(''); setHits([]); setOpen(false);
  };

  // handoff dal Command Palette (Ctrl+K): ticker diretto o query di ricerca
  useEffect(() => {
    const t = sessionStorage.getItem('bb:mktTicker');
    const qq = sessionStorage.getItem('bb:mktQuery');
    if (t) { sessionStorage.removeItem('bb:mktTicker'); select(t); }
    else if (qq) { sessionStorage.removeItem('bb:mktQuery'); setQ(qq); }
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // stato preferito (T4)
  useEffect(() => {
    if (!tk) return;
    let m = true;
    setFav(null);
    Bellomberg.favorites()
      .then(r => { if (m) setFav((r.favorites || []).some(f => f.ticker === tk)); })
      .catch(() => { if (m) setFav(false); });
    return () => { m = false; };
  }, [tk]);

  const toggleFav = async () => {
    if (fav == null || !tk) return;
    const cur = fav;
    setFav(!cur);
    try {
      if (cur) await Bellomberg.favDel(tk);
      else await Bellomberg.favAdd({ ticker: tk, name: quote?.name || '', sector: quote?.sector || '', industry: quote?.industry || '' });
    } catch { setFav(cur); }
  };

  // MACRO PULSE personalizzabile (richiesta PM 23/07): il titolo in vista si
  // aggiunge/toglie dal box MACRO PULSE di F1 (localStorage, vedi lib/pulse)
  const [inPulse, setInPulse] = useState(false);
  useEffect(() => { setInPulse(tk ? isInPulse(tk) : false); }, [tk]);
  const togglePulsePick = () => {
    if (!tk) return;
    setInPulse(togglePulse(tk, quote?.name || tk));
  };

  // quote + news al cambio titolo
  useEffect(() => {
    if (!tk) return;
    let m = true;
    setQuote(null); setNews(null); setShowSummary(false); setQuoteErr(null); setNewsErr(null);
    // Degrado DICHIARATO (regola 14/07): quote scheletro + errore reso, mai stats mute
    Bellomberg.mktQuote(tk).then(r => { if (m) setQuote(r); }).catch(e => { if (m) { setQuote({ ticker: tk, name: tk }); setQuoteErr(e?.response?.data?.detail || e?.message || String(e)); } });
    Bellomberg.mktNews(tk).then(r => { if (m) setNews(r.items || []); }).catch(e => { if (m) { setNews([]); setNewsErr(e?.response?.data?.detail || e?.message || String(e)); } });
    return () => { m = false; };
  }, [tk]);

  // bilanci + ownership al cambio titolo (T4-2)
  useEffect(() => {
    if (!tk) return;
    let m = true;
    setFin(null); setHolders(null); setFinTab('income'); setHoldersErr(null);
    Bellomberg.mktFinancials(tk).then(r => { if (m) setFin(r); }).catch(() => { if (m) setFin({ ticker: tk, error: 'bilanci non disponibili' }); });
    Bellomberg.mktHolders(tk).then(r => { if (m) setHolders(r); }).catch(e => { if (m) { setHolders({ ticker: tk, major: [], institutional: [] }); setHoldersErr(e?.response?.data?.detail || e?.message || String(e)); } });
    return () => { m = false; };
  }, [tk]);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const px = quote?.price, pc = quote?.prev_close;
  const chg = px != null && pc ? ((px / pc) - 1) * 100 : null;
  const up = (chg ?? 0) >= 0;
  const range = quote?.low_52w != null && quote?.high_52w != null && px != null && quote.high_52w > quote.low_52w
    ? Math.min(100, Math.max(0, ((px - quote.low_52w) / (quote.high_52w - quote.low_52w)) * 100)) : null;
  const btn = (on: boolean) => 'tb' + (on ? ' on' : '');

  return (
    <div className="obsx animate-fadeIn">

      {/* OMNISEARCH titoli globali */}
      <div className="p3 hero" style={{ position: 'relative', zIndex: 30 }}>
        <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
        <div className="flex items-center gap-3 px-3 py-2.5">
          <span className="font-mono" style={{ fontSize: 10, letterSpacing: '.24em', color: '#FFA51E', textTransform: 'uppercase' }}>GLOBAL MARKETS // OMNISEARCH</span>
          <div className="flex-1 relative">
            <div className="flex items-center gap-2 bg-bg border border-border focus-within:border-amber px-3 py-1.5 transition-colors">
              <Search size={12} className="text-faint" />
              <input
                ref={inputRef}
                value={q}
                onChange={e => setQ(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && hits[0]) select(hits[0].symbol);
                  if (e.key === 'Escape') { setOpen(false); setQ(''); }
                }}
                placeholder="CERCA QUALSIASI TITOLO GLOBALE - nome o ticker (es. Ferrari, RACE.MI, 7203.T, SAP.DE)"
                className="flex-1 bg-transparent outline-none font-mono text-xs text-text placeholder:text-faint tracking-wider"
              />
              {searching && <Cpu size={11} className="text-amber animate-pulse" />}
            </div>
            {open && hits.length > 0 && (
              <div className="absolute z-30 inset-x-0 top-full mt-1 bg-panel border border-amber-deep shadow-depth max-h-72 overflow-y-auto">
                {hits.map(h => (
                  <button key={h.symbol + h.exchange} onClick={() => select(h.symbol)}
                          className="w-full flex items-center gap-3 px-3 py-1.5 font-mono text-2xs text-left hover:bg-panel-hi transition-colors">
                    <span className="text-gold font-bold w-24 truncate">{h.symbol}</span>
                    <span className="text-text-dim flex-1 truncate">{h.name}</span>
                    <span className="text-faint w-24 truncate text-right">{h.exchange}</span>
                    <span className="text-cyan w-14 text-right uppercase">{h.type}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 px-3 pb-2 font-mono flex-wrap">
          <span style={{ fontSize: 9, letterSpacing: '.2em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase', marginRight: 4 }}>Quick</span>
          <span className="tfg">
            {QUICK.map(s => (
              <button key={s} onClick={() => select(s)} className={'tb' + (tk === s ? ' on' : '')}>{s}</button>
            ))}
          </span>
        </div>
      </div>

      {!tk && <MarketOverview onSelect={select} />}

      {tk && (
        <>
          {/* INTESTAZIONE TITOLO */}
          <div className="p3 hero">
            <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
            <div className="flex items-stretch justify-between flex-wrap">
              <div className="p-3.5">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="font-mono font-bold text-2xl text-gold tracking-wide">{tk}</span>
                  <span className="text-text text-sm font-mono">{quote?.name || '...'}</span>
                  <button onClick={toggleFav} disabled={fav == null}
                          title={fav ? 'Rimuovi dai preferiti' : 'Aggiungi ai preferiti (il Consigliere li seguira)'}
                          className={'flex items-center gap-1.5 px-2 py-0.5 border font-mono text-3xs uppercase tracking-wider transition-colors disabled:opacity-40 ' + (fav ? 'border-crimson text-crimson bg-crimson/10' : 'border-border text-muted hover:text-crimson hover:border-crimson')}>
                    <Heart size={10} fill={fav ? 'currentColor' : 'none'} /> {fav ? 'PREFERITO' : 'PREFERITI'}
                  </button>
                  <button onClick={togglePulsePick}
                          title={inPulse ? 'Togli dal box MACRO PULSE della Dashboard' : 'Aggiungi al box MACRO PULSE della Dashboard (F1)'}
                          className={'flex items-center gap-1.5 px-2 py-0.5 border font-mono text-3xs uppercase tracking-wider transition-colors ' + (inPulse ? 'border-cyan text-cyan bg-cyan/10' : 'border-border text-muted hover:text-cyan hover:border-cyan')}>
                    <Activity size={10} /> {inPulse ? 'NEL PULSE ✓' : '+ MACRO PULSE'}
                  </button>
                  {quote?.recommendation && (
                    <span className="font-mono text-3xs uppercase tracking-wider px-1.5 py-0.5 border border-cyan-deep text-cyan">{quote.recommendation}</span>
                  )}
                </div>
                <div className="font-mono text-3xs text-muted uppercase tracking-wider mt-1">
                  {quote?.exchange || '-'} &middot; {quote?.sector || '-'}{quote?.industry ? ' / ' + quote.industry : ''}
                </div>
              </div>
              <div className="p-3.5 text-right">
                {quote === null ? (
                  <Cpu size={16} className="text-amber animate-pulse inline" />
                ) : quoteErr ? (
                  <div className="font-mono text-2xs text-crimson max-w-[280px]">
                    QUOTE NON DISPONIBILE — {quoteErr}
                  </div>
                ) : (
                  <>
                    <div className="font-mono tabular-nums leading-none" style={{ fontSize: 34, fontWeight: 300, color: '#ECF1FA', textShadow: '0 0 30px rgba(41,211,242,.12)' }}>
                      {fx(px)} <span style={{ fontSize: 14, color: '#8D9FC4', fontWeight: 300 }}>{quote.currency || ''}</span>
                    </div>
                    {chg != null && (
                      <div className={'font-mono text-sm tabular-nums mt-1 ' + (up ? 'text-emerald' : 'text-crimson')}>
                        {up ? '+' : ''}{chg.toFixed(2)}% <span className="text-3xs text-muted">vs chiusura prec.</span>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
            {/* STATS GRID */}
            <div className="grid grid-cols-5 border-t border-border">
              <Stat label="Mkt Cap" value={cn(quote?.market_cap)} />
              <Stat label="P/E" value={fx(quote?.pe)} />
              <Stat label="Fwd P/E" value={fx(quote?.fwd_pe)} />
              <Stat label="EPS" value={fx(quote?.eps)} />
              <Stat label="Beta" value={fx(quote?.beta)} />
              <Stat label="Div Yield" value={quote?.div_yield != null ? (quote.div_yield > 0.5 ? fx(quote.div_yield) : fx(quote.div_yield * 100)) + '%' : '-'} />
              <Stat label="Volume" value={cn(quote?.volume, 1)} />
              <Stat label="Avg Vol 3M" value={cn(quote?.avg_volume, 1)} />
              <Stat label="Short % Float" value={quote?.short_pct_float != null ? fx(quote.short_pct_float * 100, 1) + '%' : '-'} />
              <Stat label="Target medio" value={fx(quote?.target_mean)} tone={quote?.target_mean != null && px != null ? (quote.target_mean >= px ? 'text-emerald' : 'text-crimson') : undefined} />
              <Stat label="EV" value={cn(quote?.ev)} />
              <Stat label="EV/EBITDA" value={fx(quote?.ev_ebitda)} />
              <Stat label="EV/Sales" value={fx(quote?.ev_sales)} />
              <Stat label="PEG" value={fx(quote?.peg)} />
              <Stat label="P/B" value={fx(quote?.pb)} />
              <Stat label="FCF Yield" value={quote?.fcf != null && quote?.market_cap ? ((quote.fcf / quote.market_cap) * 100).toFixed(1) + '%' : '-'} tone="text-cyan" />
            </div>
            {/* 52W RANGE */}
            {range != null && (
              <div className="px-3.5 py-2.5 border-t border-border flex items-center gap-3 font-mono text-2xs">
                <span className="text-muted">52W</span>
                <span className="text-crimson tabular-nums">{fx(quote?.low_52w)}</span>
                <div className="flex-1 h-[5px] bg-bg-elev border border-border/40 relative">
                  <div className="absolute inset-y-0 left-0 bg-gradient-to-r from-crimson-deep via-amber-deep to-emerald-deep opacity-40 w-full" />
                  <div className="absolute top-1/2 -translate-y-1/2 w-1.5 h-3 bg-amber" style={{ left: 'calc(' + range.toFixed(1) + '% - 3px)', boxShadow: '0 0 6px rgba(255,165,30,0.8)' }} />
                </div>
                <span className="text-emerald tabular-nums">{fx(quote?.high_52w)}</span>
                <span className="text-faint">({range.toFixed(0)}%)</span>
              </div>
            )}
          </div>

          {/* GRAFICO TV-GRADE + NEWS */}
          <div className="grid grid-cols-12 gap-2">
            <div className="p3 cy col-span-8">
              <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
              <div className="p3h">PRICE ACTION // {tk}
                <span className="side">MOTORE TRADINGVIEW · SCROLL = ZOOM · DRAG = PAN</span>
              </div>
              <TvChartPanel ticker={tk} height={420} defaultRange={5} defaultInterval={4} />
            </div>

            <div className="p3 col-span-4 flex flex-col">
              <div className="p3h am">NEWS WIRE // {tk}
                <span className="side">{news?.length ?? '...'}</span>
              </div>
              <div className="flex-1 overflow-y-auto max-h-[430px] p-2 space-y-1.5">
                {news === null ? (
                  <div className="text-faint text-2xs font-mono py-8 text-center"><Cpu size={12} className="animate-pulse inline mr-2" />caricamento wire...</div>
                ) : newsErr ? (
                  <div className="text-crimson text-2xs font-mono py-8 text-center">WIRE NON DISPONIBILE — {newsErr}</div>
                ) : news.length === 0 ? (
                  <div className="text-faint text-2xs font-mono py-8 text-center">nessuna notizia recente</div>
                ) : (
                  news.map((n, i) => (
                    <a key={i} href={externalWebUrl(n.link || '') || undefined} target="_blank" rel="noreferrer"
                       className="block border-l-2 border-border hover:border-amber bg-bg-elev hover:bg-panel-hi px-2.5 py-1.5 transition-colors group">
                      <div className="text-2xs text-text-dim group-hover:text-text leading-snug">{n.title}</div>
                      <div className="font-mono text-3xs text-faint mt-1 flex items-center gap-2">
                        <span className="text-cyan">{n.publisher || '-'}</span>
                        <span>{timeAgo(n.published)}</span>
                      </div>
                    </a>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* FINANCIALS STORICI (T4-2) */}
          <div className="p3">
            <div className="p3h">FINANCIALS // STORICO ANNUALE
              <span className="side">
                <span className="tfg">
                  <button onClick={() => setFinTab('income')} className={btn(finTab === 'income')}>CONTO ECONOMICO</button>
                  <button onClick={() => setFinTab('balance')} className={btn(finTab === 'balance')}>STATO PATRIMONIALE</button>
                  <button onClick={() => setFinTab('cashflow')} className={btn(finTab === 'cashflow')}>CASH FLOW</button>
                </span>
              </span>
            </div>
            <div className="p-2">
              {fin === null
                ? <div className="text-faint text-2xs font-mono py-6 text-center"><Cpu size={11} className="animate-pulse inline mr-2" />caricamento bilanci...</div>
                : fin.error || !fin.statements
                  ? <div className="text-faint text-2xs font-mono py-6 text-center">{fin.error || 'dati non disponibili'}</div>
                  : <FinTable b={fin.statements[finTab]} />}
            </div>
          </div>

          {/* OWNERSHIP (T4-2) */}
          <div className="grid grid-cols-12 gap-2">
            <div className="p3 col-span-4">
              <div className="p3h">OWNERSHIP // STRUTTURA</div>
              <div className="p-3 space-y-1.5 font-mono text-2xs">
                {holders === null
                  ? <div className="text-faint py-4 text-center">caricamento...</div>
                  : holdersErr
                    ? <div className="text-crimson py-4 text-center">n.d. — ownership in errore: {holdersErr}</div>
                    : holders.major.length === 0
                      ? <div className="text-faint py-4 text-center">n/d</div>
                      : holders.major.map((m, i) => {
                        const OWN_LABELS: Record<string, string> = {
                          insidersPercentHeld: 'Insider %', institutionsPercentHeld: 'Istituzionali %',
                          institutionsFloatPercentHeld: 'Istituzionali % del float', institutionsCount: 'N. istituzioni',
                        };
                        const isCount = String(m.label).toLowerCase().includes('count');
                        return (
                          <div key={i} className="flex items-center justify-between border-b border-border/40 pb-1">
                            <span className="text-text-dim">{OWN_LABELS[String(m.label)] || m.label}</span>
                            <span className="text-cyan tabular-nums">
                              {typeof m.value === 'number' ? (isCount ? Math.round(m.value).toLocaleString('it-IT') : pctv(m.value)) : String(m.value ?? '-')}
                            </span>
                          </div>
                        );
                      })}
              </div>
            </div>
            <div className="p3 col-span-8">
              <div className="p3h">INSTITUTIONAL HOLDERS // TOP 10
                <span className="side">{holders?.institutional.length ?? '...'}</span>
              </div>
              <div className="overflow-x-auto max-h-[280px] overflow-y-auto">
                {holders && holders.institutional.length > 0 ? (
                  <table className="table-bbg">
                    <thead><tr><th>Holder</th><th className="text-right">Shares</th><th className="text-right">% Out</th><th className="text-right">Valore</th><th className="text-right">Data</th></tr></thead>
                    <tbody>
                      {holders.institutional.map((r, i) => (
                        <tr key={i}>
                          <td className="text-text-dim">{String(gv(r, 'holder') ?? '-')}</td>
                          <td className="text-right text-text tabular-nums">{numv(gv(r, 'shares'))}</td>
                          <td className="text-right text-cyan tabular-nums">{pctv(gv(r, 'pctheld', 'pct_held', 'pctout', 'pct_out'))}</td>
                          <td className="text-right text-text tabular-nums">{numv(gv(r, 'value'))}</td>
                          <td className="text-right text-muted">{String(gv(r, 'date_reported') ?? '-')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : <div className={'text-2xs font-mono py-6 text-center ' + (holdersErr ? 'text-crimson' : 'text-faint')}>{holders === null ? 'caricamento...' : holdersErr ? 'n.d. — ownership in errore' : 'n/d'}</div>}
              </div>
            </div>
          </div>

          {/* PROFILO */}
          {quote?.summary && (
            <div className="p3">
              <button onClick={() => setShowSummary(s => !s)} className="p3h w-full text-left cursor-pointer" style={{ background: 'transparent', border: 0, borderBottom: showSummary ? '1px solid #1A2440' : 0, fontFamily: 'inherit' }}>
                PROFILO SOCIETARIO
                <span className="side">{showSummary ? 'CHIUDI ▴' : 'APRI ▾'}</span>
              </button>
              {showSummary && <div className="p-3.5 text-xs text-text-dim leading-relaxed">{quote.summary}</div>}
            </div>
          )}
        </>
      )}
    </div>
  );
}
