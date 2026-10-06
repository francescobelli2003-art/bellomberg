/* Viste della pagina Movimenti (mockup approvato il 05/10/2026, variante A «dettaglio fisso»):
   intestazione a pillole, Attività per mese | Realizzato, Registro (o Diario) | Dettaglio.
   Grammatica di Notizie e Filing: card bbn-card, nota accanto al titolo, riquadri numerici,
   righe a pillola, intestazioni di mese fisse. Solo presentazione: dati e azioni da MovementsPage. */
import type { ReactNode } from 'react';
import { BarChart3, BookOpen, Coins, ExternalLink, List, Loader2, RefreshCw, Search, TriangleAlert, Wallet, X } from 'lucide-react';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';
import { localeDi } from '@/i18n/lingua';
import { t as traduciStorico } from '@/i18n/t';
import { fmtNum } from '@/lib/format';
import type { MovimentoCassa } from '@/lib/api';
import {
  chiaveMese, controvalore, entra, esce, ggmmaa, legameMovimento, oraTrade, testiDi, testiDiRiga, versoDi,
  type Arco, type Corsia, type Flussi, type Mese, type Realizzato, type RigaRegistro, type StatoCancello, type Trade,
} from '@/lib/movimenti';
import { FILTRI, fraseMese, type Attivita, type Contributo, type FiltroMov, type VistaMov } from './calcoli';
import { parole, type ChiavePagina, type Parole } from './parole';

export interface RigaVista { r: RigaRegistro; k: string }

export interface DatiMovimenti {
  loading: boolean;
  err: string | null;
  errCassa: string | null;
  lettoT: boolean;
  lettoC: boolean;
  lettoAlle: string | null;
  trades: Trade[];
  cassa: MovimentoCassa[];
  /** tutte le righe lette, con la loro chiave */
  tutte: RigaVista[];
  /** le righe che passano filtro, mese, titolo e ricerca */
  righe: RigaVista[];
  mesi: Mese[];
  conteggi: Record<FiltroMov, number>;
  filtro: FiltroMov;
  mese: string | null;
  titolo: string | null;
  q: string;
  sel: RigaVista | null;
  /** la card di sinistra: Registro o Diario */
  vista: VistaMov;
  /** le righe del Diario: tutte quelle lette con un testo, dalla più recente (filtri esclusi) */
  diario: RigaVista[];
  nMov: number;
  nTitoli: number;
  finestra: boolean;
  finestraCassa: boolean;
  limite: number;
  limiteCassa: number;
  flussi: Flussi;
  realizzato: Realizzato | null;
  cancello: StatoCancello;
  osservabileT: boolean;
  arco: Arco | null;
  corsie: Corsia[];
  oreFinte: number;
  attivita: Attivita;
  contributi: Contributo[];
  /** la chiave di registro di un trade (per la storia del titolo) */
  chiaveDi: (t: Trade) => string | undefined;
}

export interface AzioniMovimenti {
  aggiorna: () => void;
  filtro: (f: FiltroMov) => void;
  mese: (k: string | null) => void;
  titolo: (t: string | null) => void;
  cerca: (q: string) => void;
  vista: (v: VistaMov) => void;
  scegli: (k: string) => void;
  azzera: () => void;
}

// ── formati ────────────────────────────────────────────────────
/** Importo nella valuta nativa: «€» solo sull'euro, il codice su tutte le altre (mai € su GBX o USD). */
const importo = (v: number | null | undefined, valuta: string, nd: string) =>
  v == null || !isFinite(v) ? nd : (valuta || '').toUpperCase() === 'EUR' ? `${fmtNum(v, 2)} €` : `${fmtNum(v, 2)} ${valuta || '?'}`;
const firmato = (v: number, dec = 2) => (v > 0 ? '+' : v < 0 ? '−' : '') + fmtNum(Math.abs(v), dec) + ' €';
const compatto = (v: number, locale: string) => {
  const a = Math.abs(v);
  return a >= 1000 ? `${(a / 1000).toLocaleString(locale, { maximumFractionDigits: 1 })}k` : Math.round(a).toLocaleString(locale);
};

function Carta({ icona, titolo, nota, azioni, className = '', children, ...rest }: {
  icona: ReactNode; titolo: ReactNode; nota?: ReactNode; azioni?: ReactNode; className?: string; children: ReactNode;
} & Omit<React.HTMLAttributes<HTMLElement>, 'title'>) {
  return (
    <section className={`bbn-card mv-card ${className}`} {...rest}>
      <header className="bbn-card-head mv-card-head">
        <span className="mv-ci" aria-hidden="true">{icona}</span>
        <h2>{titolo}</h2>
        {nota != null && nota !== '' && <span className="bbn-card-note">{nota}</span>}
        <span className="bbn-grow" />
        {azioni}
      </header>
      {children}
    </section>
  );
}

/** Pillola del verbo: blu entra, viola esce, giallo dividendo, grigio cassa. Verde e rosso restano al P&L. */
/** la parola della pillola, anche per i nomi accessibili: una fonte sola, la pillola e l'etichetta non divergono */
function verboDi(r: RigaRegistro, w: Parole): string {
  if (r.specie === 'cassa') {
    const v = versoDi(r.m.type);
    return v === 'dentro' ? w.t('movementsPage.verbDeposit') : v === 'fuori' ? w.t('movementsPage.verbWithdraw') : w.t('movementsPage.verbCashNd');
  }
  return r.t.action === 'DIVIDEND' ? w.t('movementsPage.verbDividend') : r.t.action;
}

function Verbo({ r, w }: { r: RigaRegistro; w: Parole }) {
  if (r.specie === 'cassa') {
    const v = versoDi(r.m.type);
    return <span className="mv-vb is-cash" title={v === 'ignoto' ? String(r.m.type) : undefined}>{verboDi(r, w)}</span>;
  }
  const a = r.t.action;
  const tono = a === 'DIVIDEND' ? 'is-dv' : esce(a) ? 'is-out' : entra(a) ? 'is-in' : 'is-cash';
  return <span className={`mv-vb ${tono}`} data-mov-verbo={a}>{verboDi(r, w)}</span>;
}

/** Il realizzato di un'uscita: col cancello chiuso o il dato assente è un buco dichiarato, mai uno zero. */
function PillRealizzato({ t, cancello, w }: { t: Trade; cancello: StatoCancello; w: Parole }) {
  if (cancello === 'chiuso' || typeof t.realized_eur !== 'number' || !isFinite(t.realized_eur))
    return <span className="bbn-pill is-piatto" title={cancello === 'chiuso' ? w.t('movementsPage.tRealizedGate') : w.t('movementsPage.tRealizedNd')}>{w.t('movementsPage.nd')}</span>;
  return <PastigliaVariazione valore={t.realized_eur}>{firmato(t.realized_eur)}</PastigliaVariazione>;
}

const legameBreve = (t: Trade, w: Parole) =>
  t.link_origin === 'none' ? w.t('movementsPage.manualShort')
    : t.linked_decision_id != null ? w.t('movementsPage.decisionShort', { n: t.linked_decision_id })
    : w.t('movementsPage.linkNd');

// ── intestazione ───────────────────────────────────────────────
function Intestazione({ d, a, w }: { d: DatiMovimenti; a: AzioniMovimenti; w: Parole }) {
  const qualcosa = d.lettoT || d.lettoC;
  const nT = d.trades.length, nC = d.cassa.length;
  const composizione = !qualcosa ? undefined
    : d.lettoT && d.lettoC ? w.t('movementsPage.chipComposition', { t: nT, c: nC })
    : d.lettoT ? w.t('movementsPage.chipOnlyTrades', { t: nT }) : w.t('movementsPage.chipOnlyCash', { c: nC });
  return (
    <div className="mv-top">
      <h1>{w.t('movementsPage.title')}</h1>
      {/* Elenco | Diario: lo stesso interruttore a segmenti di Diario e Mandato. data-mov-vista è l'identità
          stabile per i test, in entrambe le lingue. */}
      <div className="bbn-seg mv-seg" role="group" aria-label={w.t('movementsPage.viewLabel')}>
        {(['elenco', 'diario'] as const).map(v => (
          <button key={v} type="button" data-mov-vista={v} aria-pressed={d.vista === v} className={d.vista === v ? 'is-on' : undefined}
            onClick={() => a.vista(v)}>{v === 'elenco' ? w.t('movementsPage.viewList') : w.t('movementsPage.viewDiary')}</button>
        ))}
      </div>
      <span className="bbn-chip" data-mov-chip="movimenti" title={composizione}>
        <b>{qualcosa ? d.nMov : w.t('movementsPage.nd')}</b> {qualcosa ? w.n('movementsPage.chipMoves_one', 'movementsPage.chipMoves_other', d.nMov) : w.n('movementsPage.chipMoves_one', 'movementsPage.chipMoves_other', 2)}
        {' · '}<b>{d.lettoT ? d.nTitoli : w.t('movementsPage.nd')}</b> {w.n('movementsPage.chipSecurities_one', 'movementsPage.chipSecurities_other', d.lettoT ? d.nTitoli : 2)}
      </span>
      {composizione && !(d.lettoT && d.lettoC) && <span className="bbn-chip is-warn" data-mov-chip="composizione">{composizione}</span>}
      <span className="bbn-chip" data-mov-chip="arco">
        {d.arco ? <>{w.n('movementsPage.chipSpan_one', 'movementsPage.chipSpan_other', d.arco.giorni)} · {w.t('movementsPage.chipSpanFrom', { d: ggmmaa(d.arco.da) })}</> : w.t('movementsPage.chipSpanNd')}
      </span>
      {!d.lettoC
        ? <span className="bbn-chip is-warn" data-mov-chip="flussi">{w.t('movementsPage.flowsNd')}</span>
        : <span className="bbn-chip" data-mov-chip="flussi" title={w.t('movementsPage.flowsHint')}>
            {w.t('movementsPage.flows')} <b className="num">{firmato(d.flussi.netto)}</b> · {w.t('movementsPage.flowsNotBalance')}
            {d.finestraCassa && <> · {w.t('movementsPage.flowsWindow')}</>}
            {d.flussi.ignoti > 0 && <> · {w.n('movementsPage.flowsUncounted_one', 'movementsPage.flowsUncounted_other', d.flussi.ignoti)}</>}
          </span>}
      <span className="bbn-grow" />
      <span className="bbn-chip" data-mov-chip="letto">
        <i className={'mv-dot' + (d.loading ? ' is-live' : d.err || d.errCassa ? ' is-warn' : '')} />
        {d.lettoAlle ? w.t('movementsPage.readAt', { h: d.lettoAlle }) : w.t('movementsPage.readNever')}
      </span>
      <button type="button" className="bbn-btn mv-agg" data-mov-azione="aggiorna" onClick={a.aggiorna}
        disabled={d.loading} aria-label={w.t('movementsPage.refreshLabel')}>
        {d.loading ? <Loader2 size={15} className="mv-spin" aria-hidden="true" /> : <RefreshCw size={15} aria-hidden="true" />}
        {d.loading ? w.t('movementsPage.refreshing') : w.t('movementsPage.refresh')}
      </button>
    </div>
  );
}

/** Avvisi di lettura: archivio in errore, righe vecchie, caricamento. Ogni archivio dice il suo. */
function Avvisi({ d, w }: { d: DatiMovimenti; w: Parole }) {
  const vuotoRegistro = d.tutte.length === 0;
  if (d.loading && vuotoRegistro && !d.err && !d.errCassa) {
    return <div className="mv-note" role="status" data-mov-stato="caricamento">
      <Loader2 size={16} className="mv-spin" aria-hidden="true" />
      <span><b>{w.t('movementsPage.loading')}</b> · {w.t('movementsPage.loadingDetail')}</span></div>;
  }
  if (!d.err && !d.errCassa) return null;
  if (vuotoRegistro) {
    return <div className="mv-note is-bad" role="alert" data-mov-stato="ko">
      <TriangleAlert size={16} aria-hidden="true" />
      <span>
        <b>{d.err && d.errCassa ? w.t('movementsPage.unavailable') : w.t('movementsPage.incomplete')}.</b>{' '}
        {d.err ? w.t('movementsPage.tradeError', { e: d.err }) : d.lettoT ? w.t('movementsPage.tradeReadEmpty') : ''}{' '}
        {d.errCassa ? w.t('movementsPage.cashError', { e: d.errCassa }) : d.lettoC ? w.t('movementsPage.cashReadEmpty') : ''}{' '}
        {w.t('movementsPage.readFailedHelp')}{(d.lettoT || d.lettoC) && <> {w.t('movementsPage.headerOld')}</>}
      </span></div>;
  }
  /* dati già a schermo e una lettura fallita: lo si dice, archivio per archivio */
  return <div className="mv-note is-warn" role="alert" data-mov-stato="vecchio">
    <TriangleAlert size={16} aria-hidden="true" />
    <span>
      {d.err && <><b>{d.lettoT ? w0('staleSecurities') : w0('unreadSecurities')}</b> — {d.err}. </>}
      {d.errCassa && <><b>{d.lettoC ? w0('staleCash') : w0('unreadCash')}</b> — {d.errCassa}. </>}
    </span></div>;
}
/* le frasi sugli archivi vecchi o non letti restano quelle del catalogo storico (movements.*) */
const w0 = (k: 'staleSecurities' | 'unreadSecurities' | 'staleCash' | 'unreadCash') => traduciStorico(`movements.${k}`);

// ── attività per mese ──────────────────────────────────────────
function CardAttivita({ d, a, w }: { d: DatiMovimenti; a: AzioniMovimenti; w: Parole }) {
  const locale = localeDi(w.lingua);
  const { mesi } = d.attivita;
  const max = Math.max(1, ...mesi.flatMap(m => [m.entrate, m.uscite, Math.abs(m.cassa)]));
  const h = (v: number) => (v ? Math.max(2, Math.round(Math.abs(v) / max * 100)) : 0);
  const nomeMese = (k: string, lungo = false) => {
    const s = new Intl.DateTimeFormat(locale, { month: lungo ? 'long' : 'short' }).format(new Date(Number(k.slice(0, 4)), Number(k.slice(5, 7)) - 1, 1)).replace('.', '');
    return s.charAt(0).toLocaleUpperCase(locale) + s.slice(1) + (lungo ? ` ${k.slice(0, 4)}` : ` ${k.slice(2, 4)}`);
  };
  const avvisi = [
    d.attivita.altreValute > 0 && w.n('movementsPage.activityOtherCcy_one', 'movementsPage.activityOtherCcy_other', d.attivita.altreValute),
    d.attivita.senzaData > 0 && w.n('movementsPage.activityUnknownDate_one', 'movementsPage.activityUnknownDate_other', d.attivita.senzaData),
    d.attivita.tagliati && w.t('movementsPage.activityLast', { n: mesi.length }),
    (d.lettoT || d.lettoC) && !(d.lettoT && d.lettoC) && (d.lettoT ? w.t('movementsPage.onlyTrades') : w.t('movementsPage.onlyCash')),
    d.finestra && w.t('movementsPage.tradeWindow', { n: d.limite }),
    d.finestraCassa && w.t('movementsPage.cashWindow', { n: d.limiteCassa }),
  ].filter(Boolean) as string[];
  return (
    <Carta icona={<BarChart3 size={16} />} titolo={w.t('movementsPage.activity')} nota={w.t('movementsPage.activityNote')} className="mv-attivita"
      azioni={<span className="mv-legend" aria-hidden="true">
        <span><i className="is-in" />{w.t('movementsPage.legIn')}</span><span><i className="is-out" />{w.t('movementsPage.legOut')}</span><span><i className="is-cash" />{w.t('movementsPage.legCash')}</span>
      </span>}>
      <div className="mv-card-body">
        {mesi.length === 0
          ? <p className="mv-empty">{(d.lettoT || d.lettoC) ? w.t('movementsPage.activityEmpty') : w.t('movementsPage.nd')}</p>
          : <div className="mv-months" role="group" aria-label={w.t('movementsPage.activity')}>
              {mesi.map(m => (
                <button key={m.chiave} type="button" className="mv-mon" data-mov-mese={m.chiave} aria-pressed={d.mese === m.chiave}
                  disabled={m.n === 0} onClick={() => a.mese(d.mese === m.chiave ? null : m.chiave)}
                  aria-label={w.t('movementsPage.monthAria', { m: nomeMese(m.chiave, true), a: `${fmtNum(m.entrate, 0)} €`, b: `${fmtNum(m.uscite, 0)} €`, c: firmato(m.cassa, 0), n: m.n })}>
                  <span className="mv-bars" aria-hidden="true">
                    <i className="mv-b is-in" style={{ height: `${h(m.entrate)}%` }}>{m.entrate > 0 && <span>{compatto(m.entrate, locale)}</span>}</i>
                    <i className="mv-b is-out" style={{ height: `${h(m.uscite)}%` }}>{m.uscite > 0 && <span>{compatto(m.uscite, locale)}</span>}</i>
                    <i className="mv-b is-cash" style={{ height: `${h(m.cassa)}%` }}>{m.cassa !== 0 && <span>{(m.cassa > 0 ? '+' : '−') + compatto(m.cassa, locale)}</span>}</i>
                  </span>
                  <span className="mv-mon-l">{nomeMese(m.chiave)}</span>
                  <span className="mv-mon-s">{w.t('movementsPage.monthSub', { n: m.n, t: w.n('movementsPage.monthSubSec_one', 'movementsPage.monthSubSec_other', m.nTitoli) })}</span>
                </button>
              ))}
            </div>}
        {avvisi.length > 0 && <p className="mv-foot">{avvisi.join(' · ')}</p>}
      </div>
    </Carta>
  );
}

// ── realizzato ─────────────────────────────────────────────────
function CardRealizzato({ d, a, w }: { d: DatiMovimenti; a: AzioniMovimenti; w: Parole }) {
  const r = d.realizzato;
  let corpo: ReactNode;
  if (!d.lettoT) corpo = <p className="mv-empty" data-mov-realizzato="nd">{w.t('movementsPage.realizedUnread')}</p>;
  else if (!d.osservabileT) corpo = <p className="mv-empty" data-mov-realizzato="nessuna-riga">{w.t('movementsPage.realizedNoRows')}</p>;
  else if (r === null) corpo = <p className="mv-empty" data-mov-realizzato="cancello">{w.t('movementsPage.realizedGate')}</p>;
  else if (r.stato === 'vuoto') corpo = <p className="mv-empty" data-mov-realizzato="vuoto">{w.t('movementsPage.realizedEmpty')}</p>;
  else {
    const tono = r.somma > 0 ? 'is-up' : r.somma < 0 ? 'is-down' : '';
    const maxC = Math.max(1e-9, ...d.contributi.map(c => Math.abs(c.somma)));
    corpo = <>
      <div className="mv-rz-v" data-mov-realizzato="ok"><b className={`num ${tono}`}>{firmato(r.somma)}</b><span>{w.n('movementsPage.realizedOn_one', 'movementsPage.realizedOn_other', r.n)}</span></div>
      <div className="mv-split" aria-hidden="true">
        {r.vinte > 0 && <i className="is-up" style={{ flex: r.vinte }} />}
        {r.pari > 0 && <i className="is-flat" style={{ flex: r.pari }} />}
        {r.perse > 0 && <i className="is-down" style={{ flex: r.perse }} />}
      </div>
      <div className="mv-split-l">
        <span className="is-up">{w.t('movementsPage.realizedWins', { n: r.vinte })}</span>
        {r.pari > 0 && <span>{w.t('movementsPage.realizedFlat', { n: r.pari })}</span>}
        <span className="is-down">{w.t('movementsPage.realizedLosses', { n: r.perse })}</span>
      </div>
      {d.contributi.length > 0 && <div className="mv-contrib">
        {d.contributi.map(c => (
          <button key={c.ticker} type="button" className="mv-crow" data-mov-titolo={c.ticker} aria-pressed={d.titolo === c.ticker}
            title={w.t('movementsPage.realizedFilter', { t: c.ticker })} onClick={() => a.titolo(d.titolo === c.ticker ? null : c.ticker)}>
            <IconaTitolo ticker={c.ticker} dimensione="xs" />
            <span className="mv-crow-t"><b>{c.ticker}</b> <span>· {Math.round(c.quota * 100)}%</span></span>
            <span className="mv-track"><i className={c.somma >= 0 ? 'is-up' : 'is-down'} style={{ width: `${Math.abs(c.somma) / maxC * 100}%` }} /></span>
            <span className={`mv-crow-v num ${c.somma > 0 ? 'is-up' : c.somma < 0 ? 'is-down' : ''}`}>{firmato(c.somma, 0)}</span>
          </button>
        ))}
      </div>}
      {r.quotaMaggiore > 0 && <p className="mv-foot">{w.t('movementsPage.realizedConcentration', { t: r.tickerMaggiore, p: Math.round(r.quotaMaggiore * 100) })}</p>}
    </>;
  }
  return <Carta icona={<Coins size={16} />} titolo={w.t('movementsPage.realized')} nota={w.t('movementsPage.realizedNote')} className="mv-realizzato">
    <div className="mv-card-body">{corpo}</div>
  </Carta>;
}

// ── registro ───────────────────────────────────────────────────
const ETICHETTA_FILTRO: Record<FiltroMov, ChiavePagina> = {
  TUTTI: 'movementsPage.fAll', BUY: 'movementsPage.fBuy', ADD: 'movementsPage.fAdd', USCITE: 'movementsPage.fOut',
  DIVIDEND: 'movementsPage.fDividend', CASSA: 'movementsPage.fCash', COMMENTO: 'movementsPage.fComment',
};

function Riga({ v, d, a, w }: { v: RigaVista; d: DatiMovimenti; a: AzioniMovimenti; w: Parole }) {
  const { r, k } = v;
  const { rationale, nota, vuoto } = testiDiRiga(r);
  const corrente = d.sel?.k === k;
  if (r.specie === 'cassa') {
    const m = r.m, verso = versoDi(m.type), val = m.amount_eur;
    const leggibile = typeof val === 'number' && isFinite(val);
    return (
      <button type="button" className={'mv-row is-cash' + (corrente ? ' is-on' : '')} data-mov-row={k} data-specie="cassa"
        aria-current={corrente || undefined} onClick={() => a.scegli(k)}>
        <span className="bbn-ico is-sm mv-ico-cash" aria-hidden="true"><Wallet size={15} /></span>
        <span className="mv-row-tx">
          <span className="mv-l1"><b>{w.t('movementsPage.cash')}</b><Verbo r={r} w={w} /><span className="mv-meta num">{ggmmaa(m.date)}</span></span>
          <span className={'mv-cm' + (vuoto ? ' is-none' : '')}>{nota || w.t('movementsPage.noComment')}</span>
        </span>
        <span className="mv-row-end">
          <span className="mv-ctv num">{leggibile ? (verso === 'dentro' ? '+' : verso === 'fuori' ? '−' : '') + importo(Math.abs(val as number), 'EUR', w.t('movementsPage.nd')) : w.t('movementsPage.amountNd')}</span>
          <small>EUR</small>
        </span>
      </button>
    );
  }
  const t = r.t, ctrl = controvalore(t);
  return (
    <button type="button" className={'mv-row' + (corrente ? ' is-on' : '')} data-mov-row={k} data-specie="titolo"
      aria-current={corrente || undefined} onClick={() => a.scegli(k)}>
      <IconaTitolo ticker={t.ticker} dimensione="sm" />
      <span className="mv-row-tx">
        <span className="mv-l1">
          <b>{t.ticker}</b><Verbo r={r} w={w} />
          <span className="mv-meta num">
            {t.action !== 'DIVIDEND' && <>{t.quantita == null ? w.t('movementsPage.nd') : fmtNum(t.quantita, 0)} × {importo(t.prezzo, t.valuta, w.t('movementsPage.nd'))} · </>}
            {ggmmaa(t.data)} {oraTrade(t)}
          </span>
        </span>
        <span className={'mv-cm' + (vuoto ? ' is-none' : '')}>{rationale || nota || w.t('movementsPage.noComment')}</span>
      </span>
      <span className="mv-row-end">
        <span className="mv-ctv num">{importo(ctrl, t.valuta, w.t('movementsPage.nd'))}</span>
        {esce(t.action) ? <PillRealizzato t={t} cancello={d.cancello} w={w} /> : <small>{legameBreve(t, w)}</small>}
      </span>
    </button>
  );
}

function CardRegistro({ d, a, w }: { d: DatiMovimenti; a: AzioniMovimenti; w: Parole }) {
  const locale = localeDi(w.lingua);
  const perChiave = new Map(d.mesi.map(m => [m.chiave, m]));
  const qualcosa = d.lettoT || d.lettoC;
  const nota = qualcosa ? (d.finestra || d.finestraCassa ? w.t('movementsPage.shownAtLeast', { a: d.righe.length, b: d.nMov }) : w.t('movementsPage.shown', { a: d.righe.length, b: d.nMov })) : undefined;
  const piedi = [
    qualcosa && !(d.lettoT && d.lettoC) && (d.lettoT ? w.t('movementsPage.onlyTrades') : w.t('movementsPage.onlyCash')),
    d.finestra && w.t('movementsPage.tradeWindow', { n: d.limite }),
    d.finestraCassa && w.t('movementsPage.cashWindow', { n: d.limiteCassa }),
    d.osservabileT && d.cancello === 'chiuso' && w.t('movementsPage.realizedMissing'),
    d.oreFinte > 0 && w.t('movementsPage.conventional', { a: d.oreFinte, b: d.trades.length }),
  ].filter(Boolean) as string[];
  const vuotoArchivio = !d.loading && d.lettoT && d.lettoC && !d.err && !d.errCassa && d.tutte.length === 0;
  const annuncio = !qualcosa ? '' : d.loading ? w.t('movementsPage.loading')
    : vuotoArchivio ? w.t('movementsPage.empty')
    : d.tutte.length > 0 && d.righe.length === 0 ? w.t('movementsPage.emptyFilter') : nota ?? '';
  let corrente: string | null = null;
  return (
    <Carta icona={<List size={16} />} titolo={w.t('movementsPage.register')} nota={<>{w.t('movementsPage.registerNote')}{nota && <> · {nota}</>}</>} className="mv-registro">
      <div className="mv-fbar" role="group" aria-label={w.t('movementsPage.filtersLabel')}>
        {FILTRI.map(f => (
          <button key={f} type="button" className="mv-fpill" data-mov-filtro={f} aria-pressed={d.filtro === f}
            disabled={!d.conteggi[f] && d.filtro !== f} onClick={() => a.filtro(f)}>
            {w.t(ETICHETTA_FILTRO[f])} <b className="num">{qualcosa ? (d.conteggi[f] || 0) : w.t('movementsPage.nd')}</b>
          </button>
        ))}
        {d.mese && <button type="button" className="mv-fpill is-x" data-mov-togli="mese" aria-label={w.t('movementsPage.clearMonth', { m: d.mese })} onClick={e => { const g = e?.currentTarget?.parentElement; a.mese(null); g?.querySelector<HTMLButtonElement>('[data-mov-filtro]')?.focus(); }}>
          {fraseMese(new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric' }).format(new Date(Number(d.mese.slice(0, 4)), Number(d.mese.slice(5, 7)) - 1, 1)), locale)}
          <X size={13} aria-hidden="true" /></button>}
        {d.titolo && <button type="button" className="mv-fpill is-x" data-mov-togli="titolo" aria-label={w.t('movementsPage.clearTicker', { t: d.titolo })} onClick={e => { const g = e?.currentTarget?.parentElement; a.titolo(null); g?.querySelector<HTMLButtonElement>('[data-mov-filtro]')?.focus(); }}>
          {d.titolo}<X size={13} aria-hidden="true" /></button>}
        <span className="bbn-grow" />
        <label className="mv-field">
          <Search size={14} aria-hidden="true" />
          <input type="search" value={d.q} placeholder={w.t('movementsPage.search')} aria-label={w.t('movementsPage.search')} onChange={e => a.cerca(e.target.value)} />
        </label>
      </div>
      {piedi.length > 0 && <p className="mv-foot mv-foot-reg" data-mov-note="registro">{piedi.join(' ')}</p>}
      <Annuncio testo={annuncio && w.t('movementsPage.statusRegister', { s: annuncio })} dove="registro" />
      <div className="mv-list">
        {vuotoArchivio && <div className="mv-empty-box" data-mov-stato="vuoto"><b>{w.t('movementsPage.empty')}</b>{w.t('movementsPage.emptyDetail')}</div>}
        {d.tutte.length > 0 && d.righe.length === 0 && <div className="mv-empty-box" data-mov-stato="filtro-vuoto">
          <b>{w.t('movementsPage.emptyFilter')}</b>{w.t('movementsPage.emptyFilterDetail', { n: d.nMov })}
          <button type="button" className="bbn-btn" data-mov-azione="azzera" onClick={a.azzera}>{w.t('movementsPage.showAll')}</button></div>}
        {d.righe.map(v => {
          const k = chiaveMese(v.r.quando);
          const testa = k !== corrente ? perChiave.get(k) : undefined;
          if (k !== corrente) corrente = k;
          return <div key={v.k} className="mv-grp-item">
            {testa && <h3 className="mv-mhead" data-mov-mhead={testa.chiave || 'ignota'}>
              {fraseMese(testa.etichetta, locale)}
              <span>{w.n('movementsPage.monthMoves_one', 'movementsPage.monthMoves_other', testa.n)}{testa.nTicker > 0 && <> · {w.n('movementsPage.monthSecurities_one', 'movementsPage.monthSecurities_other', testa.nTicker)}</>}</span>
            </h3>}
            <Riga v={v} d={d} a={a} w={w} />
          </div>;
        })}
      </div>
    </Carta>
  );
}

// ── annunci per il lettore di schermo ──────────────────────────
/* Review 06/10/2026 (accessibilità): la live region stava sulla LISTA intera, e il lettore di schermo rileggeva ogni
   riga (nel Diario ogni commento pieno) a ogni filtro o aggiornamento. Ora annuncia solo questa riga breve, nascosta
   alla vista perché il conteggio si legge già nella nota della card. Senza archivi letti tace: caricamento ed
   errore li dice già l'avviso in alto (role status/alert), e due annunci uguali sarebbero rumore. */
function Annuncio({ testo, dove }: { testo: string; dove: string }) {
  return <p className="mv-sr" role="status" aria-live="polite" aria-atomic="true" data-mov-annuncio={dove}>{testo}</p>;
}

/** id stabile del testo di una voce del Diario, per aria-describedby (la chiave della riga contiene «:») */
const idTesto = (k: string, parte: 'motivo' | 'nota' | 'causale') => `mv-diario-${k.replace(/[^\w-]/g, '-')}-${parte}`;

// ── diario ─────────────────────────────────────────────────────
/* «Le parole del PM» in fila (06/10/2026): torna la vista Diario di prima del restyling (components/movimenti/
   Diario.tsx, tolta il 05/10 con il commit di Movimenti in stile Nuova), con gli stessi dati — `pm_rationale` e
   `note` dei trade, la causale della cassa — e nessuna lettura nuova. Il testo è PIENO, mai troncato: è la
   differenza con la riga del registro, che ne mostra una riga sola.
   Gli stati sono quelli del registro e non si confondono: un archivio non letto non è «0 commenti».
   Nome accessibile BREVE (review 06/10/2026): senza aria-label il nome del pulsante era il commento intero; ora è
   «data · titolo o Cassa · verbo · commento», e il testo pieno resta contenuto visibile, legato con aria-describedby. */
function VoceDiario({ v, d, a, w }: { v: RigaVista; d: DatiMovimenti; a: AzioniMovimenti; w: Parole }) {
  const { r, k } = v;
  const corrente = d.sel?.k === k;
  if (r.specie === 'cassa') {
    const m = r.m, verso = versoDi(m.type), val = m.amount_eur;
    const leggibile = typeof val === 'number' && isFinite(val);
    return (
      <button type="button" className={'mv-voce' + (corrente ? ' is-on' : '')} data-mov-diario={k} data-specie="cassa"
        aria-current={corrente || undefined} onClick={() => a.scegli(k)}
        aria-label={w.t('movementsPage.diaryEntryAriaCash', { d: ggmmaa(m.date), t: w.t('movementsPage.cash'), v: verboDi(r, w) })}
        aria-describedby={idTesto(k, 'causale')}>
        <span className="mv-voce-h">
          <span className="bbn-ico is-sm mv-ico-cash" aria-hidden="true"><Wallet size={15} /></span>
          <b>{w.t('movementsPage.cash')}</b><Verbo r={r} w={w} />
          <span className="mv-meta num">{ggmmaa(m.date)}</span>
          <span className="bbn-grow" />
          <span className="mv-ctv num">{leggibile ? (verso === 'dentro' ? '+' : verso === 'fuori' ? '−' : '') + importo(Math.abs(val as number), 'EUR', w.t('movementsPage.nd')) : w.t('movementsPage.amountNd')}</span>
        </span>
        {/* ⚠️ etichettata «Causale» e mai «Commento del PM»: la causale può averla scritta chi ha importato la
            riga (la stessa avvertenza del Diario di prima) */}
        <span className="mv-voce-tx" id={idTesto(k, 'causale')}><span className="et">{w.t('movementsPage.cashReason')}</span>{testiDiRiga(r).nota}</span>
      </button>
    );
  }
  const t = r.t;
  const { rationale, nota } = testiDi(t);
  // le etichette compaiono solo quando i testi sono due: su uno solo sarebbero rumore, su due dicono quale è quale
  const due = !!rationale && !!nota;
  const descritto = [rationale && idTesto(k, 'motivo'), nota && idTesto(k, 'nota')].filter(Boolean).join(' ');
  return (
    <button type="button" className={'mv-voce' + (corrente ? ' is-on' : '')} data-mov-diario={k} data-specie="titolo"
      aria-current={corrente || undefined} onClick={() => a.scegli(k)}
      aria-label={w.t('movementsPage.diaryEntryAria', { d: ggmmaa(t.data), t: t.ticker, v: verboDi(r, w) })}
      aria-describedby={descritto || undefined}>
      <span className="mv-voce-h">
        <IconaTitolo ticker={t.ticker} dimensione="sm" />
        <b>{t.ticker}</b><Verbo r={r} w={w} />
        <span className="mv-meta num">
          {t.action !== 'DIVIDEND' && <>{t.quantita == null ? w.t('movementsPage.nd') : fmtNum(t.quantita, 0)} × {importo(t.prezzo, t.valuta, w.t('movementsPage.nd'))} · </>}
          {ggmmaa(t.data)} {oraTrade(t)}
        </span>
        <span className="bbn-grow" />
        <span className="mv-ctv num">{importo(controvalore(t), t.valuta, w.t('movementsPage.nd'))}</span>
      </span>
      {rationale && <span className="mv-voce-tx" id={idTesto(k, 'motivo')}>{due && <span className="et">{w.t('movementsPage.reason')}</span>}{rationale}</span>}
      {nota && <span className="mv-voce-tx" id={idTesto(k, 'nota')}>{due && <span className="et">{w.t('movementsPage.note')}</span>}{nota}</span>}
    </button>
  );
}

function CardDiario({ d, a, w }: { d: DatiMovimenti; a: AzioniMovimenti; w: Parole }) {
  const qualcosa = d.lettoT || d.lettoC;
  const n = d.diario.length;
  /* il conteggio si dice solo su ciò che è stato letto: senza archivi letti è n.d., mai zero; con un archivio
     solo è un minimo («almeno»), perché i testi dell'altro archivio non sono noti */
  // completo = entrambi gli archivi letti E nessuna finestra satura: con il tetto raggiunto i commenti piu'
  // vecchi non sono stati letti, quindi il conteggio e' un minimo e il vuoto non e' un diario vuoto
  const completo = d.lettoT && d.lettoC && !d.finestra && !d.finestraCassa;
  const nota = !qualcosa ? w.t('movementsPage.diaryNoteNd')
    : completo ? w.n('movementsPage.diaryCount_one', 'movementsPage.diaryCount_other', n)
    : w.n('movementsPage.diaryCountAtLeast_one', 'movementsPage.diaryCountAtLeast_other', n);
  const piedi = [
    qualcosa && !(d.lettoT && d.lettoC) && (d.lettoT ? w.t('movementsPage.onlyTrades') : w.t('movementsPage.onlyCash')),
    d.finestra && w.t('movementsPage.tradeWindow', { n: d.limite }),
    d.finestraCassa && w.t('movementsPage.cashWindow', { n: d.limiteCassa }),
  ].filter(Boolean) as string[];
  // l'annuncio: lo stato in una riga, mai i testi (la voce del Diario è lunga per costruzione)
  const annuncio = !qualcosa ? '' : d.loading ? w.t('movementsPage.loading')
    : n === 0 ? `${completo ? w.t('movementsPage.diaryEmpty') : w.t('movementsPage.diaryEmptyPartial')} · ${nota}` : nota;
  let stato: ReactNode = null;
  if (!qualcosa) {
    stato = d.loading && !d.err && !d.errCassa
      ? <div className="mv-empty-box" data-mov-stato="diario-caricamento"><Loader2 size={16} className="mv-spin" aria-hidden="true" /><b>{w.t('movementsPage.loading')}</b></div>
      : <div className="mv-empty-box" data-mov-stato="diario-ko"><b>{w.t('movementsPage.diaryUnavailable')}</b>{w.t('movementsPage.diaryUnavailableDetail')}</div>;
  } else if (n === 0 && !d.loading) {
    // vuoto vero solo con ENTRAMBI gli archivi letti; con uno solo il vuoto vale per quello e lo si dice
    stato = completo
      ? <div className="mv-empty-box" data-mov-stato="diario-vuoto"><b>{w.t('movementsPage.diaryEmpty')}</b>{w.t('movementsPage.diaryEmptyDetail', { n: d.nMov })}</div>
      : d.lettoT && d.lettoC
      ? <div className="mv-empty-box" data-mov-stato="diario-parziale"><b>{w.t('movementsPage.diaryEmptyPartial')}</b>{w.t('movementsPage.diaryEmptyDetail', { n: d.nMov })}</div>
      : <div className="mv-empty-box" data-mov-stato="diario-parziale"><b>{w.t('movementsPage.diaryEmptyPartial')}</b>{w.t('movementsPage.diaryEmptyPartialDetail')}</div>;
  }
  return (
    <Carta icona={<BookOpen size={16} />} titolo={w.t('movementsPage.diary')} nota={<>{w.t('movementsPage.diaryNote')} · {nota}</>} className="mv-registro mv-diario">
      {piedi.length > 0 && <p className="mv-foot mv-foot-reg" data-mov-note="diario">{piedi.join(' ')}</p>}
      <Annuncio testo={annuncio && w.t('movementsPage.statusDiary', { s: annuncio })} dove="diario" />
      <div className="mv-list">
        {stato}
        {d.diario.map(v => <VoceDiario key={v.k} v={v} d={d} a={a} w={w} />)}
      </div>
    </Carta>
  );
}

// ── dettaglio ──────────────────────────────────────────────────
function Tessera({ k, v, s, tono }: { k: ReactNode; v: ReactNode; s?: ReactNode; tono?: string }) {
  return <div className="mv-tile"><span className="k">{k}</span><span className={'v num' + (tono ? ' ' + tono : '')}>{v}</span>{s != null && <span className="s">{s}</span>}</div>;
}

function Sezione({ titolo, nota, children }: { titolo: ReactNode; nota?: ReactNode; children: ReactNode }) {
  return <section className="mv-sec">
    <header className="mv-sec-h"><h3>{titolo}</h3>{nota && <span className="bbn-card-note">{nota}</span>}</header>
    {children}
  </section>;
}

function StoriaTitolo({ t, d, a, w }: { t: Trade; d: DatiMovimenti; a: AzioniMovimenti; w: Parole }) {
  const corsia = d.corsie.find(c => c.ticker === t.ticker);
  const mosse = corsia?.mosse ?? [];
  const elenco = mosse.slice().reverse();
  return (
    <Sezione titolo={w.t('movementsPage.history')} nota={w.n('movementsPage.historyNote_one', 'movementsPage.historyNote_other', mosse.length, { t: t.ticker })}>
      {d.arco && mosse.length > 0
        ? <div className="mv-trail" role="img" aria-label={w.n('movementsPage.historyNote_one', 'movementsPage.historyNote_other', mosse.length, { t: t.ticker })} title={w.t('movementsPage.historyScale')}>
            <span className="mv-axis" />
            {mosse.map((m, i) => {
              const dv = m.trade.action === 'DIVIDEND', out = esce(m.trade.action);
              const alt = dv ? 8 : 8 + m.rel * 30;
              return <i key={i} className={'mv-mk ' + (dv ? 'is-dv' : out ? 'is-out' : 'is-in') + (m.trade === t ? ' is-cur' : '')}
                style={{ left: `calc(12px + (100% - 24px) * ${m.f.toFixed(4)})`, height: `${alt}px` }}
                title={w.t('movementsPage.historyMark', { v: m.trade.action, d: ggmmaa(m.trade.data) })} />;
            })}
          </div>
        : <p className="mv-empty">{w.t('movementsPage.historyNoArc')}</p>}
      <div className="mv-hist">
        {elenco.map((m, i) => {
          const k = d.chiaveDi(m.trade);
          const x = m.trade;
          return <button key={i} type="button" className={'mv-hrow' + (x === t ? ' is-on' : '')} data-mov-storia={k}
            disabled={!k} onClick={() => k && a.scegli(k)}>
            <span className="num">{ggmmaa(x.data)}</span>
            <Verbo r={{ specie: 'titolo', quando: x.data, t: x }} w={w} />
            <span className="mv-hq num">{x.action === 'DIVIDEND' ? importo(controvalore(x), x.valuta, w.t('movementsPage.nd'))
              : `${x.quantita == null ? w.t('movementsPage.nd') : fmtNum(x.quantita, 0)} × ${importo(x.prezzo, x.valuta, w.t('movementsPage.nd'))} = ${importo(controvalore(x), x.valuta, w.t('movementsPage.nd'))}`}</span>
            {!esce(x.action) ? <span />
              : d.cancello !== 'chiuso' && typeof x.realized_eur === 'number' && isFinite(x.realized_eur)
                ? <span className={'num ' + (x.realized_eur > 0 ? 'is-up' : x.realized_eur < 0 ? 'is-down' : '')}>{firmato(x.realized_eur)}</span>
                : <span className="num" title={d.cancello === 'chiuso' ? w.t('movementsPage.tRealizedGate') : w.t('movementsPage.tRealizedNd')}>{w.t('movementsPage.nd')}</span>}
          </button>;
        })}
      </div>
    </Sezione>
  );
}

function Dettaglio({ d, a, w }: { d: DatiMovimenti; a: AzioniMovimenti; w: Parole }) {
  const v = d.sel;
  if (!v) return <section className="bbn-card mv-det"><p className="mv-empty mv-det-empty">{w.t('movementsPage.detailEmpty')}</p></section>;
  const r = v.r;
  if (r.specie === 'cassa') {
    const m = r.m, verso = versoDi(m.type), val = m.amount_eur;
    const leggibile = typeof val === 'number' && isFinite(val);
    const causale = (m.note || '').trim();
    return (
      <section className="bbn-card mv-det" data-mov-dettaglio={v.k}>
        <header className="mv-det-head">
          <span className="bbn-ico is-lg mv-ico-cash" aria-hidden="true"><Wallet size={24} /></span>
          <div className="mv-det-t">
            <h2>{w.t('movementsPage.cash')} <Verbo r={r} w={w} /></h2>
            <span>{ggmmaa(m.date)}{m.created_at ? ` · ${w.t('movementsPage.recorded').toLocaleLowerCase(localeDi(w.lingua))} ${m.created_at} UTC` : ''}</span>
          </div>
        </header>
        <div className="mv-det-body">
          <div className="mv-tiles is-two">
            <Tessera k={w.t('movementsPage.amount')} v={leggibile ? (verso === 'dentro' ? '+' : verso === 'fuori' ? '−' : '') + importo(Math.abs(val as number), 'EUR', w.t('movementsPage.nd')) : w.t('movementsPage.amountNd')}
              s={verso === 'dentro' ? w.t('movementsPage.amountIn') : verso === 'fuori' ? w.t('movementsPage.amountOut') : String(m.type)} />
            <Tessera k={w.t('movementsPage.date')} v={ggmmaa(m.date)} s={w.t('movementsPage.dateOnly')} />
          </div>
          <Sezione titolo={w.t('movementsPage.cashReason')} nota={w.t('movementsPage.cashReasonNote')}>
            <div className="mv-quote"><p className={causale ? '' : 'is-none'}>{causale || w.t('movementsPage.cashNoReason')}</p></div>
          </Sezione>
          <p className="mv-note">{w.t('movementsPage.cashExplain')}</p>
        </div>
      </section>
    );
  }
  const t = r.t, ctrl = controvalore(t);
  const { rationale, nota } = testiDi(t);
  const uscita = esce(t.action), rz = t.realized_eur;
  const rzNum = typeof rz === 'number' && isFinite(rz);
  return (
    <section className="bbn-card mv-det" data-mov-dettaglio={v.k}>
      <header className="mv-det-head">
        <IconaTitolo ticker={t.ticker} dimensione="lg" />
        <div className="mv-det-t">
          <h2>{t.ticker} <Verbo r={r} w={w} /></h2>
          <span>{t.valuta} · {ggmmaa(t.data)} {oraTrade(t)} · {legameMovimento(t)}</span>
        </div>
      </header>
      <div className="mv-det-body">
        <div className="mv-tiles">
          <Tessera k={w.t('movementsPage.tQty')} v={t.action === 'DIVIDEND' ? '—' : t.quantita == null ? w.t('movementsPage.nd') : fmtNum(t.quantita, 0)} s={w.t('movementsPage.tQtySub')} />
          <Tessera k={w.t('movementsPage.tPrice')} v={t.action === 'DIVIDEND' ? '—' : importo(t.prezzo, t.valuta, w.t('movementsPage.nd'))} s={t.valuta} />
          <Tessera k={w.t('movementsPage.tNotional')} v={importo(ctrl, t.valuta, w.t('movementsPage.nd'))} s={w.t('movementsPage.tNotionalSub')} />
          <Tessera k={w.t('movementsPage.tRealized')}
            v={!uscita ? '—' : d.cancello === 'chiuso' || !rzNum ? w.t('movementsPage.nd') : firmato(rz as number)}
            tono={uscita && rzNum && d.cancello === 'aperto' ? ((rz as number) > 0 ? 'is-up' : (rz as number) < 0 ? 'is-down' : '') : undefined}
            s={!uscita ? w.t('movementsPage.tRealizedOnlyExits') : d.cancello === 'chiuso' ? w.t('movementsPage.tRealizedGate') : rzNum ? w.t('movementsPage.tRealizedExit') : w.t('movementsPage.tRealizedNd')} />
        </div>
        <div className="mv-cols">
          <div className="mv-col">
            <Sezione titolo={w.t('movementsPage.comment')} nota={w.t('movementsPage.commentNote')}>
              <div className="mv-quote">
                {rationale && <p><span className="et">{w.t('movementsPage.reason')}</span>{rationale}</p>}
                {nota && <p><span className="et">{w.t('movementsPage.note')}</span>{nota}</p>}
                {!rationale && !nota && <p className="is-none">{w.t('movementsPage.noCommentLong')}</p>}
              </div>
            </Sezione>
            <Sezione titolo={w.t('movementsPage.provenance')}>
              <dl className="mv-kv">
                <dt>{w.t('movementsPage.link')}</dt>
                <dd>{legameMovimento(t)}
                  {t.linked_decision_id != null && <a className="bbn-link mv-link" data-mov-decisione={t.linked_decision_id}
                    href={`#/decisions?decision=${encodeURIComponent(String(t.linked_decision_id))}`}>
                    {w.t('movementsPage.openDecision')}<ExternalLink size={13} aria-hidden="true" /></a>}
                </dd>
                <dt>{w.t('movementsPage.time')}</dt><dd>{oraTrade(t)}</dd>
                <dt>{w.t('movementsPage.recorded')}</dt><dd>{t.created_at ? `${t.created_at} UTC` : w.t('movementsPage.recordedNd')}</dd>
              </dl>
            </Sezione>
          </div>
          <div className="mv-col"><StoriaTitolo t={t} d={d} a={a} w={w} /></div>
        </div>
      </div>
    </section>
  );
}

export default function VistaMovimenti({ d, a }: { d: DatiMovimenti; a: AzioniMovimenti }) {
  const w = parole();
  return (
    <div className="bbn-movimenti bbn-font" data-mov-lingua={w.lingua}>
      <Intestazione d={d} a={a} w={w} />
      <Avvisi d={d} w={w} />
      <div className="mv-overview">
        <CardAttivita d={d} a={a} w={w} />
        <CardRealizzato d={d} a={a} w={w} />
      </div>
      <div className="mv-body">
        {d.vista === 'diario' ? <CardDiario d={d} a={a} w={w} /> : <CardRegistro d={d} a={a} w={w} />}
        <Dettaglio d={d} a={a} w={w} />
      </div>
    </div>
  );
}
