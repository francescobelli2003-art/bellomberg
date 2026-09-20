import { useEffect, useRef, useState } from 'react';
import { externalWebUrl } from '../../electron/security';
import { Bellomberg, MktSearchHit, MktQuote, MktNewsItem, MktFinancials, MktHolders, FinBlock, MktOverview, MktOverviewRow } from '@/lib/api';
import TvChartPanel from '@/components/TvChartPanel';
import FilingDiffPanel from '@/components/FilingDiffPanel';
import { Search, Cpu, Heart, Activity, RefreshCw, AlertOctagon } from 'lucide-react';
import { isInPulse, togglePulse } from '@/lib/pulse';
import { useT } from '@/i18n/provider';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { leggiDetail } from '@/lib/quota';
import './dashboard-command.css';

/** UI v3 T3 - SECURITY TERMINAL: cerca e analizza QUALSIASI titolo globale (non solo il book).
 *  F15 v3 23/07: vestito OBSIDIAN COMMAND (.obsx) + grafico TV-grade condiviso (TvChartPanel). */

const QUICK = ['NVDA', 'AAPL', 'MSFT', 'TSLA', 'AMZN', 'META', 'GOOGL', 'SPY', 'QQQ', 'MSTR'];
const sourceError = (error: any) => leggiDetail(error?.response?.data?.detail) || leggiDetail(error?.message) || '—';

const cn = (v?: number | null, dec = 2) =>
  v == null || !isFinite(v) ? '-' : Intl.NumberFormat(localeDi(linguaCorrente()), { notation: 'compact', maximumFractionDigits: dec }).format(v);
const fx = (v?: number | null, dec = 2) =>
  v == null || !isFinite(v) ? '-' : v.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: dec, maximumFractionDigits: dec });

function timeAgo(p?: string | number) {
  if (p == null) return '';
  const t = typeof p === 'number' ? (p > 2e10 ? p : p * 1000) : Date.parse(p);
  if (!isFinite(t)) return '';
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (m < 60) return tr('ui.minutes_ago', { n: m });
  const h = Math.round(m / 60);
  if (h < 48) return tr('ui.hours_ago', { n: h });
  return tr('ui.days_ago', { n: Math.round(h / 24) });
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
const numv = (v: any) => v == null ? '-' : Intl.NumberFormat(localeDi(linguaCorrente()), { notation: 'compact', maximumFractionDigits: 1 }).format(Number(v));
const pctv = (v: any) => v == null ? '-' : fx(Number(v) <= 1 ? Number(v) * 100 : Number(v)) + '%';

function FinTable({ b }: { b: FinBlock }) {
  const tr = useT();
  const labels = Object.keys(b.rows || {});
  if (!labels.length) return <div className="text-faint text-2xs font-mono py-6 text-center">{tr('ui.no_data')}</div>;
  return (
    <div className="overflow-x-auto">
      <table className="table-bbg">
        <caption className="text-faint text-3xs text-left">{tr('ui.source_original')}</caption>
        <thead><tr><th>{tr('ui.row_label')}</th>{b.years.map((y, i) => <th key={i} className="text-right">{y}</th>)}</tr></thead>
        <tbody>
          {labels.map(l => (
            <tr key={l}>
              <td className="text-gold">{l}</td>
              {b.rows[l].map((v, i) => (
                <td key={i} className={'text-right tabular-nums ' + (v != null && v < 0 ? 'text-crimson' : 'text-text')}>
                  {v == null ? '-' : l.includes('EPS') ? fx(v) : numv(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const countryLabels = (): Record<string, string> => ({ US: 'USA', IT: tr('ui.country_it'), DE: tr('ui.country_de'), FR: tr('ui.country_fr'), UK: tr('ui.country_uk'), JP: tr('ui.country_jp'), CN: tr('ui.country_cn'), IN: 'India', BR: tr('ui.country_br') });

function OvwSection({ title, rows, onSelect, empty }: { title: string; rows: MktOverviewRow[]; onSelect: (t: string) => void; empty: string }) {
  useT();
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
                  <td style={{ color: '#29D3F2' }}>{r.price == null ? '-' : r.price.toLocaleString(localeDi(linguaCorrente()), { maximumFractionDigits: 2 })}</td>
                  <td className={c == null ? 'text-muted' : c >= 0 ? 'up' : 'dn'} style={{ fontWeight: 600 }}>
                    {c == null ? '-' : (c >= 0 ? '+' : '') + fx(c) + '%'}
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
  const tr = useT();
  const COUNTRY_LABELS = countryLabels();
  const [country, setCountry] = useState('US');
  const [data, setData] = useState<MktOverview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let m = true; setLoading(true); setErr(null);
    Bellomberg.mktOverview(country)
      .then(d => { if (m) setData(d); })
      .catch(e => { if (m) { setData(null); setErr(sourceError(e)); } })
      .finally(() => { if (m) setLoading(false); });
    return () => { m = false; };
  }, [country, retry]);
  const ccs = data?.countries || ['US', 'IT', 'DE', 'FR', 'UK', 'JP', 'CN', 'IN', 'BR'];
  // Regola 14/07: il buco si dichiara — mai "caricamento..." perenne su errore
  const empty = loading ? tr('ui.loading') : err ? tr('ui.market_empty_error') : tr('ui.market_empty');
  return (
    <>
      <div className="p3" style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: '5px 12px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 9, letterSpacing: '.2em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase' }}>{tr('ui.market_by_country')}</span>
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
          <span>{tr('ui.market_overview_error', { error: err })}</span>
          <button onClick={() => setRetry(r => r + 1)} className="btn btn-cyan ml-auto">
            <RefreshCw size={11} /> {tr('ui.retry')}
          </button>
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
        <OvwSection title={tr('ui.market_indices')} rows={data?.indici || []} onSelect={onSelect} empty={empty} />
        <OvwSection title={tr('ui.market_equities', { country: COUNTRY_LABELS[country] || country })} rows={data?.azioni || []} onSelect={onSelect} empty={empty} />
        <OvwSection title={tr('ui.market_commodities')} rows={data?.commodities || []} onSelect={onSelect} empty={empty} />
        <OvwSection title={tr('ui.market_currencies')} rows={data?.valute || []} onSelect={onSelect} empty={empty} />
        <OvwSection title={tr('ui.market_bonds')} rows={data?.obbligazioni || []} onSelect={onSelect} empty={empty} />
        <OvwSection title="FUTURES" rows={data?.futures || []} onSelect={onSelect} empty={empty} />
      </div>
    </>
  );
}

export default function MarketPage() {
  const tr = useT();
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<MktSearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [tk, setTk] = useState('');
  const [quote, setQuote] = useState<MktQuote | null>(null);
  const [quoteErr, setQuoteErr] = useState<string | null>(null);
  const [news, setNews] = useState<MktNewsItem[] | null>(null);
  const [newsErr, setNewsErr] = useState<string | null>(null);
  const [showSummary, setShowSummary] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const [fav, setFav] = useState<boolean | null>(null);
  const [favErr, setFavErr] = useState<{ operation: 'read' | 'write'; detail: string } | null>(null);
  const [fin, setFin] = useState<MktFinancials | null>(null);
  const [finError, setFinError] = useState<string | null>(null);
  const [holders, setHolders] = useState<MktHolders | null>(null);
  const [holdersErr, setHoldersErr] = useState<string | null>(null);
  const [finTab, setFinTab] = useState<'income' | 'balance' | 'cashflow'>('income');

  // ricerca con debounce
  useEffect(() => {
    if (!q.trim()) { setHits([]); setOpen(false); setSearchError(null); setSearching(false); return; }
    let alive = true;
    setSearching(true);
    setSearchError(null);
    const t = setTimeout(() => {
      Bellomberg.mktSearch(q.trim())
        .then(r => { if (alive) { setHits(r.results || []); setOpen(true); } })
        .catch(e => { if (alive) { setHits([]); setOpen(false); setSearchError(sourceError(e)); } })
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
    setFavErr(null);
    Bellomberg.favorites()
      .then(r => { if (m) setFav((r.favorites || []).some(f => f.ticker === tk)); })
      .catch(e => { if (m) { setFav(null); setFavErr({ operation: 'read', detail: sourceError(e) }); } });
    return () => { m = false; };
  }, [tk]);

  const toggleFav = async () => {
    if (fav == null || !tk) return;
    const cur = fav;
    setFav(!cur);
    setFavErr(null);
    try {
      if (cur) await Bellomberg.favDel(tk);
      else await Bellomberg.favAdd({ ticker: tk, name: quote?.name || '', sector: quote?.sector || '', industry: quote?.industry || '' });
    } catch (e) { setFav(cur); setFavErr({ operation: 'write', detail: sourceError(e) }); }
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
    Bellomberg.mktQuote(tk).then(r => { if (m) setQuote(r); }).catch(e => { if (m) { setQuote({ ticker: tk, name: tk }); setQuoteErr(sourceError(e)); } });
    Bellomberg.mktNews(tk).then(r => { if (m) setNews(r.items || []); }).catch(e => { if (m) { setNews([]); setNewsErr(sourceError(e)); } });
    return () => { m = false; };
  }, [tk]);

  // bilanci + ownership al cambio titolo (T4-2)
  useEffect(() => {
    if (!tk) return;
    let m = true;
    setFin(null); setFinError(null); setHolders(null); setFinTab('income'); setHoldersErr(null);
    Bellomberg.mktFinancials(tk).then(r => { if (m) setFin(r); }).catch(e => { if (m) { setFinError(sourceError(e)); setFin({ ticker: tk }); } });
    Bellomberg.mktHolders(tk).then(r => { if (m) setHolders(r); }).catch(e => { if (m) { setHolders({ ticker: tk, major: [], institutional: [] }); setHoldersErr(sourceError(e)); } });
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
          <span className="font-mono" style={{ fontSize: 10, letterSpacing: '.24em', color: '#FFA51E', textTransform: 'uppercase' }}>{tr('ui.market_search')}</span>
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
                placeholder={tr('ui.market_search_hint')}
                className="flex-1 bg-transparent outline-none font-mono text-xs text-text placeholder:text-faint tracking-wider"
              />
              {searching && <Cpu size={11} className="text-amber animate-pulse" />}
            </div>
            {searchError !== null && <div role="alert" className="text-crimson text-2xs font-mono">{tr('ui.market_search_failed', { error: searchError })}</div>}
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
          <span style={{ fontSize: 9, letterSpacing: '.2em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase', marginRight: 4 }}>{tr('ui.market_quick')}</span>
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
                          title={fav ? tr('ui.remove_favorite') : tr('ui.add_favorite')}
                          className={'flex items-center gap-1.5 px-2 py-0.5 border font-mono text-3xs uppercase tracking-wider transition-colors disabled:opacity-40 ' + (fav ? 'border-crimson text-crimson bg-crimson/10' : 'border-border text-muted hover:text-crimson hover:border-crimson')}>
                    <Heart size={10} fill={fav ? 'currentColor' : 'none'} /> {fav ? tr('ui.favorite') : tr('ui.favorites')}
                  </button>
                  <button onClick={togglePulsePick}
                          title={inPulse ? tr('ui.pulse_remove') : tr('ui.pulse_add')}
                          className={'flex items-center gap-1.5 px-2 py-0.5 border font-mono text-3xs uppercase tracking-wider transition-colors ' + (inPulse ? 'border-cyan text-cyan bg-cyan/10' : 'border-border text-muted hover:text-cyan hover:border-cyan')}>
                    <Activity size={10} /> {inPulse ? tr('ui.pulse_selected') : '+ MACRO PULSE'}
                  </button>
                  {quote?.recommendation && (
                    <span className="font-mono text-3xs uppercase tracking-wider px-1.5 py-0.5 border border-cyan-deep text-cyan">{quote.recommendation}</span>
                  )}
                </div>
                <div className="font-mono text-3xs text-muted uppercase tracking-wider mt-1">
                  {quote?.exchange || '-'} &middot; {quote?.sector || '-'}{quote?.industry ? ' / ' + quote.industry : ''}
                  {' · '}{tr('ui.source_original')}
                </div>
                {favErr && <div role="alert" className="text-crimson text-2xs font-mono">{tr(favErr.operation === 'read' ? 'ui.market_favorites_failed' : 'ui.market_favorite_unconfirmed', { error: favErr.detail })}</div>}
              </div>
              <div className="p-3.5 text-right">
                {quote === null ? (
                  <Cpu size={16} className="text-amber animate-pulse inline" />
                ) : quoteErr ? (
                  <div className="font-mono text-2xs text-crimson max-w-[280px]">
                    {tr('ui.quote_error', { error: quoteErr })}
                  </div>
                ) : (
                  <>
                    <div className="font-mono tabular-nums leading-none" style={{ fontSize: 34, fontWeight: 300, color: '#ECF1FA', textShadow: '0 0 30px rgba(41,211,242,.12)' }}>
                      {fx(px)} <span style={{ fontSize: 14, color: '#8D9FC4', fontWeight: 300 }}>{quote.currency || ''}</span>
                    </div>
                    {chg != null && (
                      <div className={'font-mono text-sm tabular-nums mt-1 ' + (up ? 'text-emerald' : 'text-crimson')}>
                        {up ? '+' : ''}{fx(chg)}% <span className="text-3xs text-muted">{tr('ui.previous_close')}</span>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
            {/* STATS GRID */}
            <div className="grid grid-cols-5 border-t border-border">
              <Stat label={tr('ui.market_cap')} value={cn(quote?.market_cap)} />
              <Stat label="P/E" value={fx(quote?.pe)} />
              <Stat label={tr('ui.forward_pe')} value={fx(quote?.fwd_pe)} />
              <Stat label="EPS" value={fx(quote?.eps)} />
              <Stat label="Beta" value={fx(quote?.beta)} />
              <Stat label={tr('ui.dividend_yield')} value={quote?.div_yield != null ? (quote.div_yield > 0.5 ? fx(quote.div_yield) : fx(quote.div_yield * 100)) + '%' : '-'} />
              <Stat label="Volume" value={cn(quote?.volume, 1)} />
              <Stat label={tr('ui.average_volume')} value={cn(quote?.avg_volume, 1)} />
              <Stat label={tr('ui.short_float')} value={quote?.short_pct_float != null ? fx(quote.short_pct_float * 100, 1) + '%' : '-'} />
              <Stat label={tr('ui.mean_target')} value={fx(quote?.target_mean)} tone={quote?.target_mean != null && px != null ? (quote.target_mean >= px ? 'text-emerald' : 'text-crimson') : undefined} />
              <Stat label="EV" value={cn(quote?.ev)} />
              <Stat label="EV/EBITDA" value={fx(quote?.ev_ebitda)} />
              <Stat label={tr('ui.ev_sales')} value={fx(quote?.ev_sales)} />
              <Stat label="PEG" value={fx(quote?.peg)} />
              <Stat label="P/B" value={fx(quote?.pb)} />
              <Stat label={tr('ui.fcf_yield')} value={quote?.fcf != null && quote?.market_cap ? fx((quote.fcf / quote.market_cap) * 100, 1) + '%' : '-'} tone="text-cyan" />
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
              <div className="p3h">{tr('ui.chart_price_action', { ticker: tk })}
                <span className="side">{tr('ui.chart_controls')}</span>
              </div>
              <TvChartPanel ticker={tk} height={420} defaultRange={5} defaultInterval={4} />
            </div>

            <div className="p3 col-span-4 flex flex-col">
              <div className="p3h am">{tr('ui.market_wire', { ticker: tk })}
                <span className="side">{news?.length ?? '...'} · {tr('ui.source_original')}</span>
              </div>
              <div className="flex-1 overflow-y-auto max-h-[430px] p-2 space-y-1.5">
                {news === null ? (
                  <div className="text-faint text-2xs font-mono py-8 text-center"><Cpu size={12} className="animate-pulse inline mr-2" />{tr('ui.wire_loading')}</div>
                ) : newsErr ? (
                  <div className="text-crimson text-2xs font-mono py-8 text-center">{tr('ui.wire_error', { error: newsErr })}</div>
                ) : news.length === 0 ? (
                  <div className="text-faint text-2xs font-mono py-8 text-center">{tr('ui.news_empty')}</div>
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
            <div className="p3h">{tr('ui.financials_title')}
              <span className="side">
                <span className="tfg">
                  <button onClick={() => setFinTab('income')} className={btn(finTab === 'income')}>{tr('ui.income_statement')}</button>
                  <button onClick={() => setFinTab('balance')} className={btn(finTab === 'balance')}>{tr('ui.balance_sheet')}</button>
                  <button onClick={() => setFinTab('cashflow')} className={btn(finTab === 'cashflow')}>{tr('ui.cash_flow')}</button>
                </span>
              </span>
            </div>
            <div className="p-2">
              {fin === null
                ? <div className="text-faint text-2xs font-mono py-6 text-center"><Cpu size={11} className="animate-pulse inline mr-2" />{tr('ui.financials_loading')}</div>
                : fin.error || !fin.statements
                  ? <div className="text-faint text-2xs font-mono py-6 text-center">{finError !== null ? `${tr('ui.financials_error')}: ${finError}` : fin.error || tr('ui.no_data')}</div>
                  : <FinTable b={fin.statements[finTab]} />}
            </div>
          </div>

          {/* OWNERSHIP (T4-2) */}
          <div className="grid grid-cols-12 gap-2">
            <div className="p3 col-span-4">
              <div className="p3h">{tr('ui.ownership_title')}</div>
              <div className="p-3 space-y-1.5 font-mono text-2xs">
                {holders === null
                  ? <div className="text-faint py-4 text-center">{tr('ui.loading')}</div>
                  : holdersErr
                    ? <div className="text-crimson py-4 text-center">{tr('ui.ownership_error', { error: holdersErr })}</div>
                    : holders.major.length === 0
                      ? <div className="text-faint py-4 text-center">{tr('settings.nd')}</div>
                      : holders.major.map((m, i) => {
                        const OWN_LABELS: Record<string, string> = {
                          insidersPercentHeld: tr('ui.ownership_insiders'), institutionsPercentHeld: tr('ui.ownership_institutions'),
                          institutionsFloatPercentHeld: tr('ui.ownership_float'), institutionsCount: tr('ui.ownership_count'),
                        };
                        const isCount = String(m.label).toLowerCase().includes('count');
                        return (
                          <div key={i} className="flex items-center justify-between border-b border-border/40 pb-1">
                            <span className="text-text-dim">{OWN_LABELS[String(m.label)] || m.label}</span>
                            <span className="text-cyan tabular-nums">
                              {typeof m.value === 'number' ? (isCount ? Math.round(m.value).toLocaleString(localeDi(linguaCorrente())) : pctv(m.value)) : String(m.value ?? '-')}
                            </span>
                          </div>
                        );
                      })}
              </div>
            </div>
            <div className="p3 col-span-8">
              <div className="p3h">{tr('ui.institutional_title')}
                <span className="side">{holders?.institutional.length ?? '...'}</span>
              </div>
              <div className="overflow-x-auto max-h-[280px] overflow-y-auto">
                {holders && holders.institutional.length > 0 ? (
                  <table className="table-bbg">
                    <thead><tr><th>{tr('ui.holder')}</th><th className="text-right">{tr('ui.shares')}</th><th className="text-right">{tr('ui.outstanding_pct')}</th><th className="text-right">{tr('ui.value')}</th><th className="text-right">{tr('ui.date')}</th></tr></thead>
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
                ) : <div className={'text-2xs font-mono py-6 text-center ' + (holdersErr ? 'text-crimson' : 'text-faint')}>{holders === null ? tr('ui.loading') : holdersErr ? tr('ui.ownership_failed') : tr('settings.nd')}</div>}
              </div>
            </div>
          </div>

          <FilingDiffPanel key={tk} ticker={tk} />

          {/* PROFILO */}
          {quote?.summary && (
            <div className="p3">
              <button onClick={() => setShowSummary(s => !s)} className="p3h w-full text-left cursor-pointer" style={{ background: 'transparent', border: 0, borderBottom: showSummary ? '1px solid #1A2440' : 0, fontFamily: 'inherit' }}>
                {tr('ui.company_profile')}
                <span className="side">{showSummary ? tr('ui.close_up') : tr('ui.open_down')}</span>
              </button>
              {showSummary && <div className="p-3.5 text-xs text-text-dim leading-relaxed"><div className="text-faint text-3xs">{tr('ui.source_original')}</div>{quote.summary}</div>}
            </div>
          )}
        </>
      )}
    </div>
  );
}
