/* Viste della pagina Decisioni (mockup approvato 05/10/2026, outputs/decisioni-nuova/mockup.html):
   intestazione con le quattro viste, striscia «Da fare», elenco | dettaglio, dialoghi di conferma.
   Solo presentazione: dati e azioni arrivano da Decisions.tsx (i test SSR indicizzano gli hook). */
import type { ReactNode } from 'react';
import {
  Archive, ArchiveRestore, Ban, Bot, Check, ChevronDown, ChevronUp, FileText, FlaskConical, Gavel, ListChecks,
  Pin, Plus, RefreshCw, RotateCcw, Send, Target, TriangleAlert, X,
} from 'lucide-react';
import { Segmenti } from '@/components/nuova/Card';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import type { Decision, DecisionEvent, DecisionNote } from '@/lib/api';
import { t as tr, type Chiave, type Parametri } from '@/i18n/t';
import { localeDi, type Lingua } from '@/i18n/lingua';
import { statoDivergenza, decisioneCompatibile } from '@/lib/trade-entry';
import {
  GIORNI_FERMA, giorniDa, isHold, isResearch, perMese, ricercaFerma, stessoTitolo, ultimaAttivita, vociVista,
  type Esito, type FiltroChiuse,
} from './logica';
import type { AzioniDecisioni, DatiDecisioni } from './tipi';

type Tono = 'ok' | 'bad' | 'warn' | 'acc' | 'off';
const w = (k: string, p?: Parametri) => tr(('decisiondesk.' + k) as Chiave, p);
const conta = (k: string, n: number, p?: Parametri) => w(`${k}_${n === 0 && k.startsWith('notesCount') ? 'zero' : n === 1 ? 'one' : 'other'}`, { n, ...p });
const giorniFa = (n: number | null) => n == null ? '' : n === 0 ? w('daysAgo_zero') : conta('daysAgo', n);

const ESITI: Esito[] = ['EXECUTED', 'PARTIAL', 'SKIPPED', 'EXPIRED'];
const STATO_TONO: Record<string, Tono> = { PENDING: 'warn', EXECUTED: 'ok', PARTIAL: 'acc', SKIPPED: 'bad', EXPIRED: 'off' };
const STATO_CHIAVE: Record<string, string> = { PENDING: 'pending', EXECUTED: 'executed', PARTIAL: 'partial', SKIPPED: 'skipped', EXPIRED: 'expired' };
const AZIONE_TONO: Record<string, Tono> = { BUY: 'acc', ADD: 'acc', SELL: 'bad', TRIM: 'warn', HOLD: 'off', RESEARCH: 'acc' };
export const statoTesto = (s: string) => STATO_CHIAVE[s] ? w(STATO_CHIAVE[s]) : s;
const azioneTesto = (a: string) => {
  const k = (a || '').toUpperCase();
  return k in AZIONE_TONO ? w('a' + k) : a;
};

const fmt = (lingua: Lingua, v: number, dec = 0) => new Intl.NumberFormat(localeDi(lingua),
  { useGrouping: 'always' as unknown as boolean, minimumFractionDigits: dec, maximumFractionDigits: dec }).format(v); // lib TS precedente a Intl v3
export const euro = (lingua: Lingua, v: number | null | undefined, segno = false) => {
  if (v == null || !Number.isFinite(v)) return w('na');
  const s = fmt(lingua, Math.abs(v), Number.isInteger(v) ? 0 : 2);
  const pre = v < 0 ? '−' : segno && v > 0 ? '+' : '';
  return lingua === 'en' ? `${pre}€${s}` : `${pre}${s}\u00a0€`;
};
const pctTesto = (lingua: Lingua, v: number) => (v < 0 ? '−' : v > 0 ? '+' : '') + fmt(lingua, Math.abs(v), 1) + '%';
export const data = (lingua: Lingua, iso?: string | null, ora = false) => {
  if (!iso) return w('na');
  const d = new Date(iso.slice(0, 10) + 'T00:00:00');
  if (!Number.isFinite(d.getTime())) return iso;
  return d.toLocaleDateString(localeDi(lingua), { day: '2-digit', month: '2-digit', year: 'numeric' })
    + (ora && iso.length >= 16 ? ' ' + iso.slice(11, 16) : '');
};
const dataBreve = (lingua: Lingua, iso?: string | null) => {
  if (!iso) return '';
  const d = new Date(iso.slice(0, 10) + 'T00:00:00');
  return Number.isFinite(d.getTime()) ? d.toLocaleDateString(localeDi(lingua), { day: '2-digit', month: '2-digit' }) : iso;
};

const Pill = ({ tono, children, ...rest }: { tono: Tono; children: ReactNode } & React.HTMLAttributes<HTMLSpanElement>) =>
  <span className={`dc-pill is-${tono}`} {...rest}>{children}</span>;
const PillAzione = ({ a }: { a: string }) => <Pill tono={AZIONE_TONO[(a || '').toUpperCase()] ?? 'off'}>{azioneTesto(a)}</Pill>;
const PillStato = ({ s }: { s: string }) => <Pill tono={STATO_TONO[s] ?? 'off'}>{statoTesto(s)}</Pill>;

function Sezione({ titolo, nota, azioni, children }: { titolo: ReactNode; nota?: ReactNode; azioni?: ReactNode; children: ReactNode }) {
  return (
    <section className="dc-sec">
      <header className="dc-sec-h"><h3>{titolo}</h3>{nota && <span className="bbn-card-note">{nota}</span>}<span className="bbn-grow" />{azioni}</header>
      {children}
    </section>
  );
}

/* ── intestazione e «Da fare» ─────────────────────────────────────── */
function Intestazione({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  const g = d.gruppi;
  const n = (x: number) => d.loadErr !== null ? w('na') : d.loading ? '…' : fmt(d.lingua, x);
  const viste = [
    { id: 'todo' as const, testo: w('viewTodo'), n: g.esegui.length + g.bloccate.length + g.conferme.length },
    { id: 'res' as const, testo: w('viewResearch'), n: g.ricerche.length },
    { id: 'closed' as const, testo: w('viewClosed'), n: g.chiuse.length },
    { id: 'arch' as const, testo: w('viewArchive'), n: g.archOp.length + g.archRes.length },
  ];
  const ultimo = d.decisions.reduce<Decision | null>((m, x) => x.memo_id != null && (!m || (x.timestamp || '') > (m.timestamp || '')) ? x : m, null);
  return (
    <div className="dc-top">
      <h1>{w('title')}</h1>
      <Segmenti etichetta={w('views')} valore={d.vista} onChange={a.vista} className="is-large"
        opzioni={viste.map(v => ({ id: v.id, testo: <>{v.testo}<span className={'dc-n' + (d.vista === v.id ? ' is-on' : '')} data-dc-conta={v.id}>{n(v.n)}</span></> }))} />
      <span className="bbn-grow" />
      {ultimo && <span className="bbn-chip">{w('lastMemo')} <b>{w('lastMemoAt', { id: ultimo.memo_id ?? '', date: data(d.lingua, ultimo.timestamp, true) })}</b></span>}
      <button type="button" className="bbn-link" onClick={a.apriMemo}><FileText size={14} aria-hidden="true" />{w('openMemos')}</button>
    </div>
  );
}

function DaFare({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  if (d.loadErr !== null) return (
    <div className="dc-note is-bad dc-center" role="alert" data-dc-errore="lettura">
      <TriangleAlert size={16} aria-hidden="true" />
      <span><b>{w('loadFailed')}</b> {w('loadFailedWhy', { detail: d.loadErr || w('errorUnknown') })}</span>
      <button type="button" className="bbn-btn is-sm" onClick={a.riprova}><RefreshCw size={14} aria-hidden="true" />{w('retry')}</button>
    </div>
  );
  if (d.loading && !d.decisions.length) return null;
  const g = d.gruppi, ferme = g.ricerche.filter(x => ricercaFerma(x));
  const voci: { tono: string; n: number; testo: string; vai?: Decision }[] = [
    { tono: 'acc', n: g.esegui.length, testo: conta('todoExec', g.esegui.length), vai: g.esegui[0] },
    { tono: 'bad', n: g.bloccate.length, testo: conta('todoBlocked', g.bloccate.length), vai: g.bloccate[0] },
    { tono: '', n: g.conferme.length, testo: conta('todoHold', g.conferme.length), vai: g.conferme[0] },
    { tono: 'warn', n: ferme.length, testo: conta('todoStale', ferme.length, { d: GIORNI_FERMA }), vai: ferme[0] },
  ].filter(v => v.n > 0);
  return (
    <div className="dc-todo" data-dc-todo={voci.length}>
      <b>{w('todo')}</b>
      {voci.length ? voci.map(v => (
        <button key={v.testo} type="button" className={'bbn-chip' + (v.tono ? ' is-' + v.tono : '')} onClick={() => v.vai && a.vaiA(v.vai)}>
          <b>{fmt(d.lingua, v.n)}</b>{v.testo}
        </button>
      )) : <span className="dc-ok"><Check size={14} aria-hidden="true" />{w('todoNone')}</span>}
    </div>
  );
}

/* ── elenco ───────────────────────────────────────────────────────── */
function Riga({ d, a, x, sotto, destra, piede }: { d: DatiDecisioni; a: AzioniDecisioni; x: Decision; sotto: ReactNode; destra: ReactNode; piede: ReactNode }) {
  const on = d.sel?.id === x.id;
  return (
    <button type="button" id={`decision-${x.id}`} className={'dc-row' + (on ? ' is-on' : '') + (d.target === x.id ? ' is-target' : '')}
      aria-current={on} data-dc-sel={x.id} onClick={() => a.scegli(x.id)}>
      <IconaTitolo ticker={x.ticker} />
      <span className="dc-row-name"><b>{x.ticker}</b><span>{sotto}</span></span>
      <span className="dc-row-end"><span className="dc-pp">{destra}</span><small>{piede}</small></span>
    </button>
  );
}

const primaRiga = (s?: string | null) => (s || '').split('\n').map(r => r.trim()).find(Boolean) || '';
const sottoOp = (d: DatiDecisioni, x: Decision) =>
  [x.eur_amount != null ? euro(d.lingua, x.eur_amount) : null, primaRiga(x.rationale) || w('proposal', { id: x.id })].filter(Boolean).join(' · ');

function Gruppo({ titolo, n, tono, children }: { titolo: string; n: number; tono?: Tono; children: ReactNode }) {
  return (
    <div className="dc-grp">
      <h3>{tono && <i className={'dc-dot is-' + tono} />}{titolo}<span>{n}</span></h3>
      {children}
    </div>
  );
}

function ElencoTodo({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  const g = d.gruppi;
  const op = (x: Decision, extra?: ReactNode) => <Riga key={x.id} d={d} a={a} x={x} sotto={sottoOp(d, x)}
    destra={<><PillAzione a={x.action} />{extra}</>}
    piede={x.esecuzione ? w('partlyExecuted') : x.timestamp ? giorniFa(giorniDa(x.timestamp)) : ''} />;
  return <>
    {g.esegui.length > 0 && <Gruppo titolo={w('grpExec')} n={g.esegui.length} tono="acc">{g.esegui.map(x => op(x))}</Gruppo>}
    {g.bloccate.length > 0 && <Gruppo titolo={w('grpBlocked')} n={g.bloccate.length} tono="bad">
      {g.bloccate.map(x => op(x, <Pill tono="bad">{w('blockedPill')}</Pill>))}</Gruppo>}
    {g.conferme.length > 0 && <Gruppo titolo={w('grpHold')} n={g.conferme.length} tono="off">
      <div className="dc-hold" data-dc-hold={g.conferme.length}>
        <div className="dc-hold-h"><b>{conta('holdTitle', g.conferme.length)}</b><span className="bbn-card-note">{w('holdNote')}</span>
          <span className="bbn-grow" />
          <button type="button" className="bbn-link" aria-expanded={d.holdAperte} onClick={a.holdAperte}>
            {d.holdAperte ? w('holdHide') : w('holdShow')}{d.holdAperte ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
          </button></div>
        {!d.holdAperte && <div className="dc-hold-tk">{g.conferme.map(x => (
          <button key={x.id} type="button" aria-current={d.sel?.id === x.id} data-dc-sel={x.id} onClick={() => a.scegli(x.id)}>{x.ticker}</button>
        ))}</div>}
        <div className="dc-hold-a">
          <button type="button" className="bbn-btn is-primary is-sm" data-dc-azione="hold-tutte" disabled={d.saving}
            onClick={() => a.apriDialogo({ tipo: 'hold' })}><ListChecks size={14} aria-hidden="true" />{w('holdConfirmAll')}</button>
          <span className="bbn-card-note">{w('holdConfirmHint')}</span>
        </div>
      </div>
      {d.holdAperte && g.conferme.map(x => op(x))}
    </Gruppo>}
  </>;
}

function ElencoRicerche({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  const r = (x: Decision) => {
    const n = (x.notes || []).length, ferma = ricercaFerma(x), gg = giorniDa(x.timestamp);
    return <Riga key={x.id} d={d} a={a} x={x} sotto={x.timing || primaRiga(x.rationale) || w('research', { id: x.id })}
      destra={x.status !== 'PENDING' ? <Pill tono="off">{w('stoppedPill', { status: statoTesto(x.status).toLowerCase() })}</Pill>
        : <Pill tono={ferma ? 'warn' : 'acc'}>{gg == null ? w('na') : w('since', { n: gg })}</Pill>}
      piede={conta('notesCount', n)} />;
  };
  const g = d.gruppi;
  return <>
    {g.ricerche.length > 0 && <Gruppo titolo={w('grpWorking')} n={g.ricerche.length} tono="acc">{g.ricerche.map(r)}</Gruppo>}
    {g.ricercheFerme.length > 0 && <Gruppo titolo={w('grpStopped')} n={g.ricercheFerme.length} tono="off">{g.ricercheFerme.map(r)}</Gruppo>}
  </>;
}

function ElencoChiuse({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  const lista = vociVista(d.gruppi, 'closed', d.filtro, d.tipoArch);
  return <>{perMese(lista, localeDi(d.lingua)).map(m => (
    <Gruppo key={m.mese} titolo={m.mese} n={m.voci.length}>{m.voci.map(x => (
      <Riga key={x.id} d={d} a={a} x={x} sotto={sottoOp(d, x)}
        destra={<>{!!x.veto && <Pill tono="bad"><Ban size={12} aria-hidden="true" />{w('vetoPill')}</Pill>}<PillStato s={x.status} /></>}
        piede={x.outcome_pct != null ? <span className={x.outcome_pct >= 0 ? 'dc-up' : 'dc-dn'}>{pctTesto(d.lingua, x.outcome_pct)}</span> : azioneTesto(x.action)} />
    ))}</Gruppo>
  ))}</>;
}

function ElencoArchivio({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  const lista = vociVista(d.gruppi, 'arch', d.filtro, d.tipoArch);
  return <Gruppo titolo={w('grpArchived')} n={lista.length}>{lista.map(x => (
    <Riga key={x.id} d={d} a={a} x={x} sotto={`${w(isResearch(x) ? 'research' : 'proposal', { id: x.id })} · ${data(d.lingua, x.timestamp)}`}
      destra={isResearch(x) ? <Pill tono={x.status === 'EXECUTED' ? 'ok' : 'off'}>{x.status === 'EXECUTED' ? w('promoted') : w('closedPill')}</Pill> : <PillStato s={x.status} />}
      piede={x.archive_override != null ? w('pinned') : w('autoArchived')} />
  ))}</Gruppo>;
}

function Elenco({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  const testa = {
    todo: [<Gavel key="i" size={16} />, w('listTodo'), w('listTodoNote')],
    res: [<FlaskConical key="i" size={16} />, w('listResearch'), w('listResearchNote')],
    closed: [<Check key="i" size={16} />, w('listClosed'), w('listClosedNote')],
    arch: [<Archive key="i" size={16} />, w('listArchive'), w('listArchiveNote')],
  }[d.vista];
  const voci = vociVista(d.gruppi, d.vista, d.filtro, d.tipoArch);
  const filtri: { id: FiltroChiuse; testo: string }[] = [
    { id: 'all', testo: w('filterAll') }, { id: 'EXECUTED', testo: w('filterExecuted') }, { id: 'PARTIAL', testo: w('filterPartial') },
    { id: 'SKIPPED', testo: w('filterSkipped') }, { id: 'EXPIRED', testo: w('filterExpired') }];
  let corpo: ReactNode;
  if (d.loadErr !== null) corpo = <p className="dc-empty is-bad">{w('loadErrList')}</p>;
  else if (d.loading && !d.decisions.length) corpo = <><p className="dc-sr" role="status">{w('loading')}</p>{[0, 1, 2, 3, 4, 5].map(i => <div key={i} className="dc-skel" />)}</>;
  else if (!voci.length) corpo = <div className="dc-empty">{d.vista === 'todo' ? <><b>{w('emptyTodo')}</b>{w('emptyTodoWhy')}</>
    : d.vista === 'res' ? <><b>{w('emptyResearch')}</b>{w('emptyResearchWhy')}</> : d.vista === 'closed' ? w('emptyClosed') : w('emptyArchive')}</div>;
  else corpo = d.vista === 'todo' ? <ElencoTodo d={d} a={a} /> : d.vista === 'res' ? <ElencoRicerche d={d} a={a} />
    : d.vista === 'closed' ? <ElencoChiuse d={d} a={a} /> : <ElencoArchivio d={d} a={a} />;
  return (
    <section className="bbn-card dc-card dc-list" data-dc-elenco={d.vista}>
      <header className="dc-card-head">
        <span className="dc-ci" aria-hidden="true">{testa[0]}</span><h2>{testa[1]}</h2><span className="bbn-card-note">{testa[2]}</span>
        {d.vista === 'closed' && <div className="dc-head-row"><Segmenti etichetta={w('filterLabel')} valore={d.filtro} onChange={a.filtro} opzioni={filtri} /></div>}
        {d.vista === 'arch' && <div className="dc-head-row"><Segmenti etichetta={w('kindLabel')} valore={d.tipoArch} onChange={a.tipoArch} opzioni={[
          { id: 'op', testo: w('kindOps', { n: d.gruppi.archOp.length }) }, { id: 'res', testo: w('kindResearch', { n: d.gruppi.archRes.length }) }]} /></div>}
      </header>
      <div className="dc-list-body">{corpo}</div>
    </section>
  );
}

/* ── dettaglio: pezzi comuni ──────────────────────────────────────── */
function Riquadri({ voci }: { voci: [string, ReactNode, ReactNode, Tono?][] }) {
  return <div className="dc-tiles">{voci.map(([k, v, s, tono]) => (
    <div key={k} className="dc-tile"><span className="k">{k}</span><span className={'v' + (tono ? ' is-' + tono : '')}>{v}</span><span className="s">{s}</span></div>
  ))}</div>;
}

function Testata({ d, a, x }: { d: DatiDecisioni; a: AzioniDecisioni; x: Decision }) {
  const res = isResearch(x), archiviata = d.vista === 'arch';
  const meta = [w(res ? 'research' : 'proposal', { id: x.id }),
    x.memo_id != null ? w('fromMemo', { id: x.memo_id, date: data(d.lingua, x.timestamp) }) : data(d.lingua, x.timestamp),
    giorniFa(giorniDa(x.timestamp))].filter(Boolean).join(' · ');
  let azioni: ReactNode;
  if (res) {
    azioni = archiviata
      ? <button type="button" className="bbn-btn is-primary is-sm" data-dc-azione="ricerca-ripristina" disabled={d.saving} title={w('restoreResearchHint')} onClick={() => a.riapriRicerca(x)}><ArchiveRestore size={14} aria-hidden="true" />{w('restoreResearch')}</button>
      : x.status === 'PENDING'
        ? <button type="button" className="bbn-btn is-sm" data-dc-azione="ricerca-chiudi" disabled={d.saving} title={w('closeResearchHint')} onClick={() => a.chiudiRicerca(x)}><Archive size={14} aria-hidden="true" />{w('closeResearch')}</button>
        : <button type="button" className="bbn-btn is-primary is-sm" data-dc-azione="ricerca-riapri" disabled={d.saving} title={w('reopenResearchHint')} onClick={() => a.riapriRicerca(x)}><RotateCcw size={14} aria-hidden="true" />{w('reopenResearch')}</button>;
  } else {
    azioni = archiviata
      ? <button type="button" className="bbn-btn is-primary is-sm" data-dc-azione="ripristina" disabled={d.saving} title={w('restoreHint')} onClick={() => a.archivia(x, false)}><ArchiveRestore size={14} aria-hidden="true" />{w('restore')}</button>
      : <button type="button" className="bbn-btn is-sm" data-dc-azione="archivia" disabled={d.saving} title={w('archiveHint')} onClick={() => a.archivia(x, true)}><Archive size={14} aria-hidden="true" />{w('archive')}</button>;
  }
  return (
    <header className="dc-det-head">
      <IconaTitolo ticker={x.ticker} dimensione="lg" />
      <div className="dc-det-t">
        <h2><span>{x.ticker}</span>{!res && <PillAzione a={x.action} />}
          {res ? (x.status === 'PENDING' ? <Pill tono={ricercaFerma(x) ? 'warn' : 'acc'}>{giorniDa(x.timestamp) == null ? w('na') : w('since', { n: giorniDa(x.timestamp) as number })}</Pill> : <PillStato s={x.status} />) : <PillStato s={x.status} />}
          {!!x.veto && <Pill tono="bad"><Ban size={12} aria-hidden="true" />{w('vetoPill')}</Pill>}
        </h2>
        <span>{meta}</span>
      </div>
      <span className="bbn-grow" />
      <div className="dc-det-act">
        {x.trade_idea && <button type="button" className="bbn-link" onClick={() => a.apriRicercaCollegata(x)}><FlaskConical size={14} aria-hidden="true" />{w('linkedResearch')}</button>}
        {x.memo_id != null && <button type="button" className="bbn-link" onClick={a.apriMemo}><FileText size={14} aria-hidden="true" />{w('memo', { id: x.memo_id })}</button>}
        {azioni}
      </div>
    </header>
  );
}

function Fissata({ d, a, x }: { d: DatiDecisioni; a: AzioniDecisioni; x: Decision }) {
  if (x.archive_override == null) return null;
  return (
    <div className="dc-note dc-center" data-dc-fissata="1">
      <Pin size={16} aria-hidden="true" />
      <span><b>{w('pinnedNote')}</b> {w(x.archive_override ? 'pinnedNoteArchived' : 'pinnedNoteActive')}</span>
      <button type="button" className="bbn-link" disabled={d.saving} title={w('backToAutoHint')} onClick={() => a.archivia(x, null)}>{w('backToAuto')}</button>
    </div>
  );
}

function Provenienza({ x }: { x: Decision }) {
  const idea = x.trade_idea;
  if (!idea || idea.destination_kind !== 'dcn' || (idea.technical_status === 'completed' && idea.artifacts_ready)) return null;
  return <div className="dc-note is-bad" role="status"><TriangleAlert size={16} aria-hidden="true" /><span><b>{w('ideaBlocked')}</b> {idea.destination_reason || ''}</span></div>;
}

/* ── dettaglio operativa ──────────────────────────────────────────── */
const RISCHIO: Record<string, [string, Tono]> = { OPERATIVE: ['rOperative', 'ok'], BLOCKED: ['rBlocked', 'bad'], OVERRIDE_PENDING: ['rOverride', 'warn'], CHECK_UNAVAILABLE: ['rUnavailable', 'warn'] };
const TITOLO_BLOCCO: Record<string, string> = { BLOCKED: 'blockedTitle', OVERRIDE_PENDING: 'overrideTitle', CHECK_UNAVAILABLE: 'unavailableTitle' };

function Cronologia({ d, x }: { d: DatiDecisioni; x: Decision }) {
  const ev = d.eventi?.id === x.id ? d.eventi : null;
  const righe: { tono: Tono | ''; t: string; s?: string | null; quando: string | null; k: string }[] = [
    { k: 'pub', tono: 'acc', t: w('evPublished'), s: x.memo_id != null ? w('evPublishedSub', { id: x.memo_id }) : null, quando: x.timestamp },
  ];
  const det = (e: DecisionEvent) => { try { return JSON.parse(e.details_json || '{}') as Record<string, unknown>; } catch { return {}; } };
  for (const e of ev?.lista ?? []) {
    const dd = det(e);
    const r = e.event_type === 'ASSESSMENT_RECORDED' ? { tono: (RISCHIO[e.to_status || '']?.[1] ?? '') as Tono | '', t: w('evAssessment', { status: RISCHIO[e.to_status || ''] ? w(RISCHIO[e.to_status || ''][0]) : (e.to_status || w('na')) }) }
      : e.event_type === 'STATUS_CHANGED' ? { tono: (STATO_TONO[e.to_status || ''] ?? '') as Tono | '', t: w('evStatus', { from: e.from_status ? statoTesto(e.from_status) : w('na'), to: e.to_status ? statoTesto(e.to_status) : w('na') }) }
      : e.event_type === 'NOTE_UPDATED' ? { tono: '' as const, t: w('evNote') }
      : e.event_type === 'TRADE_LINKED' ? { tono: 'acc' as const, t: w('evTrade', { id: dd.trade_id != null ? String(dd.trade_id) : w('na') }) }
      : e.event_type === 'MANUAL_TRADE_DIVERGENCE_RECORDED' ? { tono: 'warn' as const, t: w('evDivergence', { id: dd.trade_id != null ? String(dd.trade_id) : w('na') }) }
      : e.event_type === 'PUBLICATION_WITHDRAWN' ? { tono: 'bad' as const, t: w('evWithdrawn') }
      : { tono: '' as const, t: e.event_type };
    righe.push({ k: 'e' + e.id, ...r, s: e.reason, quando: e.created_at });
  }
  if (x.veto && x.veto_at) righe.push({ k: 'veto', tono: 'bad', t: w('evVeto'), s: x.veto_reason ? `«${x.veto_reason}»` : null, quando: x.veto_at });
  if (!ev?.lista?.some(e => e.event_type === 'STATUS_CHANGED') && x.status !== 'PENDING' && x.closed_at)
    righe.push({ k: 'chiusa', tono: STATO_TONO[x.status] ?? '', t: w('evClosed', { status: statoTesto(x.status).toLowerCase() }), quando: x.closed_at });
  righe.sort((p, q) => (p.quando || '').localeCompare(q.quando || ''));
  return (
    <Sezione titolo={w('timeline')} nota={w('timelineNote')}>
      <ol className="dc-tl" data-dc-cronologia={ev?.lista ? ev.lista.length : ev?.err ? 'errore' : 'attesa'}>
        {righe.map(r => <li key={r.k}><i className={r.tono ? 'is-' + r.tono : ''} /><span>{r.t}{r.s && <small>{r.s}</small>}</span><time>{r.quando ? data(d.lingua, r.quando, true) : ''}</time></li>)}
      </ol>
      {ev?.err && <p className="dc-mini">{w('timelineErr', { detail: ev.err })}</p>}
    </Sezione>
  );
}

function StessoTitolo({ d, a, x }: { d: DatiDecisioni; a: AzioniDecisioni; x: Decision }) {
  const altre = stessoTitolo(d.decisions, x);
  if (!altre.length) return null;
  return (
    <Sezione titolo={w('sameTicker', { ticker: x.ticker })} nota={w('sameTickerNote')}>
      <div>{altre.map(o => (
        <button key={o.id} type="button" className="dc-srow" data-dc-altra={o.id} onClick={() => a.vaiA(o)}>
          <span>{w(isResearch(o) ? 'research' : 'proposal', { id: o.id })}{o.memo_id != null && <small> · {w('memo', { id: o.memo_id })}</small>}</span>
          <span className="dc-pp"><PillAzione a={o.action} /><PillStato s={o.status} /></span>
          <small>{dataBreve(d.lingua, o.timestamp)}</small>
        </button>
      ))}</div>
    </Sezione>
  );
}

function ChiudiDecisione({ d, a, x }: { d: DatiDecisioni; a: AzioniDecisioni; x: Decision }) {
  const pctErr = d.letturaPct && !d.letturaPct.ok ? d.letturaPct.motivo : null;
  const eurErr = d.letturaEur && !d.letturaEur.ok ? d.letturaEur.motivo : null;
  // in Archivio niente Riapri: una proposta riaperta li' resterebbe PENDING fuori da «Da decidere»
  const chiusa = x.status !== 'PENDING', archiviata = d.vista === 'arch';
  return (
    <div className="dc-box" data-dc-chiudi={x.id}>
      <h3><Gavel size={16} aria-hidden="true" />{w(chiusa ? 'fixTitle' : 'closeTitle')}</h3>
      <div className="bbn-seg dc-seg-full" role="radiogroup" aria-label={w('outcomeLabel')}>
        {ESITI.map(e => {
          const vietato = !d.eseguibile && (e === 'EXECUTED' || e === 'PARTIAL');
          return <button key={e} type="button" role="radio" aria-checked={d.esito === e} className={d.esito === e ? 'is-on' : undefined}
            data-dc-esito={e} disabled={vietato || d.saving} title={vietato ? w('notForBlocked') : undefined} onClick={() => a.esito(e)}>{statoTesto(e)}</button>;
        })}
      </div>
      <div className="dc-fields">
        <label className="dc-fld"><span>{w('pctLabel')}</span>
          <input value={d.pct} onChange={e => a.pct(e.target.value)} inputMode="decimal" data-dc-campo="pct" aria-invalid={!!pctErr}
            title={pctErr || d.suggerimentoFormato} />
          <small className={pctErr ? 'is-bad' : ''}>{pctErr || d.suggerimentoFormato}</small></label>
        <label className="dc-fld"><span>{w('eurLabel')}</span>
          <input value={d.eur} onChange={e => a.eur(e.target.value)} inputMode="decimal" data-dc-campo="eur" aria-invalid={!!eurErr}
            title={eurErr || d.suggerimentoFormato} />
          <small className={eurErr ? 'is-bad' : ''}>{eurErr || w('eurHint')}</small></label>
        <label className="dc-fld is-full"><span>{w('commentLabel')} <em>· {w('optional')}</em></span>
          <textarea value={d.feedback} onChange={e => a.feedback(e.target.value)} rows={2} data-dc-campo="feedback"
            placeholder={d.esito === 'SKIPPED' ? w('commentPhSkip') : w('commentPh')} /></label>
      </div>
      <div className="dc-row-act">
        <button type="button" className="bbn-btn is-primary" data-dc-azione="conferma" disabled={d.saving} onClick={a.confermaEsito}>
          <Check size={15} aria-hidden="true" />{w('confirm', { status: statoTesto(d.esito).toLowerCase() })}</button>
        {decisioneCompatibile(x, x.ticker, x.action) && <button type="button" className="bbn-btn" data-dc-azione="collega" onClick={() => a.collegaTrade(x)}>
          <Plus size={15} aria-hidden="true" />{w('linkTrade')}</button>}
        {statoDivergenza(x) !== null && <button type="button" className="bbn-btn" data-dc-azione="divergenza" onClick={() => a.divergenza(x)}>{w('record_manual_divergence')}</button>}
        {chiusa && !x.veto && !archiviata && <button type="button" className="bbn-btn" data-dc-azione="riapri" disabled={d.saving} onClick={a.riapri}>
          <RotateCcw size={15} aria-hidden="true" />{w('reopen')}</button>}
      </div>
    </div>
  );
}

function Veto({ d, a, x }: { d: DatiDecisioni; a: AzioniDecisioni; x: Decision }) {
  if (x.veto) return null;
  const pronto = !!d.vetoReason.trim();
  return (
    <details className="dc-veto">
      <summary><Ban size={16} aria-hidden="true" />{w('vetoTitle')}<span className="bbn-card-note">{w('vetoNote')}</span></summary>
      <div className="dc-veto-in">
        <p>{w('vetoText')}</p>
        <label className="dc-fld"><span>{w('vetoReason')} <em>· {w('required')}</em></span>
          <input value={d.vetoReason} onChange={e => a.vetoReason(e.target.value)} data-dc-campo="veto" placeholder={w('vetoPh')} /></label>
        <div className="dc-row-act">
          <button type="button" className="bbn-btn is-danger" data-dc-azione="veto" disabled={d.saving || !pronto}
            onClick={() => a.apriDialogo({ tipo: 'veto', id: x.id })}><Ban size={15} aria-hidden="true" />{w('vetoApply')}</button>
          <span className="bbn-card-note">{pronto ? w('vetoAsks') : w('vetoNeedsReason')}</span>
        </div>
      </div>
    </details>
  );
}

function DettaglioOperativa({ d, a, x }: { d: DatiDecisioni; a: AzioniDecisioni; x: Decision }) {
  const hold = isHold(x), chiusa = x.status !== 'PENDING', archiviata = d.vista === 'arch';
  const rischio = x.assessment_status ? RISCHIO[x.assessment_status] : null;
  const quarto: [string, ReactNode, ReactNode, Tono?] = chiusa && x.outcome_pct != null
    ? [w('tOutcome'), `${pctTesto(d.lingua, x.outcome_pct)}${x.outcome_eur != null ? ' · ' + euro(d.lingua, x.outcome_eur, true) : ''}`, w('tOutcomeSub'), x.outcome_pct >= 0 ? 'ok' : 'bad']
    : rischio ? [w('tRisk'), w(rischio[0]), x.assessment_reason || '', rischio[1]] : [w('tRisk'), w('rNone'), w('rNoneSub')];
  const blocco = statoDivergenza(x);
  const es = x.esecuzione;
  return <>
    <Testata d={d} a={a} x={x} />
    <div className="dc-det-body">
      <Riquadri voci={[
        hold && !x.eur_amount ? [w('tAmount'), w('tAmountNone'), w('tAmountHold')] : [w('tAmount'), euro(d.lingua, x.eur_amount), `${azioneTesto(x.action).toLowerCase()} · ${x.ticker}`],
        [w('tConviction'), x.confidence || w('na'), w('tConvictionSub')],
        [w('tHorizon'), x.timing || w('na'), w('tHorizonSub')],
        quarto,
      ]} />
      {blocco && <div className="dc-note is-bad" data-dc-blocco={blocco}><TriangleAlert size={16} aria-hidden="true" />
        <span><b>{w(TITOLO_BLOCCO[blocco])}</b> {x.assessment_reason ? x.assessment_reason + '. ' : ''}{w('blockedWhy')}</span></div>}
      {!!x.veto && <div className="dc-note is-bad dc-center" data-dc-veto-attivo={x.id}><Ban size={16} aria-hidden="true" />
        <span><b>{w('vetoBanner', { date: data(d.lingua, x.veto_at) })}</b> «{x.veto_reason}» {w('vetoBannerWhy', { id: x.id })}</span>
        <button type="button" className="bbn-link" data-dc-azione="revoca" disabled={d.saving} onClick={() => a.apriDialogo({ tipo: 'revoca', id: x.id })}>{w('revoke')}</button></div>}
      {es?.inferito && x.status === 'PENDING' && <div className="dc-note is-warn"><TriangleAlert size={16} aria-hidden="true" /><span>{w('execInferredNote')}</span></div>}
      <Provenienza x={x} />
      <Fissata d={d} a={a} x={x} />
      <div className="dc-cols">
        <div className="dc-col">
          <Sezione titolo={w('rationale')} nota={w('rationaleNote')}>
            <p className="dc-prose">{x.rationale || w('rationaleNone')}</p>
            {x.rationale && <span className="dc-mini">{w('archivedText')}</span>}
          </Sezione>
          {es && <Sezione titolo={w('execution')} nota={w(es.inferito ? 'execInferred' : 'execExplicit')}>
            <dl className="dc-kv"><dt>{w('execAmount')}</dt><dd>{es.pct != null ? w('execShare', { eur: euro(d.lingua, es.eur), pct: fmt(d.lingua, es.pct, 1) }) : w('execShareNa', { eur: euro(d.lingua, es.eur) })}</dd>
              <dt>{w('execTrades')}</dt><dd>{es.trade_ids.map(id => `#${id}`).join(', ')}</dd></dl>
          </Sezione>}
          {x.pm_feedback && <Sezione titolo={w('pmComment')}><p className="dc-quote">{x.pm_feedback}</p></Sezione>}
          {x.outcome_notes && <Sezione titolo={w('outcomeNotes')}><p className="dc-quote">{x.outcome_notes}</p></Sezione>}
          {!!x.manual_divergences?.length && <Sezione titolo={w('manual_divergence_title')}>
            {x.manual_divergences.map(ev => <p key={ev.id} className="dc-quote">{w('manual_divergence_trade', {
              id: ev.details.trade_id, action: ev.details.trade_action, ticker: ev.details.ticker_eseguito,
              date: data(d.lingua, ev.details.trade_data, true), reason: ev.reason,
            })}{ev.details.isin ? w('manual_divergence_isin', { isin: ev.details.isin }) : ''}</p>)}
          </Sezione>}
          <Cronologia d={d} x={x} />
          {!hold && <StessoTitolo d={d} a={a} x={x} />}
        </div>
        <div className="dc-col">
          {/* 06/10 (PM): Chiudi, Veto e Collega trade anche in Archivio, come prima del restyle: un esito
              tardivo si registra senza «Riporta in pagina». Stessi blocchi, stesse chiamate, la voce resta archiviata. */}
          {archiviata && <div className="dc-note"><Archive size={16} aria-hidden="true" /><span>{w('archiveOpsNote')}</span></div>}
          <ChiudiDecisione d={d} a={a} x={x} />
          <Veto d={d} a={a} x={x} />
          {hold && <StessoTitolo d={d} a={a} x={x} />}
        </div>
      </div>
    </div>
  </>;
}

/* ── dettaglio ricerca: conversazione nello stile di Chat agenti ──── */
function Turno({ d, n }: { d: DatiDecisioni; n: DecisionNote }) {
  if (n.autore === 'PM') return (
    <div className="dc-pmq" data-dc-nota={n.id}><div className="q">{n.testo}</div><span className="tag">{w('you')} · {data(d.lingua, n.timestamp, true)}</span></div>
  );
  return (
    <div className="dc-turn" data-dc-nota={n.id}>
      <div className="dc-ansh"><span className="dc-bot" aria-hidden="true"><Bot size={14} /></span><span className="nm">{w('committee')}</span>
        <span className="when">{w('noteTurn', { date: data(d.lingua, n.timestamp, true) })}</span></div>
      <p className="dc-prose">{n.testo}</p>
    </div>
  );
}

function DettaglioRicerca({ d, a, x }: { d: DatiDecisioni; a: AzioniDecisioni; x: Decision }) {
  const note = [...(x.notes || [])].sort((p, q) => (p.timestamp || '').localeCompare(q.timestamp || ''));
  const archiviata = d.vista === 'arch', aperta = x.status === 'PENDING' && !archiviata;
  const ferma = ricercaFerma(x);
  return <>
    <Testata d={d} a={a} x={x} />
    <div className="dc-det-body">
      <div className="dc-cond"><Target size={16} aria-hidden="true" /><span><b>{w('condition')}</b>{x.timing || w('conditionNone')}</span></div>
      {ferma && <div className="dc-note is-warn" data-dc-ferma={x.id}><TriangleAlert size={16} aria-hidden="true" />
        <span><b>{w('staleTitle', { n: giorniDa(ultimaAttivita(x)) ?? 0 })}</b> {w('staleWhy')}</span></div>}
      {!archiviata && x.status !== 'PENDING' && <div className="dc-note"><TriangleAlert size={16} aria-hidden="true" />
        <span><b>{w('notWorked')}</b> {w('notWorkedWhy', { status: statoTesto(x.status).toLowerCase() })}</span></div>}
      {archiviata && <div className="dc-note"><Archive size={16} aria-hidden="true" /><span>{w('archiveResNote')}</span></div>}
      <Provenienza x={x} />
      <Fissata d={d} a={a} x={x} />
      {x.outcome_notes && <p className="dc-quote">{x.outcome_notes}</p>}
      <Sezione titolo={w('conversation')} nota={conta('notesCount', note.length)}>{null}</Sezione>
      <div className="dc-conv" data-dc-conv={x.id}>
        {x.rationale && <div className="dc-turn">
          <div className="dc-ansh"><span className="dc-bot" aria-hidden="true"><Bot size={14} /></span><span className="nm">{w('committee')}</span>
            <span className="when">{x.memo_id != null ? w('fromMemoTurn', { id: x.memo_id, date: data(d.lingua, x.timestamp, true) }) : data(d.lingua, x.timestamp, true)}</span></div>
          <p className="dc-prose">{x.rationale}</p>
          <span className="dc-mini">{w('archivedText')}</span>
        </div>}
        {note.map(n => <Turno key={n.id} d={d} n={n} />)}
        {!note.length && aperta && <div className="dc-empty"><b>{w('noNotes')}</b>{w('noNotesWhy')}</div>}
      </div>
      {aperta && <>
        <div className="dc-comp">
          <input value={d.nota} onChange={e => a.nota(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') a.inviaNota(); }}
            placeholder={w('composerPh')} aria-label={w('composerPh')} data-dc-campo="nota" />
          <button type="button" className="bbn-btn is-primary is-sm" data-dc-azione="invia" disabled={d.saving || !d.nota.trim()} onClick={a.inviaNota}>
            <Send size={14} aria-hidden="true" />{w('send')}</button>
        </div>
        <span className="dc-comp-note">{w('composerNote')}</span>
      </>}
    </div>
  </>;
}

function Dettaglio({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  const x = d.sel;
  let corpo: ReactNode;
  if (d.loadErr !== null || (d.loading && !d.decisions.length) || !x)
    corpo = <div className="dc-det-body dc-det-empty"><div className="dc-empty">{d.loading && !d.decisions.length ? w('loading') : w('emptyDetail')}</div></div>;
  else corpo = isResearch(x) ? <DettaglioRicerca d={d} a={a} x={x} /> : <DettaglioOperativa d={d} a={a} x={x} />;
  return <section className="bbn-card dc-card dc-det" data-dc-dettaglio={x?.id ?? ''}>{corpo}</section>;
}

/* ── dialoghi e avvisi ────────────────────────────────────────────── */
function DialogoConferma({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  const dlg = d.dialogo;
  if (!dlg) return null;
  let titolo: string, testo: string, ok: string, pericolo = false, extra: ReactNode = null;
  if (dlg.tipo === 'hold') {
    const n = d.gruppi.conferme.filter(x => !d.holdEsclusi.includes(x.id)).length;
    titolo = conta('holdDlgTitle', n); testo = w('holdDlgText'); ok = w('holdDlgOk', { n });
    extra = <div className="dc-dlg-tk">{d.gruppi.conferme.map(x => {
      const fuori = d.holdEsclusi.includes(x.id);
      return <button key={x.id} type="button" className={'dc-pill ' + (fuori ? 'is-off is-out' : 'is-acc')} aria-pressed={!fuori}
        title={w(fuori ? 'holdDlgInclude' : 'holdDlgExclude', { ticker: x.ticker })} onClick={() => a.escludiHold(x.id)}>
        {x.ticker}{fuori ? <Plus size={12} aria-hidden="true" /> : <X size={12} aria-hidden="true" />}</button>;
    })}</div>;
  } else {
    const x = d.decisions.find(y => y.id === dlg.id);
    pericolo = true;
    if (dlg.tipo === 'veto') {
      titolo = w('vetoDlgTitle'); testo = w('vetoDlgText', { id: dlg.id, ticker: x?.ticker || '' }); ok = w('vetoDlgOk');
      extra = <p className="dc-quote">«{d.vetoReason.trim()}»</p>;
    } else { titolo = w('revokeDlgTitle'); testo = w('revokeDlgText', { id: dlg.id, ticker: x?.ticker || '' }); ok = w('revokeDlgOk'); }
  }
  return (
    <div className="dc-scrim" onClick={e => { if (e.target === e.currentTarget) a.chiudiDialogo(); }}>
      <div className="dc-dlg" role="dialog" aria-modal="true" aria-labelledby="dc-dlg-t" data-dc-dialogo={dlg.tipo}
        onKeyDown={e => { if (e.key === 'Escape') a.chiudiDialogo(); }}>
        <h3 id="dc-dlg-t">{titolo}</h3><p>{testo}</p>{extra}
        <div className="dc-dlg-a">
          <button type="button" className="bbn-btn" data-dc-azione="annulla-dialogo" autoFocus={pericolo} onClick={a.chiudiDialogo}>{w('cancel')}</button>
          <button type="button" className={'bbn-btn ' + (pericolo ? 'is-danger' : 'is-primary')} data-dc-azione="ok-dialogo" autoFocus={!pericolo}
            disabled={d.saving || (dlg.tipo === 'hold' && d.gruppi.conferme.every(x => d.holdEsclusi.includes(x.id)))} onClick={a.okDialogo}>{ok}</button>
        </div>
      </div>
    </div>
  );
}

function Avviso({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  if (!d.avviso) return null;
  return (
    <div className={'dc-toast' + (d.avviso.errore ? ' is-bad' : '')} role={d.avviso.errore ? 'alert' : 'status'} data-dc-avviso={d.avviso.errore ? 'errore' : 'ok'}>
      <span className="dc-toast-i" aria-hidden="true">{d.avviso.errore ? <TriangleAlert size={14} /> : <Check size={14} />}</span>
      <span>{d.avviso.testo()}</span>
      <button type="button" className="bbn-icon-btn" aria-label={w('close')} onClick={a.chiudiAvviso}><X size={14} aria-hidden="true" /></button>
    </div>
  );
}

export default function VistaDecisioni({ d, a }: { d: DatiDecisioni; a: AzioniDecisioni }) {
  return (
    <div className="bbn-decisioni">
      <Intestazione d={d} a={a} />
      <DaFare d={d} a={a} />
      {d.targetMancante && <p className="dc-note is-warn">{w('notFound', { id: d.target ?? '' })}</p>}
      {d.stimate > 0 && <p className="dc-note">{w('archiveEstimated', { n: d.stimate })}</p>}
      <div className="dc-body">
        <Elenco d={d} a={a} />
        <Dettaglio d={d} a={a} />
      </div>
      <Avviso d={d} a={a} />
      <DialogoConferma d={d} a={a} />
    </div>
  );
}
