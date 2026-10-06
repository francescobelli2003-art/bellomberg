import type { MutableRefObject, ReactNode } from 'react';
import { AlertTriangle, Check, ExternalLink, FileText, Globe, Heart, Lightbulb, PenLine, Plus, RefreshCw, Search, Star, X } from 'lucide-react';
import type { FavCompany, FilingOverviewTitolo, MktQuote, MktSearchHit } from '@/lib/api';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';
import { Segmenti } from '@/components/nuova/Card';
import { statoDi, tonoStato } from '../filing/logica';
import { chiaveGiudizio, finito, industriaDi, nomeDi, posizione52, settoreDi, settori, SENZA_SETTORE, suScala52, tintaSettore, TUTTI, variazionePct, type Ordine } from './calcoli';
import AggiungiTitolo from './AggiungiTitolo';
import type { Parole } from './parole';

const loc = () => localeDi(linguaCorrente());
const num = (v: number | null | undefined, d = 2) => finito(v) ? v.toLocaleString(loc(), { minimumFractionDigits: d, maximumFractionDigits: d }) : null;
/** Prezzo con i decimali che servono: 4 sotto 1, 3 sotto 10, altrimenti 2. */
const prezzo = (v: number | null | undefined) => finito(v) ? num(v, Math.abs(v) < 1 ? 4 : Math.abs(v) < 10 ? 3 : 2) : null;
const compatto = (v: number | null | undefined) => finito(v) ? Intl.NumberFormat(loc(), { notation: 'compact', maximumFractionDigits: 2 }).format(v) : null;
const pct = (v: number | null | undefined) => finito(v) ? (v > 0 ? '+' : v < 0 ? '−' : '') + num(Math.abs(v)) + '%' : null;
const giorno = (iso?: string | null) => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? new Date(t).toLocaleDateString(loc(), { day: '2-digit', month: '2-digit', year: '2-digit' }) : null;
};
const momento = (iso?: string | null) => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? new Date(t).toLocaleString(loc(), { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }) : null;
};

export type PropsVista = {
  w: Parole;
  favs: FavCompany[] | null; visibili: FavCompany[]; scelto: FavCompany | null;
  quotes: Record<string, MktQuote>; quoteErr: Record<string, string>;
  loading: boolean; err: string | null; quotesAt: string | null;
  filing: Record<string, FilingOverviewTitolo> | null; filingErr: string | null;
  settore: string; setSettore: (s: string) => void; ordine: Ordine; setOrdine: (o: Ordine) => void; testo: string; setTesto: (t: string) => void;
  notes: Record<string, string>; savedNote: Record<string, boolean>; noteErrors: Record<string, string>; savingNotes: Record<string, boolean>;
  profiloAperto: boolean; setProfiloAperto: (v: boolean) => void;
  avviso: { tono: 'ok' | 'bad'; testo: string; annulla?: boolean } | null; chiudiAvviso: () => void; onAnnulla: () => void;
  aggiungo: string | null; addRef: MutableRefObject<HTMLInputElement | null>;
  onRefresh: () => void; onScegli: (t: string) => void; onRemove: (t: string) => void; onAdd: (h: MktSearchHit) => void;
  onEditNote: (t: string, v: string) => void; onSaveNote: (t: string) => void;
  onOpenMarket: (t: string) => void; onTradeIdea: (f: FavCompany) => void; onOpenFiling: (t: string) => void; onExploreMarket: () => void;
};

export default function VistaPreferiti(p: PropsVista) {
  const { w, favs, err, loading } = p;
  const stato = err ? 'error' : favs === null ? 'loading' : favs.length === 0 ? 'empty' : 'ready';
  const conteggio = err ? w.followedNd : favs === null ? w.followedLoading : w.followed(favs.length);

  return (
    <div className="bbn-preferiti bbn-font" data-state={stato}>
      <header className="pf-top">
        <h1>{w.title}</h1>
        <span className="bbn-chip pf-count" data-fav-count>{conteggio}</span>
        <span className="bbn-grow" />
        <AggiungiTitolo w={w} inputRef={p.addRef} preferiti={new Set((favs || []).map(f => f.ticker))} aggiungo={p.aggiungo} onAggiungi={p.onAdd} />
        {(loading || p.quotesAt) && (
          <span className="bbn-chip pf-quotes">
            <i className={'pf-dot' + (loading ? ' is-busy' : '')} aria-hidden="true" />
            {loading || !p.quotesAt ? w.quotesLoading : w.quotesAt(p.quotesAt)}
          </span>
        )}
        <button type="button" className="bbn-btn pf-refresh" onClick={p.onRefresh} disabled={loading}>
          <RefreshCw size={15} className={loading ? 'pf-spin' : ''} aria-hidden="true" />{w.refresh}
        </button>
      </header>

      {err && (
        <div className="pf-note is-warn" role="alert">
          <AlertTriangle size={16} aria-hidden="true" />
          <span className="pf-note-txt"><b>{w.errorTitle}</b> {w.errorBody(err)}</span>
          <button type="button" className="bbn-btn is-sm" onClick={p.onRefresh} disabled={loading}>{w.retry}</button>
        </div>
      )}

      {stato === 'empty' ? (
        <section className="bbn-card pf-empty-big">
          <span className="pf-empty-ico"><Star size={24} aria-hidden="true" /></span>
          <b>{w.emptyTitle}</b>
          <span className="pf-empty-txt">{w.emptyBody}</span>
          <span className="pf-empty-acts">
            <button type="button" className="bbn-btn is-primary" onClick={() => p.addRef.current?.focus()}><Plus size={15} aria-hidden="true" />{w.emptyAdd}</button>
            <button type="button" className="bbn-btn" onClick={p.onExploreMarket}><Globe size={15} aria-hidden="true" />{w.emptyMarket}</button>
          </span>
        </section>
      ) : (
        <div className="pf-flow">
          <Elenco {...p} stato={stato} />
          <section className="bbn-card pf-detail" aria-live="polite">
            {stato === 'error' ? <div className="pf-empty">{w.errorDetail}</div>
              : stato === 'loading' ? <DettaglioCarica />
              : p.scelto ? <Dettaglio {...p} f={p.scelto} />
              : <div className="pf-empty">{w.selectHint}</div>}
          </section>
        </div>
      )}

      {p.avviso && (
        <div className={'pf-toast is-' + p.avviso.tono} role="status">
          <span>{p.avviso.testo}</span>
          {p.avviso.annulla && <button type="button" className="pf-undo" data-undo-remove onClick={p.onAnnulla}>{w.undo}</button>}
          <button type="button" className="bbn-icon-btn" aria-label={p.avviso.annulla ? w.confirmRemove : w.closeToast} onClick={p.chiudiAvviso}><X size={14} aria-hidden="true" /></button>
        </div>
      )}
    </div>
  );
}

/* ── elenco ─────────────────────────────────────────────────────────── */
function Elenco(p: PropsVista & { stato: string }) {
  const { w, favs, quotes } = p;
  const gruppi = favs ? settori(favs, quotes) : [];
  const nomeSettore = (s: string) => s === SENZA_SETTORE ? w.noSector : s;
  return (
    <section className="bbn-card pf-list-card" aria-label={w.listTitle}>
      <header className="bbn-card-head">
        <span className="pf-ci" aria-hidden="true"><Star size={16} /></span>
        <h2>{w.listTitle}</h2>
        <span className="bbn-card-note">{w.listNote}</span>
      </header>
      {p.stato === 'error' ? (
        <div className="pf-empty pf-grow"><b>{w.errorList}</b>{w.errorListHint}</div>
      ) : p.stato === 'loading' ? (
        <div className="pf-list" aria-busy="true" aria-label={w.loading}>
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="pf-row is-skel" aria-hidden="true">
              <span className="pf-sk pf-sk-ico" /><span className="pf-tx"><span className="pf-sk" style={{ width: '60%' }} /><span className="pf-sk" style={{ width: '85%' }} /></span>
              <span className="pf-sk" /><span className="pf-r"><span className="pf-sk" style={{ width: 70 }} /><span className="pf-sk pf-sk-pill" /></span>
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="pf-interest">
            <div className="pf-sbar" role="img" aria-label={w.sectorsLabel + ': ' + gruppi.map(g => `${nomeSettore(g.settore)} ${g.n}`).join(', ')}>
              {gruppi.map((g, i) => <i key={g.settore || '-'} style={{ flex: g.n, background: tintaSettore(g.settore, i) ?? undefined }} className={g.settore === SENZA_SETTORE ? 'is-none' : undefined} />)}
            </div>
            <div className="pf-fbar" role="group" aria-label={w.sectorsLabel}>
              <button type="button" className="pf-fpill" aria-pressed={p.settore === TUTTI} onClick={() => p.setSettore(TUTTI)}>{w.all} <b>{favs?.length ?? 0}</b></button>
              {gruppi.map((g, i) => (
                <button key={g.settore || '-'} type="button" className="pf-fpill" aria-pressed={p.settore === g.settore} data-sector={g.settore || 'none'}
                  onClick={() => p.setSettore(p.settore === g.settore ? TUTTI : g.settore)}>
                  <i className={g.settore === SENZA_SETTORE ? 'is-none' : undefined} style={{ background: tintaSettore(g.settore, i) ?? undefined }} aria-hidden="true" />
                  {nomeSettore(g.settore)} <b>{g.n}</b>
                </button>
              ))}
            </div>
          </div>
          <div className="pf-tools">
            <span className="pf-lab">{w.sortLabel}</span>
            <Segmenti etichetta={w.sortLabel} valore={p.ordine} onChange={p.setOrdine}
              opzioni={[{ id: 'var', testo: w.sort.var }, { id: 'nome', testo: w.sort.nome }, { id: 'data', testo: w.sort.data }]} />
            <label className="pf-field">
              <Search size={14} aria-hidden="true" />
              <input value={p.testo} onChange={e => p.setTesto(e.target.value)} placeholder={w.filterHint} aria-label={w.filterLabel} />
              {p.testo && <button type="button" className="pf-clear" aria-label={w.showAll} onClick={() => p.setTesto('')}><X size={13} aria-hidden="true" /></button>}
            </label>
          </div>
          <div className="pf-list">
            {p.visibili.length === 0 ? (
              <div className="pf-empty">
                <b>{w.noMatch}</b>{w.noMatchHint}
                <button type="button" className="bbn-btn is-sm" onClick={() => { p.setSettore(TUTTI); p.setTesto(''); }}>{w.showAll}</button>
              </div>
            ) : p.visibili.map(f => <Riga key={f.ticker} {...p} f={f} />)}
          </div>
        </>
      )}
    </section>
  );
}

function Riga(p: PropsVista & { f: FavCompany }) {
  const { w, f } = p;
  const q = p.quotes[f.ticker];
  const pos = posizione52(q);
  const v = variazionePct(q);
  const nota = (f.note || '').trim();
  // quotazione ancora in lettura: «…», non «n.d.» (review PR #11)
  const attesa = !q && !p.quoteErr[f.ticker];
  const novita = p.filing?.[f.ticker] ? statoDi(p.filing[f.ticker]) === 'novita' : false;
  return (
    <button type="button" className="pf-row" aria-current={p.scelto?.ticker === f.ticker} data-ticker={f.ticker} onClick={() => p.onScegli(f.ticker)}>
      <IconaTitolo ticker={f.ticker} nome={nomeDi(f, q)} dimensione="sm" />
      <span className="pf-tx">
        <span className="pf-l1">
          <b className="pf-tk">{f.ticker}</b><span className="pf-nm">{nomeDi(f, q)}</span>
          {novita && <span className="pf-badge"><FileText size={10} aria-hidden="true" />{w.filingBadge}</span>}
        </span>
        <span className={'pf-cm' + (nota ? '' : ' is-none')}>{nota || w.noNote}</span>
      </span>
      <span className="pf-mini" title={w.range52Title}>
        {pos == null ? <small>{attesa ? '…' : w.range52Nd}</small> : <><span className="pf-mtr"><i style={{ left: pos + '%' }} /></span><small>{w.range52(Math.round(pos))}</small></>}
      </span>
      <span className="pf-r">
        <span className="pf-px">{prezzo(q?.price) ?? (attesa ? '…' : '—')}<small>{q?.currency || ''}</small></span>
        <PastigliaVariazione valore={v} lampo={q?.price ?? null}>{pct(v) ?? (attesa ? '…' : w.nd)}</PastigliaVariazione>
      </span>
    </button>
  );
}

/* ── dettaglio ──────────────────────────────────────────────────────── */
function Dettaglio(p: PropsVista & { f: FavCompany }) {
  const { w, f } = p;
  const q = p.quotes[f.ticker];
  const qErr = p.quoteErr[f.ticker];
  const set = settoreDi(f, q), ind = industriaDi(f, q);
  const meta = [q?.exchange, set ? set + (ind ? ' / ' + ind : '') : w.noSectorMeta, giorno(f.added_at) ? w.since(giorno(f.added_at)!) : null].filter(Boolean).join(' · ');
  return (
    <>
      <div className="pf-dhead">
        <IconaTitolo ticker={f.ticker} nome={nomeDi(f, q)} dimensione="lg" />
        <div className="pf-dtitle">
          <h2>{nomeDi(f, q)} <span className="pf-tkpill">{f.ticker}</span></h2>
          <span className="pf-meta">{meta}</span>
        </div>
        <div className="pf-acts">
          <button type="button" className="bbn-btn is-primary" data-trade-idea={f.ticker} onClick={() => p.onTradeIdea(f)}><Lightbulb size={15} aria-hidden="true" />{w.tradeIdea}</button>
          <button type="button" className="bbn-btn" data-open-market={f.ticker} onClick={() => p.onOpenMarket(f.ticker)}><Globe size={15} aria-hidden="true" />{w.openMarket}</button>
          <button type="button" className="bbn-icon-btn pf-fav" data-remove={f.ticker} aria-label={w.remove(f.ticker)} title={w.remove(f.ticker)} onClick={() => p.onRemove(f.ticker)}>
            <Heart size={16} fill="currentColor" aria-hidden="true" />
          </button>
        </div>
      </div>
      <div className="pf-dbody">
        {q ? <Prezzo w={w} q={q} quotesAt={p.quotesAt} />
          : qErr ? (
            <div className="pf-note is-warn" role="alert"><AlertTriangle size={16} aria-hidden="true" /><span className="pf-note-txt"><b>{w.quoteMissing}</b> {w.quoteMissingBody(f.ticker, qErr)}</span>
              <button type="button" className="bbn-btn is-sm" onClick={p.onRefresh} disabled={p.loading}>{w.retry}</button></div>
          ) : <div className="pf-note is-plain">{w.quoteLoading}</div>}
        <DatiChiave w={w} q={q} />
        <div className="pf-cols">
          <Nota {...p} />
          <Filing {...p} />
        </div>
        {q?.summary && (
          <div className="pf-sec">
            <div className="pf-sech"><h3>{w.profileTitle}</h3><span className="bbn-grow" />
              <button type="button" className="bbn-link" aria-expanded={p.profiloAperto} onClick={() => p.setProfiloAperto(!p.profiloAperto)}>{p.profiloAperto ? w.less : w.more}</button></div>
            <p className={'pf-prof' + (p.profiloAperto ? ' is-open' : '')}>{q.summary}</p>
          </div>
        )}
      </div>
    </>
  );
}

function Prezzo({ w, q, quotesAt }: { w: Parole; q: MktQuote; quotesAt: string | null }) {
  const v = variazionePct(q);
  const pos = posizione52(q);
  const tgt = suScala52(q, q.target_mean);
  return (
    <div className="pf-hero">
      <div className="pf-pz">
        <div className="pf-pzv"><b>{prezzo(q.price) ?? '—'}</b><span>{q.currency || ''}</span><PastigliaVariazione valore={v} grande lampo={q.price ?? null}>{pct(v) ?? w.nd}</PastigliaVariazione></div>
        <div className="pf-pzs">{[finito(q.prev_close) ? w.prevClose(prezzo(q.prev_close)!) : null, quotesAt ? w.quoteTime(quotesAt) : null].filter(Boolean).join(' · ')}</div>
      </div>
      <div className="pf-r52">
        <div className="pf-r52k"><span>{w.range52Long}</span>{pos != null && <span>{w.fromLow(Math.round(pos))}</span>}</div>
        <div className="pf-r52t">
          {tgt != null && <em style={{ left: tgt + '%' }} title={w.targetMark} />}
          {pos != null && <i style={{ left: pos + '%' }} />}
        </div>
        <div className="pf-r52e"><span>{prezzo(q.low_52w) ?? w.nd}<small>{w.low}</small></span><span>{prezzo(q.high_52w) ?? w.nd}<small>{w.high}</small></span></div>
      </div>
    </div>
  );
}

function DatiChiave({ w, q }: { w: Parole; q: MktQuote | undefined }) {
  const valuta = q?.currency ? ' ' + q.currency : '';
  const up = q && finito(q.target_mean) && finito(q.price) && q.price ? (q.target_mean / q.price - 1) * 100 : null;
  // Yahoo risponde «none» quando nessun analista copre il titolo: non è un giudizio (review PR #11)
  const chiave = chiaveGiudizio(q?.recommendation);
  const rec = chiave && chiave !== 'none' ? (w.rec[chiave] || q!.recommendation!) : null;
  const tiles: Array<{ k: string; v: string | null; s: ReactNode; tono?: string }> = [
    { k: w.k.cap, v: compatto(q?.market_cap) ? compatto(q?.market_cap) + valuta : null, s: w.s.cap },
    { k: w.k.pe, v: num(q?.pe, 1), s: num(q?.fwd_pe, 1) ? w.s.pe(num(q?.fwd_pe, 1)!) : w.s.peNd },
    { k: w.k.eve, v: num(q?.ev_ebitda, 1), s: finito(q?.ev_ebitda) ? w.s.eve : w.s.nd },
    // dividendYield di Yahoo è già in punti percentuali (vedi DettaglioTitolo)
    { k: w.k.div, v: finito(q?.div_yield) ? num(q!.div_yield, 2) + '%' : null, s: w.s.div },
    { k: w.k.tgt, v: prezzo(q?.target_mean), s: up != null ? w.s.tgt(pct(up)!) : w.s.nocov, tono: up == null ? undefined : up >= 0 ? 'pf-up' : 'pf-dn' },
    { k: w.k.rec, v: rec, s: rec ? w.s.rec : w.s.nocov },
    { k: w.k.beta, v: num(q?.beta), s: w.s.beta },
    { k: w.k.vol, v: compatto(q?.volume), s: compatto(q?.avg_volume) ? w.s.vol(compatto(q?.avg_volume)!) : w.s.nd },
  ];
  return (
    <div className="pf-sec">
      <div className="pf-sech"><h3>{w.keyData}</h3><span className="bbn-card-note">{w.keyDataNote}</span></div>
      <div className="pf-tiles">
        {tiles.map(t => (
          <div key={t.k} className={'pf-tile' + (t.v == null ? ' is-nd' : '')}>
            <span className="pf-tk-k">{t.k}</span><span className="pf-tk-v">{t.v ?? w.nd}</span><span className={'pf-tk-s' + (t.tono ? ' ' + t.tono : '')}>{t.s}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Nota(p: PropsVista & { f: FavCompany }) {
  const { w, f } = p;
  const t = f.ticker;
  const testo = p.notes[t] ?? '';
  const sporca = testo !== (f.note || '');
  const salvo = !!p.savingNotes[t];
  const errore = p.noteErrors[t];
  return (
    <div className="pf-sec pf-nota">
      <div className="pf-sech"><h3>{w.noteTitle}</h3><span className="bbn-card-note">{w.noteNote}</span></div>
      <textarea value={testo} rows={6} maxLength={1000} aria-label={w.noteLabel(t)} placeholder={w.notePlaceholder}
        onChange={e => p.onEditNote(t, e.target.value)}
        onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && sporca && !salvo) { e.preventDefault(); p.onSaveNote(t); } }} />
      <div className="pf-nfoot">
        {salvo ? <span className="pf-stato">{w.noteSaving}</span>
          : sporca ? <span className="pf-stato is-dirty"><PenLine size={13} aria-hidden="true" />{w.noteDirty}</span>
          : p.savedNote[t] ? <span className="pf-stato is-ok"><Check size={13} aria-hidden="true" />{w.noteSavedNow}</span>
          : f.note ? <span className="pf-stato"><Check size={13} aria-hidden="true" />{w.noteStored}</span>
          : <span className="pf-stato">{w.noteNone}</span>}
        <span className="pf-ncount">{testo.length}/1000</span>
        <span className="bbn-grow" />
        <button type="button" className={'bbn-btn is-sm' + (sporca ? ' is-primary' : '')} data-save-note={t} disabled={salvo || !sporca} onClick={() => p.onSaveNote(t)}>
          {salvo ? w.noteSaving : w.noteSave}
        </button>
      </div>
      {errore && <div role="alert" className="pf-note is-bad pf-nerr"><AlertTriangle size={15} aria-hidden="true" /><span className="pf-note-txt">{w.noteFailed(errore)}</span></div>}
    </div>
  );
}

function Filing(p: PropsVista & { f: FavCompany }) {
  const { w, f } = p;
  const t = p.filing?.[f.ticker];
  const stato = t ? statoDi(t) : null;
  const tono = stato ? tonoStato(stato) : null;
  const apri = <button type="button" className="bbn-link" data-open-filing={f.ticker} onClick={() => p.onOpenFiling(f.ticker)}>{w.filingOpen} <ExternalLink size={13} aria-hidden="true" /></button>;
  let corpo;
  if (p.filingErr) corpo = <div className="pf-note is-warn"><AlertTriangle size={15} aria-hidden="true" /><span className="pf-note-txt">{w.filingErr(p.filingErr)}</span></div>;
  else if (!p.filing) corpo = <div className="pf-fil"><p>{w.filingLoading}</p></div>;
  // assente dalla risposta (es. titolo anche in portafoglio: l'ambito preferiti lo esclude) = n.d.
  else if (!t || !stato) corpo = (
    <div className="pf-fil">
      <div className="pf-filh"><span className="pf-tag is-off">{w.nd}</span><b>{w.filingAbsentTitle}</b></div>
      <p>{w.filingAbsent(f.ticker)}</p>
    </div>
  );
  else if (stato === 'non_attivo') corpo = (
    <div className="pf-fil">
      <div className="pf-filh"><span className="pf-tag is-off">{w.filingTag.non_attivo}</span><b>{w.filingInactiveTitle}</b></div>
      <p>{w.filingInactive}</p>
      <div><button type="button" className="bbn-btn is-sm" onClick={() => p.onOpenFiling(f.ticker)}>{w.filingActivate}</button></div>
    </div>
  );
  else if (stato === 'senza_fonte') corpo = (
    <div className="pf-fil">
      <div className="pf-filh"><span className="pf-tag is-off">{w.filingTag.senza_fonte}</span><b>{w.filingNoSourceTitle}</b></div>
      <p>{w.filingNoSource(f.ticker)}</p>
    </div>
  );
  else {
    const conf = giorno(t.ultimo_confronto);
    corpo = (
      <div className="pf-fil">
        <div className="pf-filh">
          <span className={'pf-tag is-' + tono}>{w.filingTag[stato]}</span>
          {t.documento && <b>{t.documento}</b>}
          {conf && <span className="bbn-card-note">· {w.filingCompared(conf)}</span>}
        </div>
        <p>{!t.ultimo_confronto ? w.filingFirst : t.cambiamenti ? <b>{w.filingChanges(t.cambiamenti)}</b> : w.filingNoChanges}</p>
        <dl className="pf-kv">
          {t.fonte && <><dt>{w.filingSource}</dt><dd>{t.fonte.toUpperCase()}</dd></>}
          {momento(t.prossimo_at) && <><dt>{w.filingNext}</dt><dd>{momento(t.prossimo_at)}</dd></>}
          {t.ultimo_errore?.reason && <><dt>{w.filingLastError}</dt><dd className="pf-dn">{[momento(t.ultimo_errore.at), t.ultimo_errore.reason].filter(Boolean).join(' · ')}</dd></>}
        </dl>
      </div>
    );
  }
  return (
    <div className="pf-sec">
      <div className="pf-sech"><h3>{w.filingTitle}</h3><span className="bbn-card-note">{w.filingNote}</span><span className="bbn-grow" />{apri}</div>
      {corpo}
    </div>
  );
}

function DettaglioCarica() {
  return (
    <div className="pf-dskel" aria-hidden="true">
      <div className="pf-dhead"><span className="pf-sk pf-sk-lg" /><div className="pf-dtitle"><span className="pf-sk" style={{ width: 260, height: 20 }} /><span className="pf-sk" style={{ width: 380 }} /></div></div>
      <div className="pf-dbody">
        <span className="pf-sk" style={{ height: 92, borderRadius: 18 }} />
        <div className="pf-tiles">{Array.from({ length: 8 }, (_, i) => <span key={i} className="pf-sk" style={{ height: 66, borderRadius: 14 }} />)}</div>
        <div className="pf-cols"><span className="pf-sk" style={{ height: 190, borderRadius: 14 }} /><span className="pf-sk" style={{ height: 190, borderRadius: 16 }} /></div>
      </div>
    </div>
  );
}
