import { externalWebUrl } from '../../electron/security';
// ============================================================
// BELLOMBERG — NEWS WIRE v3 OBSIDIAN (F5; v2 = T4-3 parte B)
// Vestito OBSIDIAN COMMAND (.obsx/.p3 condivisi con F1/F2/F15),
// LOGICA ED ENDPOINT INTATTI dalla v2:
// Vista WIRE: nastro agenzia cronologico dal feed DB (get_feed),
// filtri client-side sul prefetch (limit 200), MARKET MOVING per
// _materiality, DESK STATS client-side, preferiti PM con stella.
// Vista DESK: briefing/macro/societario/calendario/global (legacy v0.7).
// Refresh pesante verso provider SOLO su bottone; AUTO 60s legge
// soltanto /news/feed (DB locale).
// FONTI MUTE (voce (25) backend): hero cell + banner cablati sui campi
// fonti_mute/avviso dei payload consumati — oggi quegli endpoint NON li
// espongono => "n.d." DICHIARATO; si accendono da soli col coordinamento
// backend chiesto in (F5). Mai dedotto client-side.
// ============================================================
import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { useScalaTesto } from '../lib/svg-kit';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Newspaper, RefreshCw, AlertCircle, ExternalLink, FileText,
  TrendingUp, Building2, Globe, Sparkles, Calendar, Layers,
  Briefcase, Megaphone, Shield, Bitcoin, ChevronDown, Check,
  Star, Zap, Activity, Filter,
} from 'lucide-react';
import {
  Bellomberg,
  NewsItem, MacroNewsItem, CorporateEvent, GlobalNewsItem,
  BriefingData, EconomicEvent, FavCompany, FontiMuteFields, UltimoGiro,
} from '@/lib/api';
import './dashboard-command.css';

// ------------------------------------------------------------
// TIPI LOCALI — campi runtime restituiti da get_feed (backend
// news_aggregator) non presenti nel tipo base NewsItem.
// ------------------------------------------------------------
type FeedItem = NewsItem & {
  headline_it?: string | null;
  why_matters?: string | null;
  title_original?: string | null;
  snippet_original?: string | null;
  _materiality?: number | null;
};

type SentBucket = 'pos' | 'neu' | 'neg';

interface WireStats {
  pos: number; neu: number; neg: number;
  h24: number; h6: number; total: number;
  topThemes: Array<{ key: string; label: string; count: number }>;
  nTickers: number; nProviders: number; avgRel: number;
}

// ------------------------------------------------------------
// PALETTE OBSIDIAN (hex solo per inline-style; classi altrove)
// ------------------------------------------------------------
const C = {
  amber: '#FFA51E', amberBright: '#FFC555', cyan: '#29D3F2',
  emerald: '#21E0A0', crimson: '#FF3D60', gold: '#D4AF37',
  muted: '#8D9FC4', faint: '#73829F', steel: '#2A3760',
};

// ============================================================
// HELPERS
// ============================================================
function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '-';
  try {
    const t = new Date(iso).getTime();
    if (!isFinite(t)) return iso.slice(0, 16);
    const diffMs = Date.now() - t;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'now';
    if (diffMin < 60) return `${diffMin}m`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH}h`;
    return `${Math.floor(diffH / 24)}d`;
  } catch { return '-'; }
}

function tsOf(n: { published_at?: string | null; pulled_at?: string | null }): number {
  for (const s of [n.published_at, n.pulled_at]) {
    if (s) { const t = new Date(s).getTime(); if (isFinite(t)) return t; }
  }
  return 0;
}

function hhmm(n: FeedItem): string {
  const t = tsOf(n);
  if (!t) return '--:--';
  return new Date(t).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
}

function dayKeyFromTs(ts: number): string {
  if (!ts) return 'nd';
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function dayLabel(key: string): string {
  if (key === 'nd') return 'DATA NON DISPONIBILE';
  const today = dayKeyFromTs(Date.now());
  const yesterday = dayKeyFromTs(Date.now() - 86_400_000);
  const d = new Date(`${key}T12:00:00`);
  const fmt = d.toLocaleDateString('it-IT', { weekday: 'short', day: '2-digit', month: 'short' })
               .replace(/\./g, '').toUpperCase();
  if (key === today) return `OGGI · ${fmt}`;
  if (key === yesterday) return `IERI · ${fmt}`;
  const yr = d.getFullYear() !== new Date().getFullYear() ? ` ${d.getFullYear()}` : '';
  return fmt + yr;
}

function sentBucket(s: string | null | undefined): SentBucket {
  const v = (s || '').toLowerCase();
  if (v === 'bullish' || v === 'positive') return 'pos';
  if (v === 'bearish' || v === 'negative') return 'neg';
  return 'neu';
}

function sentColor(s: string | null | undefined): string {
  const b = sentBucket(s);
  return b === 'pos' ? C.emerald : b === 'neg' ? C.crimson : C.muted;
}

function relColor(rel: number): string {
  if (rel >= 8) return C.amber;
  if (rel >= 5) return C.cyan;
  return C.muted;
}

// Categorie derivate dal campo `theme` (valori scritti da
// news_aggregator.auto_pull_feed: fed / ecb / ukraine / middle_east /
// cpi / btc_etf / china / italy; '' = news ticker o feed generale).
// Fallback: temi nuovi etichettati dinamicamente dagli item caricati.
const THEME_LABELS: Record<string, string> = {
  fed: 'FED / FOMC',
  ecb: 'BCE',
  cpi: 'INFLAZIONE / CPI',
  ukraine: 'UCRAINA',
  middle_east: 'MEDIO ORIENTE',
  btc_etf: 'BTC / ETF',
  china: 'CINA',
  italy: 'ITALIA',
  __pf: 'PORTAFOGLIO',
  __gen: 'GENERALE',
};

function themeKeyOf(n: FeedItem): string {
  const th = (n.theme || '').trim();
  if (th) return th;
  return n.ticker_mentioned ? '__pf' : '__gen';
}

function themeLabelOf(key: string): string {
  return THEME_LABELS[key] || key.replace(/_/g, ' ').toUpperCase();
}

function toggleIn<T>(arr: T[], v: T, set: (next: T[]) => void) {
  set(arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v]);
}

// --- helpers vista DESK (legacy v0.7, palette aggiornata) ---
function sentimentTone(s: string | null | undefined) {
  const b = sentBucket(s);
  if (b === 'pos') return { color: C.emerald, label: 'BULL' };
  if (b === 'neg') return { color: C.crimson, label: 'BEAR' };
  return { color: C.muted, label: 'NEUT' };
}

function importanceTone(imp: number | undefined) {
  const i = imp ?? 3;
  if (i >= 5) return C.crimson;
  if (i >= 4) return C.amber;
  if (i >= 3) return C.amberBright;
  return C.muted;
}

function categoryIcon(cat: string | undefined) {
  switch (cat) {
    case 'rates':       return Briefcase;
    case 'inflation':   return TrendingUp;
    case 'geopolitics': return Shield;
    case 'politics':    return Megaphone;
    case 'em':          return Globe;
    case 'commodities': return Layers;
    case 'crypto':      return Bitcoin;
    case 'corporate':   return Building2;
    default:            return Newspaper;
  }
}

const CATEGORY_LABELS: Record<string, string> = {
  rates:       'Banche Centrali',
  inflation:   'Inflazione',
  geopolitics: 'Geopolitica',
  politics:    'Politica',
  em:          'EM',
  commodities: 'Commodity',
  crypto:      'Crypto',
  corporate:   'Eventi Soc.',
};

const COUNTRIES_AVAILABLE = ['US', 'EU', 'UK', 'IT', 'DE', 'JP', 'CN', 'FR', 'ES', 'CA', 'AU'];
const IMPORTANCE_LEVELS = [
  { value: 5, label: 'Alta (5*)' },
  { value: 4, label: 'Medio-Alta (4*)' },
  { value: 3, label: 'Media (3*)' },
];

const DATE_PRESETS_NEWS: Array<{ value: string; label: string }> = [
  { value: 'today',  label: 'Oggi' },
  { value: '24h',    label: 'Ultime 24h' },
  { value: '3days',  label: 'Ultimi 3 giorni' },
  { value: 'week',   label: 'Ultima settimana' },
  { value: 'all',    label: 'Tutto' },
];
const DATE_PRESETS_CAL: Array<{ value: string; label: string }> = [
  { value: 'today',    label: 'Oggi' },
  { value: 'tomorrow', label: 'Domani' },
  { value: 'week',     label: 'Settimana' },
  { value: '2weeks',   label: '2 settimane' },
  { value: 'all',      label: 'Tutto' },
];

const SENT_FILTERS: Array<{ key: SentBucket; label: string; color: string }> = [
  { key: 'pos', label: 'POS', color: C.emerald },
  { key: 'neu', label: 'NEU', color: C.muted },
  { key: 'neg', label: 'NEG', color: C.crimson },
];
const REL_FILTERS = [
  { value: 0, label: 'Tutte' },
  { value: 5, label: 'Rel 5+' },
  { value: 8, label: 'Rel 8+' },
];

const HOLIDAY_KEYWORDS = ['day of', 'eid ', 'arafa', 'independence', 'holiday', 'memorial', 'thanksgiving',
                          'christmas', 'easter', 'new year', 'labour day', 'national day', 'bank holiday'];

function isHoliday(title: string): boolean {
  const t = title.toLowerCase();
  return HOLIDAY_KEYWORDS.some(k => t.includes(k));
}

function matchesDatePresetNews(iso: string | null | undefined, preset: string): boolean {
  if (!preset || preset === 'all') return true;
  if (!iso) return true;
  const t = new Date(iso).getTime();
  if (!isFinite(t)) return true;
  const now = Date.now();
  const ageH = (now - t) / 3600_000;
  if (preset === 'today') {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    return t >= today.getTime();
  }
  if (preset === '24h')   return ageH <= 24;
  if (preset === '3days') return ageH <= 72;
  if (preset === 'week')  return ageH <= 168;
  return true;
}

function matchesDatePresetCal(eventDate: string, preset: string): boolean {
  if (!preset || preset === 'all') return true;
  if (!eventDate) return false;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
  const weekEnd = new Date(today); weekEnd.setDate(today.getDate() + 7);
  const twoWeeksEnd = new Date(today); twoWeeksEnd.setDate(today.getDate() + 14);
  const ed = new Date(eventDate);
  if (preset === 'today')    return ed.toDateString() === today.toDateString();
  if (preset === 'tomorrow') return ed.toDateString() === tomorrow.toDateString();
  if (preset === 'week')     return ed >= today && ed <= weekEnd;
  if (preset === '2weeks')   return ed >= today && ed <= twoWeeksEnd;
  return true;
}

// ============================================================
// DROPDOWN POPUP (multi-select con checkbox — vista DESK)
// ============================================================
function Dropdown<T extends string | number>({
  label, options, selected, onChange, allLabel = 'Tutto', summaryFmt, width = 150,
}: {
  label: string;
  options: Array<{ value: T; label: string; count?: number }>;
  selected: T[];
  onChange: (v: T[]) => void;
  allLabel?: string;
  summaryFmt?: (sel: T[], opts: Array<{ value: T; label: string; count?: number }>) => string;
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const toggle = (v: T) => {
    if (selected.includes(v)) onChange(selected.filter(x => x !== v));
    else onChange([...selected, v]);
  };
  const summary = summaryFmt
    ? summaryFmt(selected, options)
    : selected.length === 0
      ? allLabel
      : selected.length === options.length
        ? allLabel
        : `${selected.length}/${options.length}`;

  return (
    <div ref={ref} className="relative" style={{ minWidth: width }}>
      <div className="text-3xs text-faint uppercase tracking-wider font-mono mb-0.5">{label}</div>
      <button
        onClick={() => setOpen(v => !v)}
        className={`w-full flex items-center justify-between gap-2 px-2 py-1 text-2xs font-mono
                    bg-bg-elev border rounded-sm transition-colors
                    ${open || selected.length > 0
                      ? 'border-amber/60 text-amber'
                      : 'border-border text-text hover:border-amber/40'}`}
      >
        <span className="truncate">{summary}</span>
        <ChevronDown size={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 left-0 right-0 bg-bg border border-amber/40 rounded-sm
                        max-h-64 overflow-y-auto py-1"
             style={{ boxShadow: '0 4px 16px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,165,30,0.18)' }}>
          {/* All toggle */}
          <button
            onClick={() => { onChange([]); }}
            className={`w-full flex items-center gap-2 px-2 py-1 text-2xs font-mono text-left
                        hover:bg-amber/10 transition-colors ${selected.length === 0 ? 'text-amber' : 'text-text-dim'}`}
          >
            <div className={`w-3 h-3 border rounded-sm flex items-center justify-center
                            ${selected.length === 0 ? 'bg-amber border-amber' : 'border-border'}`}>
              {selected.length === 0 && <Check size={9} className="text-bg" />}
            </div>
            <span>{allLabel}</span>
          </button>
          <div className="h-px bg-border my-0.5" />
          {options.map(opt => {
            const on = selected.includes(opt.value);
            return (
              <button
                key={String(opt.value)}
                onClick={() => toggle(opt.value)}
                className={`w-full flex items-center gap-2 px-2 py-1 text-2xs font-mono text-left
                            hover:bg-amber/10 transition-colors ${on ? 'text-amber' : 'text-text'}`}
              >
                <div className={`w-3 h-3 border rounded-sm flex items-center justify-center
                                ${on ? 'bg-amber border-amber' : 'border-border'}`}>
                  {on && <Check size={9} className="text-bg" />}
                </div>
                <span className="flex-1 truncate">{opt.label}</span>
                {opt.count !== undefined && (
                  <span className={`text-3xs ${on ? 'text-amber/70' : 'text-faint'}`}>{opt.count}</span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// SINGLE-SELECT dropdown (preset data — vista DESK)
function DropdownSingle({
  label, options, selected, onChange, width = 130,
}: {
  label: string;
  options: Array<{ value: string; label: string }>;
  selected: string;
  onChange: (v: string) => void;
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const summary = options.find(o => o.value === selected)?.label || options[0]?.label || '-';

  return (
    <div ref={ref} className="relative" style={{ minWidth: width }}>
      <div className="text-3xs text-faint uppercase tracking-wider font-mono mb-0.5">{label}</div>
      <button
        onClick={() => setOpen(v => !v)}
        className={`w-full flex items-center justify-between gap-2 px-2 py-1 text-2xs font-mono
                    bg-bg-elev border rounded-sm transition-colors
                    ${open ? 'border-amber/60 text-amber' : 'border-border text-text hover:border-amber/40'}`}
      >
        <span className="truncate">{summary}</span>
        <ChevronDown size={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 left-0 right-0 bg-bg border border-amber/40 rounded-sm
                        max-h-64 overflow-y-auto py-1"
             style={{ boxShadow: '0 4px 16px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,165,30,0.18)' }}>
          {options.map(opt => (
            <button
              key={opt.value}
              onClick={() => { onChange(opt.value); setOpen(false); }}
              className={`w-full flex items-center gap-2 px-2 py-1 text-2xs font-mono text-left
                          hover:bg-amber/10 transition-colors ${selected === opt.value ? 'text-amber' : 'text-text'}`}
            >
              <div className={`w-3 h-3 border rounded-sm flex items-center justify-center
                              ${selected === opt.value ? 'bg-amber border-amber' : 'border-border'}`}>
                {selected === opt.value && <Check size={9} className="text-bg" />}
              </div>
              <span>{opt.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// SUB-COMPONENTS CONDIVISI
// ============================================================
function PanelHeader({ icon: Icon, title, count, action }: {
  icon: any; title: string; count?: number; action?: React.ReactNode;
}) {
  return (
    <div className="p3h am">
      <Icon size={11} /> {title}
      {count !== undefined && <span className="n num">({count})</span>}
      {action && <span className="side" style={{ display: 'inline-flex', alignItems: 'center' }}>{action}</span>}
    </div>
  );
}

function NewsRow({
  title, snippet, url, provider, source, published_at, ticker,
  sentiment, importance, badges,
}: {
  title: string; snippet?: string | null; url?: string | null;
  provider?: string | null; source?: string | null;
  published_at?: string | null; ticker?: string | null;
  sentiment?: string | null; importance?: number | null;
  badges?: React.ReactNode;
}) {
  const tone = sentimentTone(sentiment);
  const barColor = importance != null && importance >= 5 ? C.crimson
                  : importance != null && importance >= 4 ? C.amber
                  : sentBucket(sentiment) === 'pos' ? C.emerald
                  : sentBucket(sentiment) === 'neg' ? C.crimson
                  : C.steel;
  const href = externalWebUrl(url || '') || undefined;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer"
       className={`block hover:bg-bg-elev border-b border-border/40 transition-colors group ${href ? '' : 'cursor-default'}`}
       style={{ borderLeft: `2px solid ${barColor}` }}>
      <div className="px-2 py-1">
        <div className="flex items-center gap-1 flex-wrap">
          {ticker && (
            <span className="text-3xs font-mono font-bold px-1 bg-amber/15 text-gold rounded-sm">{ticker}</span>
          )}
          {sentiment && (
            <span className="text-3xs font-mono px-1 rounded-sm"
                  style={{ color: tone.color, background: tone.color + '14' }}>{tone.label}</span>
          )}
          {badges}
          <span className="text-3xs text-faint font-mono ml-auto">{timeAgo(published_at)}</span>
        </div>
        <div className="text-2xs text-text leading-tight group-hover:text-amber-bright transition-colors mt-0.5">{title}</div>
        {snippet && <div className="text-3xs text-muted leading-tight mt-0.5 line-clamp-1 italic">{snippet}</div>}
        <div className="flex items-center gap-1 mt-0.5">
          <span className="text-3xs text-faint truncate">
            {provider}{source && provider !== source ? ` · ${source}` : ''}
          </span>
          {href && <ExternalLink size={8} className="text-faint group-hover:text-amber" />}
        </div>
      </div>
    </a>
  );
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div className="m-2 p-2 border-l-2 border-crimson bg-crimson/5 text-2xs text-crimson font-mono flex items-start gap-2">
      <AlertCircle size={11} className="mt-0.5 flex-shrink-0" /> {msg}
    </div>
  );
}

function LoadingBox({ label = 'Caricamento...' }: { label?: string }) {
  return <div className="p-4 text-center text-2xs text-faint font-mono animate-pulse">{label}</div>;
}

function EmptyBox({ label }: { label: string }) {
  return <div className="p-4 text-center text-2xs text-faint font-mono">{label}</div>;
}

// ============================================================
// BRIEFING CARD (vista DESK)
// ============================================================
function BriefingCard({ data, loading, error, onRefresh }: {
  data: BriefingData | null; loading: boolean; error: string | null; onRefresh: () => void;
}) {
  const stale = data?.stale;
  const isInitial = !data?.generated_at;
  const ageLabel = data?.age_minutes != null
    ? (data.age_minutes < 60 ? `${data.age_minutes}m fa` : `${Math.floor(data.age_minutes / 60)}h fa`)
    : 'mai';
  return (
    <div className="p3 flex-1 min-h-0" style={{ ...bd(60), borderLeft: '2px solid rgba(255,165,30,.55)' }}>
      <div className="p3h am"><Sparkles size={11} /> DAILY BRIEFING
        {data?.slot_label && <span className="n">· {data.slot_label}</span>}
        <span className="side" style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <span className={stale ? 'text-amber' : ''}>{isInitial ? 'MAI' : ageLabel}</span>
          <button onClick={onRefresh} disabled={loading}
                  className={'tb' + (loading ? ' dis' : '')} style={{ color: '#FFA51E' }}>
            {loading ? 'GENERO…' : '↻ REFRESH'}
          </button>
        </span>
      </div>
      <div className="flex-1 overflow-y-auto p-3 min-h-0">
        {error ? <ErrorBox msg={error} /> :
         loading && !data ? <LoadingBox label="Generazione briefing (Haiku)..." /> :
         <div className="prose prose-invert prose-sm max-w-none news-briefing-md">
           <ReactMarkdown remarkPlugins={[remarkGfm]}>{data?.briefing_md || '_Nessun briefing._'}</ReactMarkdown>
         </div>}
      </div>
    </div>
  );
}

// ============================================================
// VISTA WIRE — componenti
// ============================================================
function RailLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="font-mono text-3xs uppercase tracking-[0.22em] text-faint px-1 mb-1">{children}</div>
  );
}

function RailChip({ on, label, count, star, onClick }: {
  on: boolean; label: string; count?: number; star?: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-1.5 px-1.5 py-[3px] font-mono text-2xs rounded-sm text-left border transition-colors
                  ${on
                    ? 'bg-amber/15 text-amber border-amber/40'
                    : 'border-transparent text-text-dim hover:text-text hover:bg-panel-hi'}`}
    >
      {star && <Star size={8} className={`shrink-0 ${on ? 'text-amber fill-amber' : 'text-gold fill-gold'}`} />}
      <span className="truncate flex-1">{label}</span>
      {count !== undefined && (
        <span className={`text-3xs tabular-nums ${on ? 'text-amber/80' : 'text-faint'}`}>{count}</span>
      )}
    </button>
  );
}

// Riga del nastro: orario mono, dot sentiment, ticker gold (+stella se
// preferito), headline da desk, why_matters in corsivo, fonte e
// relevance a destra. REL >= 8: accento ambra (bordo sx + fondo).
function WireRow({ n, fav }: { n: FeedItem; fav: boolean }) {
  const hot = (n.relevance ?? 0) >= 8;
  const dot = sentColor(n.sentiment);
  const rel = n.relevance ?? 0;
  const href = externalWebUrl(n.url || '') || undefined;
  return (
    <a
      href={href} target="_blank" rel="noopener noreferrer"
      title={n.title_original || n.title}
      className={`bb-row-in group flex items-start gap-2 px-2 py-[5px] border-b border-border/30 transition-colors
                  ${hot ? 'bg-amber/[0.05] hover:bg-amber/[0.09]' : 'hover:bg-panel-hi'}
                  ${href ? '' : 'cursor-default'}`}
      style={{ borderLeft: hot ? `2px solid ${C.amber}` : '2px solid transparent' }}
    >
      <span className="font-mono text-2xs tabular-nums text-text-dim w-9 shrink-0 pt-0.5">{hhmm(n)}</span>
      <span className="h-1.5 w-1.5 rounded-full shrink-0 mt-1.5"
            style={{ background: dot, boxShadow: `0 0 5px ${dot}66` }} />
      <div className="flex-1 min-w-0">
        <div className="leading-snug">
          {n.ticker_mentioned && (
            <span className="font-mono text-2xs font-bold text-gold mr-1.5 whitespace-nowrap">
              {n.ticker_mentioned}
              {fav && <Star size={8} className="inline ml-0.5 -mt-0.5 text-amber fill-amber" />}
            </span>
          )}
          <span className="text-xs text-text group-hover:text-amber-bright transition-colors">{n.title}</span>
        </div>
        {n.snippet && (
          <div className="text-2xs italic text-muted leading-snug mt-0.5 truncate">{n.snippet}</div>
        )}
      </div>
      <div className="shrink-0 w-32 flex flex-col items-end gap-1 pt-0.5">
        <span className="font-mono text-3xs text-faint uppercase truncate max-w-full">
          {n.provider || '?'}{n.source && n.source !== n.provider ? ` · ${n.source}` : ''}
        </span>
        <div className="flex items-center gap-1.5">
          <div className="h-[3px] w-12 rounded-full overflow-hidden bg-border/70">
            <div className="h-full" style={{ width: `${Math.min(10, Math.max(0, rel)) * 10}%`, background: relColor(rel) }} />
          </div>
          <span className="font-mono text-3xs tabular-nums w-3 text-right" style={{ color: relColor(rel) }}>
            {rel > 0 ? rel : '-'}
          </span>
          <ExternalLink size={8} className={href ? 'text-faint group-hover:text-amber' : 'text-transparent'} />
        </div>
      </div>
    </a>
  );
}

// Riga MARKET MOVING: catalyst REL>=8 ordinati per _materiality.
// rank = posizione nella coda priorita' (l'ordine E' informazione: materiality).
function MoverRow({ n, fav, rank }: { n: FeedItem; fav: boolean; rank: number }) {
  const href = externalWebUrl(n.url || '') || undefined;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer"
       className={`block px-2 py-1.5 border-b border-border/40 hover:bg-panel-hi transition-colors group ${href ? '' : 'cursor-default'}`}>
      <div className="flex items-center gap-1.5">
        <span className="rkn num">{String(rank).padStart(2, '0')}</span>
        <span className="font-mono text-3xs tabular-nums text-text-dim">{hhmm(n)}</span>
        {n.ticker_mentioned && (
          <span className="font-mono text-3xs font-bold text-gold">
            {n.ticker_mentioned}
            {fav && <Star size={7} className="inline ml-0.5 -mt-px text-amber fill-amber" />}
          </span>
        )}
        <span className="ml-auto font-mono text-3xs tabular-nums px-1 rounded-sm bg-amber/15 text-amber">
          R{n.relevance ?? '-'}
        </span>
        <span className="font-mono text-3xs tabular-nums text-cyan"
              title="Materiality: relevance + peso book + freschezza + boost preferito">
          M{(n._materiality ?? 0).toFixed(1)}
        </span>
      </div>
      <div className="text-2xs text-text leading-snug mt-0.5 line-clamp-2 group-hover:text-amber-bright transition-colors">
        {n.title}
      </div>
      {n.snippet && (
        <div className="text-3xs italic text-muted leading-snug mt-0.5 line-clamp-1">{n.snippet}</div>
      )}
    </a>
  );
}

// CANALI // LINK PROVIDER: i provider come canali di trasmissione (LED
// vivo/spento/MUTO, barra volume) + copertura. Il muto viene SOLO dalla
// dichiarazione backend (25), mai dedotto. Sentiment/flusso/temi vivono
// nell'hero e nel rail: qui NIENTE doppioni della stessa misura.
function ChannelsPanel({ stats, channels, fontiDeclared }: {
  stats: WireStats; channels: Channel[]; fontiDeclared: boolean;
}) {
  const maxCh = Math.max(1, ...channels.map(c => c.count));
  return (
    <div className="p3 flex-1 min-h-0" style={bd(240)}>
      <div className="p3h"><Activity size={11} /> CANALI // LINK PROVIDER
        <span className="side">CLIENT-SIDE · DAL PREFETCH</span>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-3 min-h-0">
        <div>
          <div className="space-y-px">
            {channels.length === 0 && (
              <div className="font-mono text-3xs text-faint px-1">nessun canale nel feed</div>
            )}
            {channels.map(ch => {
              const live = ch.last > 0 && (Date.now() - ch.last) <= 24 * 3600_000;
              return (
                <div key={ch.name} className="chx font-mono"
                     title={ch.muteReason
                       ? `${ch.name}: MUTO dichiarato dal backend (${ch.muteReason}) — poche/zero news qui NON significa quiete`
                       : `${ch.name}: ${ch.count} item nel buffer · ultimo ${ch.last ? new Date(ch.last).toLocaleString('it-IT') : 'n.d.'}`}>
                  <span className={`ld ${ch.muteReason ? 'r' : live ? 'g' : 'off'}`} />
                  <span className="nm">{ch.name}</span>
                  <span className="bar"><i style={{ width: `${(ch.count / maxCh) * 100}%` }} /></span>
                  {ch.muteReason
                    ? <span className="mt">{ch.muteReason.replace('SKIP_', '')}</span>
                    : <span className="ct num">{ch.count}</span>}
                </div>
              );
            })}
          </div>
          <div className="font-mono text-3xs mt-1" style={{ color: fontiDeclared ? '#8D9FC4' : '#B97A00', letterSpacing: '.04em' }}
               title={fontiDeclared
                 ? 'Il backend dichiara lo stato quota dei provider (voce (25)): LED rosso = muto per budget/disable'
                 : 'Gli endpoint consumati qui non espongono ancora fonti_mute/avviso: LED solo vivo/spento dal feed, stato quota n.d. DICHIARATO'}>
            {fontiDeclared ? 'STATO QUOTA: DICHIARATO DAL BACKEND' : 'STATO QUOTA: n.d. — DICHIARAZIONE NON ESPOSTA QUI'}
          </div>
        </div>

        <div>
          <div className="font-mono text-3xs uppercase tracking-[0.22em] text-faint mb-1">Copertura</div>
          <div className="font-mono text-3xs text-text-dim tabular-nums leading-relaxed">
            <div className="flex justify-between"><span className="text-faint">TICKER DISTINTI</span><span>{stats.nTickers}</span></div>
            <div className="flex justify-between"><span className="text-faint">REL MEDIA</span><span className="text-cyan">{stats.avgRel.toFixed(1)}</span></div>
          </div>
        </div>
      </div>
    </div>
  );
}

// Cella hero (stesso idioma di HeroStat in F2): etichetta 8px, valore 17px, sub.
// `grado10`: la cella nata DOPO il lotto chiarezza nasce al gradino giusto
// (etichetta/sub 10px, sub #8D9FC4) — le sorelle a 9px/600 su #73829F misurano
// 4,27-4,40:1 (baseline cancello 17/08) e sono il debito di F8, non un modello:
// saliranno tutte insieme il giorno del suo triage.
function HeroCell({ label, value, tone, color, sub, title, grado10 }: {
  label: string; value: string; tone?: 'up' | 'dn'; color?: string; sub?: string; title?: string;
  grado10?: boolean;
}) {
  return (
    <div title={title}
         style={{ padding: '10px 16px', borderLeft: '1px solid rgba(26,36,64,.6)',
                  display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
      <div style={{ fontSize: grado10 ? 10 : 9, letterSpacing: '.2em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{label}</div>
      <div className={'num ' + (tone || '')} style={{ fontSize: 17, fontWeight: 300, marginTop: 2, color: color || (tone ? undefined : '#ECF1FA'), whiteSpace: 'nowrap' }}>{value}</div>
      {sub && <div style={{ fontSize: grado10 ? 10 : 9, fontWeight: 600, color: grado10 ? '#8D9FC4' : '#73829F', marginTop: 2, whiteSpace: 'nowrap', letterSpacing: '.05em', textTransform: 'uppercase' }}>{sub}</div>}
    </div>
  );
}

// SIGNAL TRAFFIC: istogramma item/ora sulle ultime 24h, SOLO dai timestamp
// veri del feed caricato (zero chiamate, zero numeri inventati). Barra 23 =
// ora corrente (evidenziata). Tooltip per barra: ora · conteggio.
function FlowBars({ feed, width = 220 }: { feed: FeedItem[]; width?: number }) {
  const buckets = useMemo(() => {
    const now = Date.now();
    const arr = new Array(24).fill(0) as number[];
    for (const n of feed) {
      const t = tsOf(n);
      if (!t) continue;
      const age = now - t;
      if (age < 0 || age >= 24 * 3600_000) continue;
      arr[23 - Math.floor(age / 3600_000)]++;
    }
    return arr;
  }, [feed]);
  const max = Math.max(1, ...buckets);
  const nowH = new Date().getHours();
  return (
    <div className="fbx num" style={{ width }}
         title="Item per ora, ultime 24h — misura il flusso SALVATO nel DB locale, non il mercato">
      {buckets.map((c, i) => {
        const hh = ((nowH - (23 - i)) + 48) % 24;
        return (
          <i key={i}
             className={i === 23 ? 'now' : c > 0 ? 'on' : ''}
             style={{ height: c === 0 ? 2 : Math.max(3, Math.round((c / max) * 26)) }}
             title={`${String(hh).padStart(2, '0')}:00 · ${c} item`} />
        );
      })}
    </div>
  );
}

// Canale provider per la matrice: count/last dal feed; muto = dichiarazione (25).
interface Channel { name: string; count: number; last: number; muteReason: string | null }

// Delay della sequenza di accensione pannelli (CSS .bootx, var --bd).
const bd = (ms: number) => ({ ['--bd']: `${ms}ms` } as React.CSSProperties);

// Jitter DETERMINISTICO (niente Math.random: i blip non devono ballare tra render).
function hashJitter(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return (h % 1000) / 1000;
}

// ============================================================
// RADAR SCOPE — contatti delle ultime 24h su quadrante polare.
// OGNI BLIP E' UNA NEWS VERA (tooltip = titolo, click = fonte):
// settore = tema · raggio = eta' (nuovo al centro, anelli 1H/6H/24H)
// colore = sentiment · blip caldo con alone = REL>=8 · anello oro = preferito.
// Zero dipendenze (SVG a mano, come l'altimetro della pagina di accesso).
// ============================================================
const SC_R_MIN = 14, SC_R_MAX = 118, SC_C = 150;
function scRadius(ageFrac: number): number {
  return SC_R_MIN + (SC_R_MAX - SC_R_MIN) * Math.sqrt(Math.min(1, Math.max(0, ageFrac)));
}
function RadarScope({ feed, favSet }: { feed: FeedItem[]; favSet: Set<string> }) {
  const { contacts, sectors } = useMemo(() => {
    const now = Date.now();
    const H24 = 24 * 3600_000;
    const items = feed.filter(n => { const t = tsOf(n); return t > 0 && (now - t) < H24; });
    const counts: Record<string, number> = {};
    items.forEach(n => { const k = themeKeyOf(n); counts[k] = (counts[k] || 0) + 1; });
    const topKeys = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 7).map(([k]) => k);
    const secs = topKeys.length > 0 ? [...topKeys, '__altro'] : ['__altro'];
    const N = secs.length;
    const cts = items.map(n => {
      const age = (now - tsOf(n)) / H24;
      const rr = scRadius(age);
      const k = themeKeyOf(n);
      const i0 = topKeys.indexOf(k);
      const si = i0 >= 0 ? i0 : N - 1;
      const j = hashJitter(String(n.id ?? n.title));
      const ang = (-90 + (si + 0.12 + 0.76 * j) * (360 / N)) * Math.PI / 180;
      return { n, x: SC_C + rr * Math.cos(ang), y: SC_C + rr * Math.sin(ang) };
    });
    return { contacts: cts, sectors: secs };
  }, [feed]);
  const N = sectors.length;
  // Il testo dentro un viewBox fisso scala col contenitore. Misurato sull'app
  // viva: questo radar e' reso a 244px a `terzo` (0,81x) e a 200px col
  // Windows al 150% (0,67x), quindi un 7px arrivava all'occhio come 5,69px e
  // 4,67px. Col fattore, il corpo dichiarato e' quello che si vede.
  const svgRef = useRef<SVGSVGElement>(null);
  const kT = useScalaTesto(svgRef, 300);
  return (
    <div className="scwrap">
      <div className="scbox">
      <svg ref={svgRef} viewBox="0 0 300 300">
        {/* anelli tempo: 1H / 6H / 24H */}
        {([[1 / 24, '1H'], [6 / 24, '6H'], [1, '24H']] as Array<[number, string]>).map(([f, lb]) => (
          <g key={lb}>
            <circle cx={SC_C} cy={SC_C} r={scRadius(f)} fill="none" stroke="#1A2440" strokeWidth={1} />
            <text x={SC_C + 2} y={SC_C - scRadius(f) + 8} fontSize={9 * kT} fontWeight={600} fill="#73829F" fontFamily="inherit">{lb}</text>
          </g>
        ))}
        {/* raggi settore + etichette tema */}
        {sectors.map((k, i) => {
          const a0 = (-90 + i * (360 / N)) * Math.PI / 180;
          const am = (-90 + (i + 0.5) * (360 / N)) * Math.PI / 180;
          const lx = SC_C + (SC_R_MAX + 10) * Math.cos(am);
          const ly = SC_C + (SC_R_MAX + 10) * Math.sin(am);
          return (
            <g key={k}>
              <line x1={SC_C} y1={SC_C} x2={SC_C + SC_R_MAX * Math.cos(a0)} y2={SC_C + SC_R_MAX * Math.sin(a0)}
                    stroke="#1A2440" strokeWidth={0.6} />
              <text x={lx} y={ly} fontSize={8 * kT} fill="#8D9FC4" textAnchor="middle" dominantBaseline="middle"
                    fontFamily="inherit" letterSpacing={0.5}>
                {k === '__altro' ? 'ALTRO' : themeLabelOf(k).slice(0, 9)}
              </text>
            </g>
          );
        })}
        {/* croce centrale */}
        <line x1={SC_C - 4} y1={SC_C} x2={SC_C + 4} y2={SC_C} stroke="#2A3760" strokeWidth={1} />
        <line x1={SC_C} y1={SC_C - 4} x2={SC_C} y2={SC_C + 4} stroke="#2A3760" strokeWidth={1} />
        {/* contatti (nuovo al centro) */}
        {contacts.map(({ n, x, y }, i) => {
          const hot = (n.relevance ?? 0) >= 8;
          const col = sentColor(n.sentiment);
          const fav = !!n.ticker_mentioned && favSet.has((n.ticker_mentioned || '').toUpperCase());
          const href = externalWebUrl(n.url || '') || undefined;
          const tip = `${hhmm(n)}${n.ticker_mentioned ? ' · ' + n.ticker_mentioned : ''} · ${n.title}`;
          const blip = (
            <g key={n.id ?? i} className={'blip' + (hot ? ' hot' : '')}>
              {fav && <circle cx={x} cy={y} r={hot ? 5.2 : 4} fill="none" stroke="#D4AF37" strokeWidth={0.8} />}
              <circle cx={x} cy={y} r={hot ? 3.4 : 2.2} fill={col} fillOpacity={hot ? 0.95 : 0.75}
                      stroke={hot ? '#FFA51E' : 'none'} strokeWidth={hot ? 0.8 : 0} />
              <title>{tip}</title>
            </g>
          );
          return href
            ? <a key={n.id ?? i} href={href} target="_blank" rel="noopener noreferrer">{blip}</a>
            : blip;
        })}
      </svg>
      {/* sweep: dimensioni in % del quadrante -> scala con .scbox */}
      <div className="swp" />
      </div>
    </div>
  );
}

// COMM TAPE: le ultime headline in nastro scorrevole (contenuto x2 = loop CSS).
function CommTape({ feed }: { feed: FeedItem[] }) {
  const latest = useMemo(
    () => [...feed].sort((a, b) => tsOf(b) - tsOf(a)).slice(0, 18),
    [feed]);
  if (latest.length === 0) return null;
  const run = (dup: number) => latest.map((n, i) => (
    <span key={`${dup}-${n.id ?? i}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span className="tt num">{hhmm(n)}</span>
      {n.ticker_mentioned && <span className="tk">{n.ticker_mentioned}</span>}
      <span className="tl">{n.title}</span>
      <span className="sep">◆</span>
    </span>
  ));
  return (
    <div className="tapex font-mono" title="Ultime 18 headline dal feed DB locale — hover = pausa, click sulle righe del nastro sotto per aprire le fonti">
      <div className="tapein">{run(0)}{run(1)}</div>
    </div>
  );
}

// ============================================================
// MAIN PAGE — NEWS WIRE v3 OBSIDIAN "SIGNAL DECK" (F5)
// ============================================================
export default function NewsPage() {
  // ---- vista: WIRE (default) / DESK (briefing+macro+societario+calendario) ----
  const [view, setView] = useState<'wire' | 'desk'>('wire');

  // ---- WIRE: feed dal DB (get_feed) ----
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [feedLoad, setFeedLoad] = useState(false);
  const [feedErr, setFeedErr] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshInfo, setRefreshInfo] = useState<string | null>(null);
  const [auto, setAuto] = useState(true);
  const [lastFeedAt, setLastFeedAt] = useState<Date | null>(null);
  const [favs, setFavs] = useState<FavCompany[]>([]);

  // ---- filtri wire (combinabili, client-side sul prefetch) ----
  const [fPeriod, setFPeriod] = useState<string>('all');
  const [fThemes, setFThemes] = useState<string[]>([]);
  const [fSent, setFSent] = useState<SentBucket[]>([]);
  const [fRel, setFRel] = useState<number>(0);
  // preselect ticker dal Command Palette (sessionStorage 'bb:newsTicker')
  const [fTickers, setFTickers] = useState<string[]>(() => {
    const t = sessionStorage.getItem('bb:newsTicker');
    if (t) { sessionStorage.removeItem('bb:newsTicker'); return [t.toUpperCase()]; }
    return [];
  });

  // ---- DESK (legacy v0.7) ----
  const [briefing, setBriefing] = useState<BriefingData | null>(null);
  const [briefingLoad, setBriefingLoad] = useState(false);
  const [briefingErr, setBriefingErr] = useState<string | null>(null);

  const [macro, setMacro] = useState<MacroNewsItem[]>([]);
  const [macroLoad, setMacroLoad] = useState(false);
  const [macroErr, setMacroErr] = useState<string | null>(null);
  const [macroCategorySel, setMacroCategorySel] = useState<string[]>([]);
  const [macroDateSel, setMacroDateSel] = useState<string>('week');

  const [corp, setCorp] = useState<CorporateEvent[]>([]);
  const [corpLoad, setCorpLoad] = useState(false);
  const [corpErr, setCorpErr] = useState<string | null>(null);
  const [corpDateSel, setCorpDateSel] = useState<string>('week');
  const [corpTickerSel, setCorpTickerSel] = useState<string[]>([]);

  const [globalNews, setGlobalNews] = useState<GlobalNewsItem[]>([]);
  const [globalLoad, setGlobalLoad] = useState(false);
  const [globalErr, setGlobalErr] = useState<string | null>(null);
  const [globalDateSel, setGlobalDateSel] = useState<string>('24h');

  const [econ, setEcon] = useState<EconomicEvent[]>([]);
  const [econLoad, setEconLoad] = useState(false);
  const [econErr, setEconErr] = useState<string | null>(null);
  const [econCountrySel, setEconCountrySel] = useState<string[]>(['US', 'EU', 'IT', 'DE']);
  const [econImportanceSel, setEconImportanceSel] = useState<number[]>([4, 5]);
  const [econDateSel, setEconDateSel] = useState<string>('week');

  const [lastDeskAt, setLastDeskAt] = useState<Date | null>(null);

  // ---- FONTI MUTE (voce (25) backend, regola no-fallback-silenziosi) ----
  // declared=false finché NESSUN endpoint consumato espone fonti_mute/avviso:
  // in pagina si dichiara "n.d.", mai dedotto. Quando il campo arriva:
  // mute={} = dichiarato "nessun provider muto"; mute={prov:motivo} = buco urlato.
  const [fonti, setFonti] = useState<{ declared: boolean; mute: Record<string, string>; avviso: string | null }>(
    { declared: false, mute: {}, avviso: null });
  const takeFonti = useCallback((d: FontiMuteFields) => {
    // chiave ASSENTE = endpoint che non dichiara (stato invariato);
    // chiave presente (anche null) = dichiarazione del backend, resa com'e'.
    if (!('fonti_mute' in d) && !('avviso' in d)) return;
    setFonti({ declared: true, mute: d.fonti_mute || {}, avviso: d.avviso ?? null });
  }, []);

  // ---- FRESCHEZZA DEL FEED (ponte (68) backend; scelta PM 17/08: cella in
  // testata). Tre verità DISTINTE, mai confuse in un trattino: 'attesa' =
  // prima lettura in volo · 'errore' = endpoint non raggiunto · 'assente' =
  // risponde ma senza `ultimo_giro` (backend più vecchio del contratto 12/08).
  // Il giro, quando c'è, si rende con lo STATO e non solo con l'età: poche
  // news per quota e news fresche sono problemi diversi con la stessa età.
  const [giro, setGiro] = useState<'attesa' | 'errore' | 'assente' | UltimoGiro>('attesa');
  const [nextRun, setNextRun] = useState<string | null>(null);
  const loadGiro = useCallback(async () => {
    try {
      const d = await Bellomberg.newsProviders();
      setGiro(d.ultimo_giro ?? 'assente');
      takeFonti(d);   // il payload porta ANCHE fonti_mute/avviso: accende la cella FONTI MUTE
    } catch { setGiro('errore'); }
  }, [takeFonti]);
  // il «prossimo giro» è NextRunTime del task NewsFeed (il join del ponte):
  // task Disabled o formato imprevisto → null, e in cella si scrive n.d.
  const loadNextRun = useCallback(async () => {
    try {
      const d = await Bellomberg.scheduledTasks();
      const t = (d.tasks || []).find((x: any) => x?.TaskName === 'Bellomberg-NewsFeed');
      const m = t && t.State !== 'Disabled' && typeof t.NextRunTime === 'string'
        ? t.NextRunTime.match(/(\d{2}:\d{2}):\d{2}$/) : null;
      setNextRun(m ? m[1] : null);
    } catch { setNextRun(null); }
  }, []);

  // La cella ULTIMO GIRO FEED: età VIVA dal timestamp (modello FX STALE —
  // invecchia fra un poll e l'altro; ricalcolata a ogni render, e la pagina
  // ri-renderizza coi poll), age_minutes dichiarato come ripiego ETICHETTATO.
  const giroCella = (() => {
    if (giro === 'attesa') return {
      v: '…', col: undefined as string | undefined, sub: 'PRIMA LETTURA IN CORSO',
      tip: 'GET /news/providers: prima lettura in corso',
    };
    if (giro === 'errore') return {
      v: 'n.d.', col: '#FF5C7A', sub: 'ENDPOINT NON RAGGIUNTO',
      tip: 'GET /news/providers non ha risposto: la freschezza del feed non è misurabile — dichiarato, non dedotto',
    };
    if (giro === 'assente') return {
      v: 'n.d.', col: '#FF5C7A', sub: 'CHIAVE ASSENTE DAL BACKEND',
      tip: 'Il backend risponde ma senza `ultimo_giro`: processo più vecchio del contratto (68) del 12/08 — al riavvio la dichiarazione compare da sola',
    };
    const g = giro;
    const ts = g.timestamp ? new Date(g.timestamp).getTime() : NaN;
    const eta = isFinite(ts) ? Math.max(0, Math.round((Date.now() - ts) / 60000))
      : (typeof g.age_minutes === 'number' && isFinite(g.age_minutes) ? Math.round(g.age_minutes) : null);
    const etaTxt = eta == null ? '?' : `${eta}′`;
    const oraTxt = isFinite(ts)
      ? new Date(ts).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' }) : 'n.d.';
    const blocked = g.providers_blocked || {};
    const nBlk = Object.keys(blocked).length;
    const blkTxt = Object.entries(blocked)
      .map(([p, m]) => `${p} (${String(m).replace('SKIP_', '')})`).join(' · ');
    const tip = `Giro delle ${oraTxt}: ${g.fetched ?? 0} lette · ${g.classified ?? 0} classificate · `
      + `${g.saved ?? 0} salvate · ${g.skipped_duplicates ?? 0} doppioni.`
      + (nBlk ? ` Bloccati: ${blkTxt}.` : '')
      + ` Prossimo giro ${nextRun ?? 'n.d.'} (task scheduler).`
      + ' Stato dichiarato dal backend [src: GET /news/providers] — poche news per quota e news fresche sono problemi diversi con la stessa età.';
    if (g.stato === 'ok') return {
      v: `${etaTxt} · OK`, col: undefined,
      sub: `${g.fetched ?? 0} LETTE · ${g.saved ?? 0} SALVATE · PROSSIMO ${nextRun ?? 'n.d.'}`, tip,
    };
    if (g.stato === 'degradato') return {
      v: `${etaTxt} · DEGRADATO`, col: '#FFA51E',
      sub: `${g.fetched ?? 0} LETTE · ${g.saved ?? 0} SALVATE · ${nBlk} ${nBlk === 1 ? 'FONTE' : 'FONTI'} K.O.`, tip,
    };
    // n.d. / illeggibile / valore imprevisto: il motivo VERBATIM, mai un trattino nudo
    return {
      v: 'n.d.', col: '#FF5C7A',
      sub: (g.motivo || `STATO «${String(g.stato)}» NON PREVISTO DAL CONTRATTO`).toUpperCase().slice(0, 42),
      tip: (g.motivo ? `Motivo dal backend, verbatim: ${g.motivo}` : `Stato «${String(g.stato)}» fuori contratto (68)`)
        + ` — ultimo tentativo di lettura noto: ${oraTxt}.`,
    };
  })();

  // ============================================================
  // LOADERS
  // ============================================================
  // Lettura LEGGERA dal DB locale (/news/feed): nessun provider esterno.
  const loadFeed = useCallback(async () => {
    setFeedLoad(true);
    try {
      const d = await Bellomberg.newsFeed({ limit: 200, min_relevance: 0 });
      setFeed((d.items || []) as FeedItem[]);
      setFeedErr(null);
      setLastFeedAt(new Date());
      takeFonti(d);
    } catch (e: any) { setFeedErr(e?.message || 'caricamento feed fallito'); }
    finally { setFeedLoad(false); }
  }, [takeFonti]);

  const loadFavs = useCallback(async () => {
    try { const d = await Bellomberg.favorites(); setFavs(d.favorites || []); }
    catch { /* non bloccante */ }
  }, []);

  // Refresh PESANTE (pull provider + classificazione Haiku): SOLO su bottone.
  const heavyRefresh = useCallback(async () => {
    setRefreshing(true); setRefreshInfo(null);
    try {
      const r = await Bellomberg.newsFeedRefresh(1, true);
      setRefreshInfo(`+${r.saved ?? 0} nuove · ${r.skipped_duplicates ?? 0} dup`);
      takeFonti(r);
      // il refresh manuale E' un giro: lo stato freschezza va riletto
      await Promise.all([loadFeed(), loadFavs(), loadGiro()]);
    } catch (e: any) { setFeedErr(e?.message || 'refresh feed fallito'); }
    finally { setRefreshing(false); }
  }, [loadFeed, loadFavs, takeFonti]);

  const loadBriefing = useCallback(async () => {
    try {
      const d = await Bellomberg.briefingCurrent();
      setBriefing(d); setBriefingErr(d.error || null);
    } catch (e: any) { setBriefingErr(e?.message || 'briefing load failed'); }
  }, []);
  const refreshBriefing = useCallback(async () => {
    setBriefingLoad(true); setBriefingErr(null);
    try { const d = await Bellomberg.briefingRefresh(); setBriefing(d); if (d.error) setBriefingErr(d.error); }
    catch (e: any) { setBriefingErr(e?.message || 'briefing refresh failed'); }
    finally { setBriefingLoad(false); }
  }, []);
  const loadMacro = useCallback(async (cats: string[] = []) => {
    setMacroLoad(true); setMacroErr(null);
    try {
      const d = await Bellomberg.newsMacro({
        categories: cats.length > 0 ? cats.join(',') : undefined,
        min_importance: 3, days: 2, max_per_topic: 3, include_reddit: false,
      });
      setMacro(d.items || []);
      takeFonti(d);
    } catch (e: any) { setMacroErr(e?.message || 'macro news failed'); }
    finally { setMacroLoad(false); }
  }, [takeFonti]);
  const loadCorporate = useCallback(async () => {
    setCorpLoad(true); setCorpErr(null);
    try { const d = await Bellomberg.newsCorporateEvents(30, 50); setCorp(d.items || []); takeFonti(d); }
    catch (e: any) { setCorpErr(e?.message || 'corporate events failed'); }
    finally { setCorpLoad(false); }
  }, [takeFonti]);
  const loadGlobal = useCallback(async () => {
    setGlobalLoad(true); setGlobalErr(null);
    try { const d = await Bellomberg.newsTopGlobal(30); setGlobalNews(d.items || []); takeFonti(d); }
    catch (e: any) { setGlobalErr(e?.message || 'global news failed'); }
    finally { setGlobalLoad(false); }
  }, [takeFonti]);
  const loadEcon = useCallback(async () => {
    setEconLoad(true); setEconErr(null);
    try { const d = await Bellomberg.economicCalendar(14); setEcon(d.items || []); }
    catch (e: any) { setEconErr(e?.message || 'economic calendar failed'); }
    finally { setEconLoad(false); }
  }, []);
  const refreshAllDesk = useCallback(async () => {
    await Promise.all([loadBriefing(), loadMacro(macroCategorySel),
                       loadCorporate(), loadGlobal(), loadEcon()]);
    setLastDeskAt(new Date());
  }, [loadBriefing, loadMacro, loadCorporate, loadGlobal, loadEcon, macroCategorySel]);

  // ============================================================
  // EFFECTS
  // ============================================================
  // Mount + poll 5 min della vista DESK (cadenza identica alla v0.7:
  // NESSUN aumento di frequenza verso i provider esterni).
  useEffect(() => {
    loadFeed(); loadFavs(); refreshAllDesk(); loadGiro(); loadNextRun();
    const id = setInterval(() => { loadFeed(); refreshAllDesk(); loadGiro(); loadNextRun(); }, 5 * 60 * 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // AUTO 60s: SOLO letture leggere — get_feed (DB) e lo stato giro (file
  // json via /news/providers), nessun provider esterno.
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => { loadFeed(); loadGiro(); }, 60 * 1000);
    return () => clearInterval(id);
  }, [auto, loadFeed, loadGiro]);

  // cambio categorie macro -> ricarica server-side (skip primo render)
  const firstMacroRun = useRef(true);
  useEffect(() => {
    if (firstMacroRun.current) { firstMacroRun.current = false; return; }
    loadMacro(macroCategorySel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [macroCategorySel]);

  // ============================================================
  // DERIVED — WIRE
  // ============================================================
  const favSet = useMemo(
    () => new Set(favs.map(f => (f.ticker || '').toUpperCase()).filter(Boolean)),
    [favs]);

  const feedTickerCounts = useMemo(() => {
    const m: Record<string, number> = {};
    feed.forEach(n => {
      const t = (n.ticker_mentioned || '').toUpperCase();
      if (t) m[t] = (m[t] || 0) + 1;
    });
    return m;
  }, [feed]);

  const tickerOptions = useMemo(() => {
    const m = { ...feedTickerCounts };
    fTickers.forEach(t => { if (!(t in m)) m[t] = 0; });
    return Object.entries(m).map(([ticker, count]) => ({ ticker, count }))
      .sort((a, b) => b.count - a.count || a.ticker.localeCompare(b.ticker));
  }, [feedTickerCounts, fTickers]);

  const themeOptions = useMemo(() => {
    const counts: Record<string, number> = {};
    feed.forEach(n => { const k = themeKeyOf(n); counts[k] = (counts[k] || 0) + 1; });
    return Object.entries(counts)
      .map(([key, count]) => ({ key, count, label: themeLabelOf(key) }))
      .sort((a, b) => b.count - a.count);
  }, [feed]);

  const filtered = useMemo(() => {
    return feed.filter(n => {
      if (!matchesDatePresetNews(n.published_at || n.pulled_at, fPeriod)) return false;
      if (fThemes.length > 0 && !fThemes.includes(themeKeyOf(n))) return false;
      if (fSent.length > 0 && !fSent.includes(sentBucket(n.sentiment))) return false;
      if (fRel > 0 && (n.relevance ?? 0) < fRel) return false;
      if (fTickers.length > 0 && !fTickers.includes((n.ticker_mentioned || '').toUpperCase())) return false;
      return true;
    });
  }, [feed, fPeriod, fThemes, fSent, fRel, fTickers]);

  // nastro cronologico raggruppato per giorno (OGGI / IERI / data)
  const dayGroups = useMemo(() => {
    const sorted = [...filtered].sort((a, b) => tsOf(b) - tsOf(a));
    const map = new Map<string, FeedItem[]>();
    for (const n of sorted) {
      const k = dayKeyFromTs(tsOf(n));
      const arr = map.get(k);
      if (arr) arr.push(n); else map.set(k, [n]);
    }
    return Array.from(map.entries());
  }, [filtered]);

  // fascia MARKET MOVING preservata (relevance >= 8), rank per _materiality
  const marketMoving = useMemo(
    () => feed.filter(n => (n.relevance ?? 0) >= 8)
              .sort((a, b) => (b._materiality ?? 0) - (a._materiality ?? 0))
              .slice(0, 6),
    [feed]);

  const stats = useMemo<WireStats>(() => {
    const now = Date.now();
    let pos = 0, neu = 0, neg = 0, h24 = 0, h6 = 0, relSum = 0, relN = 0;
    const themes: Record<string, number> = {};
    const tickers = new Set<string>();
    const providers = new Set<string>();
    for (const n of feed) {
      const b = sentBucket(n.sentiment);
      if (b === 'pos') pos++; else if (b === 'neg') neg++; else neu++;
      const t = tsOf(n);
      if (t > 0) {
        const ageH = (now - t) / 3600_000;
        if (ageH <= 24) h24++;
        if (ageH <= 6) h6++;
      }
      const k = themeKeyOf(n);
      themes[k] = (themes[k] || 0) + 1;
      if (n.ticker_mentioned) tickers.add(n.ticker_mentioned.toUpperCase());
      if (n.provider) providers.add(n.provider);
      if (n.relevance != null) { relSum += n.relevance; relN++; }
    }
    const topThemes = Object.entries(themes)
      .map(([key, count]) => ({ key, label: themeLabelOf(key), count }))
      .sort((a, b) => b.count - a.count).slice(0, 6);
    return { pos, neu, neg, h24, h6, total: feed.length, topThemes,
             nTickers: tickers.size, nProviders: providers.size,
             avgRel: relN > 0 ? relSum / relN : 0 };
  }, [feed]);

  // MATRICE CANALI: provider visti nel feed (count + ultimo item) fusi con la
  // dichiarazione fonti_mute (25); un provider muto ASSENTE dal feed compare
  // comunque a 0 — il buco si vede, non sparisce (lezione search_portfolio_news).
  const channels = useMemo<Channel[]>(() => {
    const m: Record<string, { count: number; last: number }> = {};
    for (const n of feed) {
      const p = (n.provider || '?').toLowerCase();
      const t = tsOf(n);
      if (!m[p]) m[p] = { count: 0, last: 0 };
      m[p].count++;
      if (t > m[p].last) m[p].last = t;
    }
    const out: Channel[] = Object.entries(m).map(([name, v]) => ({
      name, count: v.count, last: v.last,
      muteReason: fonti.mute[name] ?? null,
    }));
    for (const [p, motivo] of Object.entries(fonti.mute)) {
      if (!(p.toLowerCase() in m)) out.push({ name: p.toLowerCase(), count: 0, last: 0, muteReason: String(motivo) });
    }
    return out.sort((a, b) => b.count - a.count);
  }, [feed, fonti.mute]);

  const nActiveFilters = (fPeriod !== 'all' ? 1 : 0) + fThemes.length + fSent.length
                       + (fRel > 0 ? 1 : 0) + fTickers.length;
  const resetFilters = useCallback(() => {
    setFPeriod('all'); setFThemes([]); setFSent([]); setFRel(0); setFTickers([]);
  }, []);

  // ============================================================
  // DERIVED — DESK (legacy)
  // ============================================================
  const filteredMacro = useMemo(() => {
    return macro.filter(n => matchesDatePresetNews(n.published_at, macroDateSel)).slice(0, 100);
  }, [macro, macroDateSel]);

  const corpTickers = useMemo(() => {
    const counts: Record<string, number> = {};
    corp.forEach(e => {
      const t = e.ticker_mentioned;
      if (t) counts[t] = (counts[t] || 0) + 1;
    });
    return Object.entries(counts).map(([ticker, count]) => ({ ticker, count })).sort((a, b) => b.count - a.count);
  }, [corp]);

  const filteredCorp = useMemo(() => {
    let arr = corp;
    if (corpTickerSel.length > 0)
      arr = arr.filter(e => e.ticker_mentioned && corpTickerSel.includes(e.ticker_mentioned));
    arr = arr.filter(e => matchesDatePresetNews(e.published_at, corpDateSel));
    return arr;
  }, [corp, corpTickerSel, corpDateSel]);

  const filteredGlobal = useMemo(() => {
    return globalNews.filter(n => matchesDatePresetNews(n.published_at, globalDateSel));
  }, [globalNews, globalDateSel]);

  const filteredEcon = useMemo(() => {
    return econ.filter(e => {
      if (isHoliday(e.title || '')) return false;
      if (!matchesDatePresetCal(e.date, econDateSel)) return false;
      if (econImportanceSel.length > 0 && !econImportanceSel.includes(e.importance)) return false;
      if (econCountrySel.length > 0 && !econCountrySel.includes((e.country || '').toUpperCase())) return false;
      return true;
    });
  }, [econ, econCountrySel, econImportanceSel, econDateSel]);

  const econByDate = useMemo(() => {
    const m: Record<string, EconomicEvent[]> = {};
    for (const e of filteredEcon) {
      const k = e.date;
      if (!m[k]) m[k] = [];
      m[k].push(e);
    }
    return m;
  }, [filteredEcon]);

  const countryCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    econ.forEach(e => {
      if (isHoliday(e.title || '')) return;
      const c = (e.country || '').toUpperCase();
      if (c) counts[c] = (counts[c] || 0) + 1;
    });
    return counts;
  }, [econ]);

  const deskBusy = briefingLoad || macroLoad || corpLoad || globalLoad || econLoad;
  const nMute = Object.keys(fonti.mute).length;
  const tot3 = Math.max(1, stats.pos + stats.neu + stats.neg);

  // ============================================================
  // RENDER
  // ============================================================
  return (
    <div className="obsx bootx h-full min-h-0 overflow-hidden animate-fadeIn">

      {/* ══ HERO "SIGNAL DECK": traffico wire in vetrina + copertura DICHIARATA + comandi ══ */}
      <div className="p3 hero scanx" style={{ flexShrink: 0 }}>
        <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
        <div className="p3h am">NEWS // SIGNAL DECK
          <span className="n">· WIRE & DESK · FEED DB LOCALE · PROVIDER SOLO SU REFRESH</span>
          <span className="side num">
            {feed.length} ITEM · AGG {lastFeedAt ? lastFeedAt.toLocaleTimeString('it-IT', { hour12: false }) : '--:--:--'}
            {refreshInfo ? ' · ' + refreshInfo : ''}
            {view === 'desk'
              ? ' · DESK AUTO 5M' + (lastDeskAt ? ' · AGG ' + lastDeskAt.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' }) : '')
              : auto ? ' · AUTO 60S' : ''}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'stretch', flexWrap: 'wrap', minHeight: 74 }}>
          {/* vetrina: SIGNAL TRAFFIC — istogramma orario 24h + polso sentiment */}
          <div style={{ padding: '9px 16px 11px' }}>
            <div style={{ fontSize: 9, letterSpacing: '.22em', fontWeight: 600, color: '#73829F', textTransform: 'uppercase' }}>SIGNAL TRAFFIC // ULTIME 24H</div>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, marginTop: 3 }}>
              <div className="num" style={{ fontSize: 22, fontWeight: 700, lineHeight: 1.1, color: '#ECF1FA', whiteSpace: 'nowrap' }}
                   title="Item del feed DB locale con timestamp nelle ultime 24h: misura il flusso SALVATO, non il mercato — lo stato quota provider è nella cella FONTI MUTE">
                {stats.h24}<span style={{ fontSize: 12, fontWeight: 600, marginLeft: 6, color: '#8D9FC4' }}>ITEM</span>
              </div>
              <FlowBars feed={feed} width={200} />
            </div>
            <div style={{ width: 260, height: 3, marginTop: 6, display: 'flex', background: 'rgba(26,36,64,.9)' }}
                 title={`Polso sentiment sull'intero feed: POS ${stats.pos} / NEU ${stats.neu} / NEG ${stats.neg}`}>
              <span style={{ display: 'block', width: `${(stats.pos / tot3) * 100}%`, background: C.emerald }} />
              <span style={{ display: 'block', width: `${(stats.neu / tot3) * 100}%`, background: '#2A3760' }} />
              <span style={{ display: 'block', width: `${(stats.neg / tot3) * 100}%`, background: C.crimson }} />
            </div>
            <div className="num" style={{ fontSize: 9, fontWeight: 600, color: '#73829F', marginTop: 4, letterSpacing: '.1em', textTransform: 'uppercase' }}>
              6H {stats.h6} · TOT {stats.total} · ISTOGRAMMA ITEM/ORA · POLSO POS/NEU/NEG
            </div>
          </div>
          <HeroCell label="MARKET MOVING" value={String(marketMoving.length)}
                    color={marketMoving.length > 0 ? '#FFA51E' : undefined}
                    sub="REL ≥ 8 · RANK MATERIALITY"
                    title="Catalyst con relevance ≥ 8, ordinati per materiality (relevance + peso book + freschezza + boost preferito)" />
          <HeroCell label="COPERTURA FEED" value={`${stats.nTickers} TKR`}
                    sub={`${stats.nProviders} FONTI VIVE · REL MEDIA ${stats.avgRel.toFixed(1)}`}
                    title="Ticker distinti e provider presenti negli item caricati (conteggio client-side sul prefetch)" />
          <HeroCell label="FONTI MUTE"
                    value={!fonti.declared ? 'n.d.' : nMute === 0 ? 'NESSUNA' : String(nMute)}
                    tone={!fonti.declared ? undefined : nMute === 0 ? 'up' : 'dn'}
                    sub={!fonti.declared ? 'DICHIARAZIONE NON ESPOSTA QUI'
                         : nMute === 0 ? 'DICHIARATO DAL BACKEND'
                         : Object.keys(fonti.mute).sort().join(' · ').slice(0, 34).toUpperCase()}
                    title={!fonti.declared
                      ? 'Gli endpoint consumati da questa pagina non espongono ancora fonti_mute/avviso (voce (25) backend: oggi solo /news/ticker, /news/search, /news/portfolio): n.d. DICHIARATO, mai dedotto. Coordinamento richiesto alla chat backend in (F5).'
                      : nMute === 0
                        ? 'Il backend dichiara: nessun provider fuori per budget/disable. NB: il cooldown per-query resta fuori dal conteggio (scelta (25)).'
                        : 'Provider fuori per budget/disable dichiarati dal backend: poche/zero news NON significa quiete.'} />
          {/* FRESCHEZZA (ponte (68), impianto A scelto dal PM 17/08 sulla rosa
              in situ): stato + età, MAI solo l'età; il verbale pieno nel title */}
          <HeroCell label="ULTIMO GIRO FEED" grado10
                    value={giroCella.v} color={giroCella.col}
                    sub={giroCella.sub} title={giroCella.tip} />
          <div style={{ marginLeft: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', justifyContent: 'center', gap: 6, padding: '8px 14px' }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              <span className="tfg">
                <button className={'tb' + (view === 'wire' ? ' on' : '')} onClick={() => setView('wire')}>WIRE</button>
                <button className={'tb' + (view === 'desk' ? ' on' : '')} onClick={() => setView('desk')}>DESK</button>
              </span>
              <button className={'tb' + (auto ? ' on' : '')} onClick={() => setAuto(a => !a)}
                      title="Poll del solo get_feed (DB locale) ogni 60s — nessun provider esterno">
                AUTO 60S
              </button>
              {view === 'wire' ? (
                <button className={'tb' + (refreshing ? ' dis' : '')} onClick={heavyRefresh} disabled={refreshing}
                        style={{ color: '#FFA51E' }}
                        title="Pull pesante dai provider + classificazione Haiku (solo manuale)">
                  {refreshing ? 'PULL IN CORSO…' : '↻ REFRESH PROVIDER'}
                </button>
              ) : (
                <button className={'tb' + (deskBusy ? ' dis' : '')} onClick={refreshAllDesk} disabled={deskBusy}
                        style={{ color: '#FFA51E' }}>
                  {deskBusy ? 'CARICO…' : '↻ REFRESH ALL'}
                </button>
              )}
            </div>
            <div className="num" style={{ fontSize: 9, fontWeight: 600, color: '#73829F', letterSpacing: '.1em', textTransform: 'uppercase' }}>
              {view === 'wire'
                ? `${filtered.length}/${feed.length} nel nastro · ${nActiveFilters} filtri attivi`
                : `briefing + macro + societario + calendario + global`}
            </div>
          </div>
        </div>
      </div>

      {/* ══ COMM TAPE: nastro headline live (dati veri, hover=pausa) ══ */}
      <CommTape feed={feed} />

      {/* ══ AVVISO COPERTURA (reso SOLO se il backend lo dichiara: mai dedotto) ══ */}
      {fonti.declared && (nMute > 0 || fonti.avviso) && (
        <div className="border border-amber/50 bg-amber/5 px-3 py-2 font-mono text-2xs text-amber flex items-start gap-2"
             style={{ flexShrink: 0 }}>
          <AlertCircle size={12} className="mt-0.5 shrink-0" />
          <span>
            <b>COPERTURA PARZIALE — FONTI MUTE: </b>
            {Object.entries(fonti.mute).map(([p, m]) => `${p} (${String(m).replace('SKIP_', '')})`).join(' · ') || '—'}
            {fonti.avviso ? ` — ${fonti.avviso}` : ''}
          </span>
        </div>
      )}

      {view === 'wire' ? (
        /* ================= VISTA WIRE: rail filtri | nastro | destra + runline ================= */
        <>
        <div className="nwir flex-1 flex gap-2 overflow-hidden min-h-0 animate-fadeIn">
          {/* (a) RAIL FILTRI */}
          <div className="w-44 shrink-0 p3" style={bd(60)}>
            <div className="p3h"><Filter size={10} /> FILTRI
              {nActiveFilters > 0 && (
                <span className="side">
                  <button onClick={resetFilters}
                          className="font-mono text-3xs text-amber hover:text-amber-bright transition-colors">
                    AZZERA ({nActiveFilters})
                  </button>
                </span>
              )}
            </div>
            <div className="flex-1 overflow-y-auto p-1.5 space-y-3 min-h-0">
              <div>
                <RailLabel>Periodo</RailLabel>
                <div className="space-y-px">
                  {DATE_PRESETS_NEWS.map(p => (
                    <RailChip key={p.value} on={fPeriod === p.value} label={p.label}
                              onClick={() => setFPeriod(p.value)} />
                  ))}
                </div>
              </div>

              <div>
                <RailLabel>Categorie</RailLabel>
                <div className="space-y-px">
                  {themeOptions.length === 0 && (
                    <div className="px-1.5 font-mono text-3xs text-faint">nessun tema</div>
                  )}
                  {themeOptions.map(t => (
                    <RailChip key={t.key} on={fThemes.includes(t.key)} label={t.label} count={t.count}
                              onClick={() => toggleIn(fThemes, t.key, setFThemes)} />
                  ))}
                </div>
              </div>

              <div>
                <RailLabel>Sentiment</RailLabel>
                <div className="grid grid-cols-3 gap-1">
                  {SENT_FILTERS.map(s => {
                    const on = fSent.includes(s.key);
                    return (
                      <button key={s.key}
                        onClick={() => toggleIn(fSent, s.key, setFSent)}
                        className="font-mono text-3xs py-1 rounded-sm border text-center transition-colors"
                        style={on
                          ? { borderColor: s.color, color: s.color, background: s.color + '1f' }
                          : { borderColor: '#1A2440', color: C.muted }}>
                        {s.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <RailLabel>Relevance</RailLabel>
                <div className="grid grid-cols-3 gap-1">
                  {REL_FILTERS.map(r => {
                    const on = fRel === r.value;
                    return (
                      <button key={r.value}
                        onClick={() => setFRel(r.value)}
                        className={`font-mono text-3xs py-1 rounded-sm border text-center transition-colors
                                    ${on ? 'border-amber/60 text-amber bg-amber/15' : 'border-border text-muted hover:text-text-dim'}`}>
                        {r.label.toUpperCase()}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <RailLabel>Ticker nel feed</RailLabel>
                <div className="space-y-px max-h-44 overflow-y-auto pr-0.5">
                  {tickerOptions.length === 0 && (
                    <div className="px-1.5 font-mono text-3xs text-faint">nessun ticker</div>
                  )}
                  {tickerOptions.slice(0, 40).map(t => (
                    <RailChip key={t.ticker} on={fTickers.includes(t.ticker)} label={t.ticker} count={t.count}
                              star={favSet.has(t.ticker)}
                              onClick={() => toggleIn(fTickers, t.ticker, setFTickers)} />
                  ))}
                </div>
              </div>

              <div>
                <RailLabel>Preferiti PM</RailLabel>
                <div className="space-y-px max-h-44 overflow-y-auto pr-0.5">
                  {favs.length === 0 && (
                    <div className="px-1.5 font-mono text-3xs text-faint">nessun preferito</div>
                  )}
                  {favs.map(f => {
                    const tk = (f.ticker || '').toUpperCase();
                    if (!tk) return null;
                    return (
                      <RailChip key={tk} on={fTickers.includes(tk)} label={tk}
                                count={feedTickerCounts[tk] || 0} star
                                onClick={() => toggleIn(fTickers, tk, setFTickers)} />
                    );
                  })}
                </div>
              </div>
            </div>
          </div>

          {/* (b) NASTRO WIRE */}
          <div className="nwmain flex-1 min-w-0 p3" style={bd(120)}>
            <div className="p3h am">WIRE // NASTRO CRONOLOGICO
              <span className="n num">{filtered.length}/{feed.length} ITEM</span>
              {feedLoad && <RefreshCw size={10} className="animate-spin text-amber" />}
              <span className="side hidden xl:block">CLICK RIGA = APRI FONTE</span>
            </div>
            <div className="flex-1 overflow-y-auto min-h-0">
              {feedErr ? (
                <div className="p-4">
                  <ErrorBox msg={feedErr} />
                  <div className="text-center mt-2">
                    <button onClick={loadFeed} className="btn btn-amber text-3xs px-3 py-1">RIPROVA</button>
                  </div>
                </div>
              ) : feed.length === 0 && (feedLoad || refreshing) ? (
                <LoadingBox label="Caricamento wire dal DB..." />
              ) : feed.length === 0 ? (
                <div className="p-8 text-center font-mono text-2xs text-faint">
                  <div className="mb-3">WIRE VUOTO — nessun item nel DB locale.</div>
                  <button onClick={heavyRefresh} disabled={refreshing}
                          className="btn btn-amber text-3xs px-3 py-1 disabled:opacity-50">
                    <RefreshCw size={10} className={refreshing ? 'animate-spin' : ''} /> PULL DAI PROVIDER
                  </button>
                </div>
              ) : filtered.length === 0 ? (
                <div className="p-8 text-center font-mono text-2xs text-faint">
                  <div className="mb-3">Nessun item con i filtri attivi.</div>
                  <button onClick={resetFilters} className="btn btn-amber text-3xs px-3 py-1">AZZERA FILTRI</button>
                </div>
              ) : (
                dayGroups.map(([k, items]) => (
                  <div key={k}>
                    <div className="sticky top-0 z-10 px-2 py-1 bg-bg-elev border-y border-border
                                    font-mono text-3xs tracking-[0.25em] text-amber flex items-center justify-between">
                      <span>{dayLabel(k)}</span>
                      <span className="text-faint tabular-nums">{items.length}</span>
                    </div>
                    {items.map((n, i) => (
                      <WireRow key={n.id ?? `${k}-${i}`} n={n}
                               fav={!!n.ticker_mentioned && favSet.has((n.ticker_mentioned || '').toUpperCase())} />
                    ))}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* (c) COLONNA DESTRA: SCOPE + MARKET MOVING + CANALI */}
          <div className="nwright w-72 shrink-0 flex flex-col gap-2 overflow-hidden min-h-0">
            <div className="p3 scpx cy shrink-0" style={bd(150)}>
              <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
              <div className="p3h">SCOPE // CONTATTI 24H
                <span className="side num">{feed.filter(n => { const t = tsOf(n); return t > 0 && Date.now() - t < 86_400_000; }).length} BLIP</span>
              </div>
              <RadarScope feed={feed} favSet={favSet} />
              <div className="scleg num">SETTORE=TEMA · RAGGIO=ETÀ (1H/6H/24H) · COLORE=SENTIMENT · ALONE=REL≥8 · ANELLO ORO=PREFERITO · CLICK=FONTE</div>
            </div>
            <div className="p3 flex-1 min-h-0" style={{ ...bd(180), borderLeft: '2px solid rgba(255,165,30,.55)' }}>
              <div className="p3h am"><Zap size={11} /> MARKET MOVING
                <span className="n">REL ≥ 8</span>
                <span className="side"><span className={`led ${marketMoving.length > 0 ? 'led-amber pulse-dot' : 'led-off'}`} /></span>
              </div>
              <div className="overflow-y-auto min-h-0">
                {marketMoving.length === 0
                  ? <EmptyBox label="Nessun catalyst con REL >= 8." />
                  : marketMoving.map((n, i) => (
                      <MoverRow key={n.id ?? i} n={n} rank={i + 1}
                                fav={!!n.ticker_mentioned && favSet.has((n.ticker_mentioned || '').toUpperCase())} />
                    ))}
              </div>
            </div>
            <ChannelsPanel stats={stats} channels={channels} fontiDeclared={fonti.declared} />
          </div>
        </div>

        {/* runline di stato del link wire (idioma SECURE CHANNEL della pagina di accesso) */}
        <div className="runline num" style={{ flexShrink: 0 }}>
          <span className={`led ${auto ? 'led-cyan pulse-dot' : 'led-off'}`} />
          WIRE LINK · /news/feed DB LOCALE {auto ? '· POLL 60S ATTIVO' : '· POLL SOSPESO'} · BUFFER 200 · PROVIDER SOLO SU REFRESH MANUALE
          <span style={{ marginLeft: 'auto' }}>
            AGG {lastFeedAt ? lastFeedAt.toLocaleTimeString('it-IT', { hour12: false }) : '--:--:--'}
          </span>
        </div>
        </>
      ) : (
        /* ================= VISTA DESK: briefing / macro / societario / calendario ================= */
        <div className="nwdesk flex-1 grid grid-cols-12 gap-2 overflow-hidden min-h-0 animate-fadeIn">
          {/* col 1: briefing */}
          <div className="col-span-4 flex flex-col gap-2 overflow-hidden min-h-0">
            <BriefingCard data={briefing} loading={briefingLoad} error={briefingErr} onRefresh={refreshBriefing} />
          </div>

          {/* col 2: macro + eventi societari */}
          <div className="col-span-4 flex flex-col gap-2 overflow-hidden min-h-0">
            <div className="p3 flex-1 min-h-0" style={bd(120)}>
              <PanelHeader
                icon={Globe} title="Macro & Geopolitica" count={filteredMacro.length}
                action={macroLoad && <RefreshCw size={10} className="animate-spin text-amber" />}
              />
              <div className="px-2 py-2 border-b border-border bg-bg/50 flex gap-2 flex-shrink-0">
                <DropdownSingle
                  label="Periodo" options={DATE_PRESETS_NEWS}
                  selected={macroDateSel} onChange={setMacroDateSel}
                />
                <Dropdown
                  label="Categoria"
                  options={Object.entries(CATEGORY_LABELS).map(([k, v]) => ({ value: k, label: v }))}
                  selected={macroCategorySel}
                  onChange={setMacroCategorySel}
                  allLabel="Tutte"
                />
              </div>
              <div className="flex-1 overflow-y-auto min-h-0">
                {macroErr ? <ErrorBox msg={macroErr} /> :
                 filteredMacro.length === 0 && macroLoad ? <LoadingBox label="Caricamento macro news... (~30s)" /> :
                 filteredMacro.length === 0 ? <EmptyBox label="Nessuna news macro con questi filtri." /> :
                 filteredMacro.map((n, i) => {
                   const CatIcon = categoryIcon(n.topic_category);
                   return (
                     <NewsRow key={i}
                       title={n.title} snippet={n.snippet} url={n.url}
                       provider={n.provider} source={n.source}
                       published_at={n.published_at} importance={n.topic_importance}
                       badges={
                         <>
                           <span className="text-3xs font-mono px-1 rounded-sm flex items-center gap-1"
                                 style={{ background: importanceTone(n.topic_importance) + '14',
                                          color: importanceTone(n.topic_importance) }}>
                             <CatIcon size={9} />{n.topic_label?.slice(0, 16) || 'macro'}
                           </span>
                           {n.tickers_affected && n.tickers_affected.length > 0 && (
                             <span className="text-3xs font-mono text-amber/80">
                               {n.tickers_affected.slice(0, 3).join(',')}
                             </span>
                           )}
                         </>
                       } />
                   );
                 })}
              </div>
            </div>

            <div className="p3 flex-1 min-h-0" style={bd(180)}>
              <PanelHeader
                icon={Building2} title="Eventi Societari" count={filteredCorp.length}
                action={corpLoad && <RefreshCw size={10} className="animate-spin text-amber" />}
              />
              <div className="px-2 py-2 border-b border-border bg-bg/50 flex gap-2 flex-shrink-0">
                <DropdownSingle
                  label="Periodo" options={DATE_PRESETS_NEWS}
                  selected={corpDateSel} onChange={setCorpDateSel}
                />
                <Dropdown
                  label="Ticker"
                  options={corpTickers.map(t => ({ value: t.ticker, label: t.ticker, count: t.count }))}
                  selected={corpTickerSel}
                  onChange={setCorpTickerSel}
                  allLabel={`Tutti (${corp.length})`}
                />
              </div>
              <div className="flex-1 overflow-y-auto min-h-0">
                {corpErr ? <ErrorBox msg={corpErr} /> :
                 filteredCorp.length === 0 && corpLoad ? <LoadingBox label="Caricamento SEC + Finnhub..." /> :
                 filteredCorp.length === 0 ? <EmptyBox label="Nessun evento con questi filtri." /> :
                 filteredCorp.map((e, i) => (
                   <NewsRow key={i}
                     title={e.title} snippet={e.snippet} url={e.url}
                     provider={e.provider} published_at={e.published_at}
                     ticker={e.ticker_mentioned} importance={e.topic_importance}
                     badges={e.event_type ? (
                       <span className="text-3xs font-mono px-1 rounded-sm flex items-center gap-1"
                             style={{ background: importanceTone(e.topic_importance) + '14',
                                      color: importanceTone(e.topic_importance) }}>
                         <FileText size={9} />{e.event_type}
                       </span>
                     ) : undefined} />
                 ))}
              </div>
            </div>
          </div>

          {/* col 3: calendario economico + top global */}
          <div className="col-span-4 flex flex-col gap-2 overflow-hidden min-h-0">
            <div className="p3 cy min-h-0" style={{ ...bd(240), flex: '1.5 1 0' }}>
              <PanelHeader
                icon={Calendar} title="Economic Calendar" count={filteredEcon.length}
                action={econLoad && <RefreshCw size={10} className="animate-spin text-amber" />}
              />
              <div className="px-2 py-2 border-b border-border bg-bg/50 flex gap-2 flex-wrap flex-shrink-0">
                <DropdownSingle
                  label="Data" options={DATE_PRESETS_CAL}
                  selected={econDateSel} onChange={setEconDateSel}
                />
                <Dropdown
                  label="Importanza"
                  options={IMPORTANCE_LEVELS.map(l => ({ value: l.value, label: l.label }))}
                  selected={econImportanceSel} onChange={setEconImportanceSel}
                  allLabel="Qualsiasi"
                />
                <Dropdown
                  label="Paese"
                  options={COUNTRIES_AVAILABLE.map(c => ({ value: c, label: c, count: countryCounts[c] }))}
                  selected={econCountrySel} onChange={setEconCountrySel}
                  allLabel="Tutti i paesi"
                />
              </div>
              <div className="flex-1 overflow-y-auto min-h-0">
                {econErr ? (
                  <ErrorBox msg={econErr} />
                ) : Object.keys(econByDate).length === 0 ? (
                  <EmptyBox label="Nessun evento con questi filtri." />
                ) : (
                  Object.entries(econByDate).map(([date, events]) => (
                    <div key={date} className="border-b border-border/40">
                      <div className="px-3 py-1 bg-bg-elev text-3xs font-mono text-amber uppercase tracking-wider sticky top-0">
                        {date}
                      </div>
                      {events.map((e, j) => {
                        const hasData = e.previous != null || e.estimate != null || e.actual != null;
                        const beat = e.actual != null && e.estimate != null
                          ? (Number(e.actual) > Number(e.estimate) ? 'beat'
                            : Number(e.actual) < Number(e.estimate) ? 'miss' : 'inline') : null;
                        const beatColor = beat === 'beat' ? C.emerald : beat === 'miss' ? C.crimson : C.muted;
                        return (
                          <div key={j} className="px-2 py-1 hover:bg-bg-elev/50 border-b border-border/20">
                            <div className="flex items-center gap-1">
                              <span className="text-3xs font-mono text-faint w-12">{e.time}</span>
                              <span className="text-3xs font-mono px-1 rounded-sm"
                                    style={{ background: importanceTone(e.importance) + '14',
                                             color: importanceTone(e.importance) }}>
                                {'*'.repeat(Math.min(5, e.importance))}
                              </span>
                              <span className="text-3xs font-mono text-amber">{e.country}</span>
                              {beat && (
                                <span className="text-3xs font-mono font-bold ml-auto" style={{ color: beatColor }}>
                                  {beat === 'beat' ? '+ BEAT' : beat === 'miss' ? '- MISS' : '= INLINE'}
                                </span>
                              )}
                            </div>
                            <div className="text-2xs text-text leading-tight mt-0.5">{e.title}</div>
                            {hasData && (
                              <div className="flex gap-3 mt-0.5 text-3xs font-mono tabular-nums">
                                <span className="text-faint">prev <span className="text-text-dim">{e.previous != null ? String(e.previous) : '-'}{e.unit || ''}</span></span>
                                <span className="text-faint">est <span style={{ color: C.cyan }}>{e.estimate != null ? String(e.estimate) : '-'}{e.unit || ''}</span></span>
                                <span className="text-faint">act <span style={{ color: beat === 'beat' ? C.emerald : beat === 'miss' ? C.crimson : C.amberBright, fontWeight: 700 }}>{e.actual != null ? String(e.actual) : '-'}{e.unit || ''}</span></span>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="p3 vi flex-1 min-h-0" style={bd(300)}>
              <PanelHeader
                icon={Globe} title="Top Global News" count={filteredGlobal.length}
                action={globalLoad && <RefreshCw size={10} className="animate-spin text-amber" />}
              />
              <div className="px-2 py-2 border-b border-border bg-bg/50 flex gap-2 flex-shrink-0">
                <DropdownSingle
                  label="Periodo" options={DATE_PRESETS_NEWS}
                  selected={globalDateSel} onChange={setGlobalDateSel}
                />
              </div>
              <div className="flex-1 overflow-y-auto min-h-0">
                {globalErr ? <ErrorBox msg={globalErr} /> :
                 filteredGlobal.length === 0 && globalLoad ? <LoadingBox /> :
                 filteredGlobal.length === 0 ? <EmptyBox label="Nessuna top news con questi filtri." /> :
                 filteredGlobal.map((n, i) => (
                   <NewsRow key={i}
                     title={n.title} snippet={n.snippet} url={n.url}
                     provider={n.provider} source={n.source}
                     published_at={n.published_at} />
                 ))}
              </div>
            </div>
          </div>
        </div>
      )}

      <style>{`
        .news-briefing-md h2 {
          color: #FFA51E; font-size: 11px; font-weight: 700;
          text-transform: uppercase; letter-spacing: 0.1em;
          margin: 10px 0 4px 0;
          border-bottom: 1px solid rgba(255, 165, 30, 0.22); padding-bottom: 2px;
        }
        .news-briefing-md h2:first-child { margin-top: 0; }
        .news-briefing-md p {
          font-size: 11px; line-height: 1.6; color: #ECF1FA; margin: 4px 0 8px 0;
        }
        .news-briefing-md strong { color: #FFC555; font-weight: 600; }
        .news-briefing-md em { color: #29D3F2; }
        .news-briefing-md code {
          background: rgba(255, 165, 30, 0.1); color: #FFA51E;
          padding: 1px 4px; border-radius: 2px; font-size: 10px;
        }
        .news-briefing-md ul {
          font-size: 11px; color: #ECF1FA; margin: 4px 0 8px 0;
          padding-left: 16px; list-style: disc;
        }
        .news-briefing-md li { margin: 2px 0; line-height: 1.5; }
      `}</style>
    </div>
  );
}
