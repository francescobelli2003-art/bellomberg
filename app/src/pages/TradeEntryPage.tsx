import { t as tr } from '@/i18n/t';
// F7 TRADE ENTRY "LA CASSA" — impianto scelto dal PM 27/07 sui PNG di
// mockup_f7_trade (opzione A, contro B "il precedente" e C "la forma del libro").
// Spec: docs/superpowers/specs/2026-07-27-f7-trade-entry-la-cassa-design.md
//
// LA DOMANDA A CUI RISPONDE LA PAGINA E' UNA SOLA: posso permettermelo, e cosa
// divento dopo? Legge posizioni, cassa e NAV, dichiarando i dati mancanti.
//
// La logica di validazione (bugfix #164), il congelamento del corpo fra
// conferma e scrittura (Lotto D) e `ConfirmDialog` sono quelli di prima.
//
// ⚠ QUESTA E' LA SECONDA STESURA. La review avversariale del 27/07 (10 agenti
// Opus 5) ha demolito la prima su punti che non erano di forma:
//   · i campi `type="number"` MANGIANO la virgola: `158,50` diventava `15850`
//     senza errore, e il POST partiva. Misurato sull'app viva, locale en-GB.
//   · il precompilato dal book scriveva il rumore float32 di yfinance nel DB.
//   · cliccando una riga e poi DIVIDEND, il prezzo in pence restava in un
//     campo che nel frattempo si chiamava «EUR per azione».
//   · una ricarica fallita lasciava a schermo i numeri vecchi accanto a
//     «non disponibile».
//   · «Il trade NON è stato scritto» veniva affermato anche su un timeout,
//     cioe' proprio quando la scrittura poteva essere avvenuta.
import type { ReactNode } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Bellomberg } from '@/lib/api';
import type { Decision, DecisionsResponse, MktSearchHit, MovimentoCassa, PortfolioSnapshot, Position } from '@/lib/api';
import { portfolioValues } from '@/lib/portfolio-values';
import { congelaAnteprima, corpoIdentita, dataTrade, decisioneCompatibile, legameIniziale, legameTrade, righeLegame, totaliMovimenti, FrontendTradeError, statoDivergenza, etichettaStatoDivergenza, spiegazioneDivergenza } from '@/lib/trade-entry';
import type { TradeRequest, TradePreview, TradeResult } from '@/lib/trade-entry';
import { preparaPosizioneIniziale, congelaPosizioneIniziale, leggiRicevutaPosizione, leggiPosizioniIniziali } from '@/lib/position-opening';
import type { OpeningDraft, OpeningRecord, OpeningResult } from '@/lib/position-opening';
import { useLingua, useT } from '@/i18n/provider';
import ModernPage from '@/components/ModernPage';
import NewInterfaceBoundary from '@/components/NewInterfaceBoundary';
import { linguaCorrente, localeDi, type Lingua } from '@/i18n/lingua';
import ConfirmDialog, { ConfirmRow } from '@/components/ConfirmDialog';
import {
  ENTRA, cambioPer, converti, simula, taglieStoriche, ordinaPerControvalore,
  esitoScrittura, scritturaRifiutata, valutaDiscorde, controMediana, leggiNumero,
  prezzoDaBook, perche, esitoMovimento, leggiRifiuto, leggiDataValuta,
} from '@/lib/cassa';
import type {
  Discordanza, Esito, EsitoMovimento, Ordine, RifiutoCassa, TradeRiga, Verbo,
} from '@/lib/cassa';
import { RefreshCw, Check, AlertOctagon, CheckCircle2, AlertTriangle, HelpCircle, Info, ChevronRight, Link2, ArrowDownLeft, ArrowUpRight, FileText, XCircle } from 'lucide-react';
import Card from '@/components/nuova/Card';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';
import { parole } from './trade/parole';
import './trade-nuova.css';

/** Render the presentational tree below its boundary while keeping its
 * controller hooks in the owning page component. */
function DeferredTradeView({ render }: { render: () => ReactNode }) {
  return render();
}

function TradeViewBoundary({ render }: { render: () => ReactNode }) {
  const language = useLingua();
  return <NewInterfaceBoundary language={language}>
    <DeferredTradeView render={render} />
  </NewInterfaceBoundary>;
}

const ACTIONS: { v: Verbo; label: string }[] = [
  { v: 'BUY', get label() { return tr('trade.buy_help'); } },
  { v: 'ADD', get label() { return tr('trade.add_help'); } },
  { v: 'TRIM', get label() { return tr('trade.trim_help'); } },
  { v: 'SELL', get label() { return tr('trade.sell_help'); } },
  { v: 'DIVIDEND', get label() { return tr('trade.dividend_help'); } },
];
const CURRENCIES = ['EUR', 'USD', 'GBP', 'GBX', 'CHF', 'JPY', 'HKD'];
const LIMITE_TRADES = 200;
const ULTIMI_IN_LISTA = 12;
// Il backend clampa a 1..1000. Serve come COSTANTE e non come letterale nella
// chiamata perche' va confrontata con le righe rese: se ne tornano esattamente
// LIMITE, quella che vedi e' una finestra e non lo storico — e va detto.
// (Stessa cura di MovementsPage:35, dove era una ALTA latente.)
const LIMITE_MOVIMENTI = 200;
type Notice = string | { render: (translate: ReturnType<typeof useT>) => string };
const noticeText = (notice: Notice, translate: ReturnType<typeof useT>) =>
  typeof notice === 'string' ? notice : notice.render(translate);

// ⚠ `fmtNum` di lib/format non forza il raggruppamento, e la locale it-IT usa
// "min2": 39265 diventa "39.265" ma 8053 resta "8053" — una scala che cambia
// regola a meta' tabella. (Difetto di format.ts, riguarda anche le altre
// pagine: voce aperta nel MASTER, non si tocca globalmente da qui.)
const opz = (min: number, max: number): Intl.NumberFormatOptions => {
  const o: Record<string, unknown> = {
    minimumFractionDigits: min, maximumFractionDigits: max, useGrouping: 'always',
  };
  return o as Intl.NumberFormatOptions;
};
const num = (v: number | null | undefined, dec = 2): string =>
  v == null || !isFinite(v) ? tr('trade.na') : v.toLocaleString(localeDi(linguaCorrente()), opz(dec, dec));
const eur = (v: number | null | undefined, dec = 2): string =>
  v == null || !isFinite(v) ? tr('trade.na') : `${num(v, dec)} €`;
// quantita' frazionarie (cripto) e prezzi a 4 decimali (dividendi): mai
// arrotondare, si confermerebbe un numero diverso da quello inviato.
const exact = (v: number | null | undefined, dec = 8): string =>
  v == null || !isFinite(v) ? tr('trade.na') : v.toLocaleString(localeDi(linguaCorrente()), opz(0, dec));
const segno = (v: number | null | undefined, dec = 2): string =>
  v == null || !isFinite(v) ? tr('trade.na') : (v > 0 ? '+' : '') + num(v, dec);
const gg = (iso: string): string =>
  (iso || '').length >= 10 ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}` : tr('trade.na');
const pct = (f: number): string =>
  (isFinite(f) ? Math.min(100, Math.max(0, f * 100)).toFixed(3) : '0') + '%';
/** Il giorno di oggi secondo QUESTA macchina, in ISO.
 *  Il campo data del libretto parte compilato e non vuoto per due ragioni:
 *  quello che il PM legge è quello che viene scritto, e la riga gemella si
 *  può cercare davvero — a campo vuoto il giorno lo sceglierebbe il backend
 *  e il libretto non saprebbe contro quale confrontare la terna. App
 *  Electron e backend girano sulla stessa macchina, stesso orologio. */
const oggiISO = (): string => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

/** ⚠ Si congela il corpo E i numeri derivati: prima «In euro» e «Cassa dopo»
 *  del dialog seguivano lo stato VIVO mentre le altre righe erano congelate,
 *  cioe' due numeri sulla stessa schermata con garanzie diverse. */
type Pending = {
  body: TradeRequest; preview: TradePreview; discorde: Discordanza | null;
};
/** Il corpo di POST /cash/movement.
 *  ⚠ `data` viene SEMPRE valorizzata, col giorno che il browser calcola in
 *  `oggiISO` — è una scelta, non una svista, e il commento qui diceva il
 *  contrario finché un confutatore non l'ha beccato. La ragione: a campo
 *  vuoto il giorno lo sceglierebbe il backend, e il libretto non saprebbe
 *  contro quale data cercare la terna gemella — cioè perderebbe l'unica cosa
 *  per cui questo impianto è stato scelto. App Electron e backend girano
 *  sulla stessa macchina, stesso orologio; il rischio di mezzanotte è chiuso
 *  dal rinfresco su `focus`/`visibilitychange`. */
type CorpoMovimento = {
  tipo: 'DEPOSIT' | 'WITHDRAWAL'; importo_eur: number;
  data?: string; nota?: string; conferma?: boolean;
};

type Modo = 'trade' | 'cash' | 'opening';
/** Cassa e ricarica stanno nel controller delle operazioni: la testata le legge da qui. */
type Testata = { cassa: number | null; loading: boolean; letta: boolean };

export default function TradeEntryPage() {
  const t = useT();
  const [params] = useSearchParams();
  const modoIniziale: Modo = params.get('mode') === 'opening' ? 'opening' : params.get('mode') === 'cash' ? 'cash' : 'trade';
  const [mode, setMode] = useState<Modo>(modoIniziale);
  // Operazione e Cassa condividono un solo controller (stesse letture, stessa cassa):
  // resta montato finché una delle due schede è stata aperta, cosi' le bozze sopravvivono.
  const [visited, setVisited] = useState({ trade: modoIniziale !== 'opening', opening: modoIniziale === 'opening' });
  const [testata, setTestata] = useState<Testata>({ cassa: null, loading: false, letta: false });
  const ricarica = useRef<(() => void) | null>(null);
  const w = parole();
  const choose = (value: Modo) => {
    setVisited(v => ({ ...v, [value === 'opening' ? 'opening' : 'trade']: true }));
    setMode(value);
  };
  const schede: { id: Modo; dom: string; testo: string }[] = [
    { id: 'trade', dom: 'f7-mode-trade', testo: t('trade.operation') },
    { id: 'cash', dom: 'f7-mode-cash', testo: t('trade.cash') },
    { id: 'opening', dom: 'f7-mode-opening', testo: t('trade.opening') },
  ];
  return <ModernPage page="trades" presentationBoundary={false} render={() => <div className="te-page bbn-font">
    <TradeViewBoundary render={() => <div className="te-top">
      <h1>{w.title}</h1>
      <div className="bbn-seg is-large" role="group" aria-label={t('trade.mode')}>
        {schede.map(s => <button key={s.id} type="button" id={s.dom} aria-pressed={mode === s.id}
          className={mode === s.id ? 'is-on' : undefined} onClick={() => choose(s.id)}>{s.testo}</button>)}
      </div>
      <span className="bbn-grow" />
      {visited.trade && <span className="te-today">
        {w.cashAvailable} <b className="num">{!testata.letta ? '…' : testata.cassa != null ? eur(testata.cassa) : t('trade.na')}</b>
        <button type="button" className="bbn-icon-btn" aria-label={w.refresh} title={w.refresh} aria-busy={testata.loading}
          onClick={() => ricarica.current?.()}>
          <RefreshCw size={14} className={testata.loading ? 'animate-spin' : ''} aria-hidden="true" />
        </button>
      </span>}
    </div>} />
    <div hidden={mode === 'opening'} aria-hidden={mode === 'opening'}>
      {visited.trade && <TradeOperationEntry view={mode === 'cash' ? 'cash' : 'trade'}
        onTestata={setTestata} ricarica={ricarica} />}
    </div>
    <div hidden={mode !== 'opening'} aria-hidden={mode !== 'opening'}>
      {visited.opening && <PositionOpeningEntry />}
    </div>
  </div>} />;
}

/** A field retains its notation until cleared or explicitly filled by the book. */
function useNumericDraft() {
  const [raw, setRaw] = useState('');
  const [inputLanguage, setInputLanguage] = useState<Lingua | null>(null);
  const change = (value: string, suppliedLanguage?: Lingua) => {
    setInputLanguage(value.trim() === '' ? null : suppliedLanguage ?? (raw.trim() ? inputLanguage : null) ?? linguaCorrente());
    setRaw(value);
  };
  return [raw, change, inputLanguage ?? linguaCorrente()] as const;
}

function TradeOperationEntry({ view, onTestata, ricarica }: {
  view: 'trade' | 'cash';
  onTestata: (value: Testata) => void;
  ricarica: { current: (() => void) | null };
}) {
  const tr = useT(), language = useLingua();
  const [searchParams] = useSearchParams();
  const decisionFromRoute = searchParams.get('decision');
  const divergenceFromRoute = searchParams.get('divergence');
  const routeConsumed = useRef<string | null>(null);
  const divergenceRouteConsumed = useRef<string | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [decisionsErr, setDecisionsErr] = useState<string | null>(null);
  const [selectedDecision, setSelectedDecision] = useState(() => legameIniziale(decisionFromRoute));
  const [manualDivergenceDecision, setManualDivergenceDecision] = useState(divergenceFromRoute || '');
  const [manualDivergenceReason, setManualDivergenceReason] = useState('');
  const [manualDivergenceMessage, setManualDivergenceMessage] = useState<{
    text: string; error: boolean;
  } | null>(null);
  const [identityIsin, setIdentityIsin] = useState('');
  const [identitySource, setIdentitySource] = useState('');
  const [identityVerifiedAt, setIdentityVerifiedAt] = useState('');
  const [identityReason, setIdentityReason] = useState('');
  const [tradeDay, setTradeDay] = useState('');
  const [tradeTime, setTradeTime] = useState('');
  const [preparing, setPreparing] = useState(false);
  const previewInFlight = useRef(false);
  const [tradeResult, setTradeResult] = useState<TradeResult | null>(null);
  const [snap, setSnap] = useState<PortfolioSnapshot | null>(null);
  const [posErr, setPosErr] = useState<string | null>(null);
  const [trades, setTrades] = useState<TradeRiga[]>([]);
  const [tradesErr, setTradesErr] = useState<string | null>(null);
  const [rates, setRates] = useState<Record<string, number> | null>(null);
  const [ratesErr, setRatesErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [ticker, setTicker] = useState('');
  const [action, setAction] = useState<Verbo>('BUY');
  const [qty, setQty, qtyLanguage] = useNumericDraft();
  const [price, setPrice, priceLanguage] = useNumericDraft();
  const [valuta, setValuta] = useState('EUR');
  const [note, setNote] = useState('');
  const [rationale, setRationale] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [avviso, setAvviso] = useState<Notice | null>(null);
  const [esito, setEsito] = useState<Esito | null>(null);
  const [erroreScrittura, setErroreScrittura] = useState<string | null>(null);
  const [esitoIgnoto, setEsitoIgnoto] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);

  // ── IL LIBRETTO DELLA CASSA (F37): versare e prelevare «come un trade» ──
  // Impianto C scelto dal PM 20/08 sulla rosa IN SITU (mockup_f7_cassa):
  // il modulo sta SOPRA le righe gia' scritte, cosi' la terna duplicata si
  // vede mentre la si digita invece di scoprirla dal 422 a POST partita.
  const [movimenti, setMovimenti] = useState<MovimentoCassa[]>([]);
  const [movErr, setMovErr] = useState<string | null>(null);
  /** ⚠ TERZO STATO, come il book di questa stessa pagina. Senza, fra il mount
   *  e la prima risposta di `GET /cash/movements` il libretto affermava
   *  «0 movimenti a registro» e «Nessun movimento di cassa a registro» — su un
   *  registro che non aveva letto. È la lezione dei 0,55s pari pari: una frase
   *  di stato va vera in OGNI stato, e «non ce ne sono» non è «non lo so». */
  const [movLetto, setMovLetto] = useState(false);
  const [mvTipo, setMvTipo] = useState<'DEPOSIT' | 'WITHDRAWAL'>('DEPOSIT');
  const [mvImporto, setMvImporto, cashLanguage] = useNumericDraft();
  const [mvData, setMvData] = useState(oggiISO);
  /** il giorno di oggi come lo vede QUESTA macchina, rinfrescato: serve al
   *  dominio della data (niente `2062-08-20`) e non all'invio */
  const [mvOggi, setMvOggi] = useState(oggiISO);
  /** vero finché il campo data porta il valore messo da noi: se il PM lo
   *  scrive a mano, non glielo tocchiamo più */
  const mvDataAuto = useRef(true);
  const mvInVolo = useRef(false);
  const [mvNota, setMvNota] = useState('');
  const [mvInvio, setMvInvio] = useState(false);
  const [mvAvviso, setMvAvviso] = useState<Notice | null>(null);
  const [mvEsito, setMvEsito] = useState<EsitoMovimento | null>(null);
  const [mvIgnoto, setMvIgnoto] = useState<string | null>(null);
  /** Il rifiuto E il corpo che lo ha causato, CONGELATI insieme: «Confermo»
   *  rimanda quel corpo, non i campi vivi. Senza il congelamento il PM
   *  potrebbe correggere l'importo dopo il 422 e confermare un movimento
   *  diverso da quello di cui ha letto il motivo (stessa cura del Lotto D). */
  const [mvRifiuto, setMvRifiuto] = useState<
    { rifiuto: RifiutoCassa; corpo: CorpoMovimento } | null>(null);
  const mvAnnullaRef = useRef<HTMLButtonElement>(null);
  const mvImportoRef = useRef<HTMLInputElement>(null);
  const mvGemellaRef = useRef<HTMLLIElement>(null);

  const esitoRef = useRef<HTMLDivElement>(null);

  // ── i tre fetch, ognuno col SUO stato d'errore dichiarato ────────────────
  // ⚠ su errore si AZZERA anche il dato: prima si settava solo il messaggio e i
  // numeri vecchi restavano a schermo accanto a «non disponibile» — cioe' un
  // dato stantio reso come corrente, che e' la regola 14/07 al contrario.
  const carica = useCallback(async () => {
    setLoading(true);
    const [p, t, f, m, d] = await Promise.allSettled([
      Bellomberg.portfolio(), Bellomberg.trades(LIMITE_TRADES), Bellomberg.fx(),
      Bellomberg.cashMovements(LIMITE_MOVIMENTI),
      Bellomberg.decisions(undefined, 500),
    ]);
    const testo = (x: unknown) => {
      const e = x as { response?: { data?: { detail?: string } }; message?: string };
      return e?.response?.data?.detail || e?.message || String(x);
    };
    if (d.status === 'fulfilled' && Array.isArray(d.value?.decisions)) {
      setDecisions(d.value.decisions); setDecisionsErr(null);
      setTiStorage(d.value.trade_idea_storage ?? null);
    } else {
      setDecisions([]); setTiStorage(null);
      setDecisionsErr(d.status === 'rejected' ? testo(d.reason) : tr('trade.invalid_decisions'));
    }

    if (p.status === 'fulfilled') { setSnap(p.value); setPosErr(null); }
    else { setSnap(null); setPosErr(testo(p.reason)); }

    if (t.status === 'fulfilled') {
      const d = t.value as { trades?: TradeRiga[] };
      setTrades(Array.isArray(d?.trades) ? d.trades : []); setTradesErr(null);
    } else { setTrades([]); setTradesErr(testo(t.reason)); }

    if (f.status === 'fulfilled') {
      const d = f.value as { rates?: Record<string, number> };
      setRates(d?.rates || null); setRatesErr(null);
    } else { setRates(null); setRatesErr(testo(f.reason)); }

    // ⚠ stesso azzeramento degli altri tre: su lettura fallita il libretto NON
    // resta con le righe di prima sotto la scritta «non disponibile».
    if (m.status === 'fulfilled') {
      const d = m.value;
      // ⚠ un payload di forma diversa (rename backend, proxy che risponde
      // HTML) diventava «Nessun movimento a registro»: un buco reso come una
      // misura, cioè la regola 14/07 al contrario. Ora si dichiara.
      if (Array.isArray(d?.movements)) { setMovimenti(d.movements); setMovErr(null); }
      else {
        setMovimenti([]);
        setMovErr(tr('trade.invalid_movements'));
      }
    } else { setMovimenti([]); setMovErr(testo(m.reason)); }
    setMovLetto(true);
    setLoading(false);
  }, []);

  useEffect(() => { carica(); }, [carica]);

  useEffect(() => {
    if (!decisionFromRoute || routeConsumed.current === decisionFromRoute) return;
    setSelectedDecision(decisionFromRoute);
    const d = decisions.find(item => String(item.id) === decisionFromRoute);
    if (!d) return;
    routeConsumed.current = decisionFromRoute;
    setTicker(d.ticker);
    if (['BUY', 'ADD', 'SELL', 'TRIM'].includes(d.action)) setAction(d.action as Verbo);
  }, [decisionFromRoute, decisions]);

  useEffect(() => {
    if (!divergenceFromRoute || divergenceRouteConsumed.current === divergenceFromRoute) return;
    const d = decisions.find(item => String(item.id) === divergenceFromRoute);
    if (!d) return;
    divergenceRouteConsumed.current = divergenceFromRoute;
    setManualDivergenceDecision(divergenceFromRoute);
    setSelectedDecision('none');
    setTicker(d.proposal_ticker || d.ticker);
    if (['BUY', 'ADD', 'SELL', 'TRIM'].includes(d.action)) setAction(d.action as Verbo);
  }, [divergenceFromRoute, decisions]);

  // ── il verbo: cambiarlo NON deve lasciarsi dietro il prezzo di prima ─────
  // Passando da un prezzo in pence a DIVIDEND, il campo diventa «EUR per azione»:
  // conservare il valore precedente gonfierebbe l'incasso. Si azzera quando
  // cambia il significato del campo.
  const cambiaVerbo = (v: Verbo) => {
    const cambiaSignificato = (v === 'DIVIDEND') !== (action === 'DIVIDEND');
    setAction(v);
    if (v === 'DIVIDEND') setValuta('EUR');
    if (cambiaSignificato) { setPrice(''); setQty(''); }
    setAvviso(null);
  };

  // ── i numeri scritti a mano ──────────────────────────────────────────────
  const letturaQty = useMemo(() => leggiNumero(qty, qtyLanguage, language), [qty, qtyLanguage, language]);
  const letturaPrice = useMemo(() => leggiNumero(price, priceLanguage, language), [price, priceLanguage, language]);
  const qtyN = letturaQty && letturaQty.ok ? letturaQty.valore : NaN;
  const priceN = letturaPrice && letturaPrice.ok ? letturaPrice.valore : NaN;

  const positions: Position[] = useMemo(() => snap?.positions || [], [snap]);
  const bookAssente = posErr != null || snap == null;
  const tickerUp = ticker.toUpperCase().trim();
  const selectedDecisionRow = decisions.find(d => String(d.id) === selectedDecision);
  const manualDivergenceRow = decisions.find(d => String(d.id) === manualDivergenceDecision);
  const identityTargetDecision = selectedDecisionRow || manualDivergenceRow;
  const proposalTicker = selectedDecisionRow?.proposal_ticker || selectedDecisionRow?.ticker || '';
  const identityProposalTicker = identityTargetDecision?.proposal_ticker || identityTargetDecision?.ticker || '';
  const identityNeeded = !!identityTargetDecision && !!tickerUp
    && identityProposalTicker.toUpperCase() !== tickerUp;
  /** Decisione collegabile al trade che si sta scrivendo: stesso ticker inserito,
   *  oppure la decisione GIA' scelta con un ticker broker diverso (alias che il
   *  backend accetta solo con la verifica ISIN confermata insieme al trade). */
  const compatibileQui = (d: Decision) => decisioneCompatibile(d, tickerUp, action, false, tiStorage)
    || (String(d.id) === selectedDecision && decisioneCompatibile(d, tickerUp, action, true, tiStorage));
  useEffect(() => {
    setIdentityIsin(''); setIdentitySource(''); setIdentityVerifiedAt(''); setIdentityReason('');
  }, [selectedDecision, manualDivergenceDecision, tickerUp]);
  const pos = useMemo(
    () => positions.find(p => (p.ticker || '').toUpperCase() === tickerUp),
    [positions, tickerUp],
  );

  const ordineValido = !!tickerUp && isFinite(qtyN) && qtyN > 0
    && isFinite(priceN) && priceN > 0;

  const ordine: Ordine = useMemo(
    () => ({ ticker: tickerUp, action, quantita: qtyN, prezzo: priceN, valuta }),
    [tickerUp, action, qtyN, priceN, valuta],
  );
  const cambio = useMemo(() => cambioPer(valuta, rates, positions), [valuta, rates, positions]);
  const conv = useMemo(() => converti(ordine, cambio), [ordine, cambio]);
  const sim = useMemo(
    () => simula(ordine, conv, pos, snap, bookAssente), [ordine, conv, pos, snap, bookAssente]);
  const taglie = useMemo(
    () => taglieStoriche(trades, rates, positions), [trades, rates, positions]);
  const book = useMemo(() => ordinaPerControvalore(positions), [positions]);
  const discorde = useMemo(() => valutaDiscorde(ordine, pos), [ordine, pos]);
  const volte = controMediana(sim.valido ? conv.eur : null, taglie.mediana);

  const cassa = sim.cassaPrima;
  const delta = sim.valido && conv.eur != null ? conv.eur : null;
  const sfora = sim.copre === false;
  const entra = ENTRA(action);
  // il binario si disegna solo su una cassa POSITIVA: zero e negativo sono
  // misure, e hanno il loro messaggio — non «assente dal payload»
  const scala = useMemo(() => {
    if (cassa == null || cassa <= 0) return null;
    const b = sim.cassaDopo != null && sim.cassaDopo > 0 ? sim.cassaDopo : cassa;
    return Math.max(cassa, b);
  }, [cassa, sim.cassaDopo]);
  const pezzo = useMemo(() => {
    if (scala == null || delta == null || cassa == null || sim.cassaDopo == null) return null;
    if (entra) return { da: 0, largo: Math.min(1, delta / scala) };
    return { da: Math.min(1, cassa / scala), largo: Math.min(1, delta / scala) };
  }, [scala, delta, cassa, sim.cassaDopo, entra]);

  const ultimi = useMemo(
    () => taglie.taglie.slice()
      .sort((a, b) => (a.data < b.data ? 1 : a.data > b.data ? -1 : 0))
      .slice(0, ULTIMI_IN_LISTA),
    [taglie]);


  const selectPosition = (p: Position) => {
    setTicker(p.ticker);
    setValuta(action === 'DIVIDEND' ? 'EUR' : (p.valuta || 'EUR'));
    // su DIVIDEND il campo e' «EUR per azione»: il prezzo di mercato non c'entra
    setPrice(action === 'DIVIDEND' ? '' : prezzoDaBook(p), language);
    setQty('');
    setAvviso(null); setEsito(null); setErroreScrittura(null); setEsitoIgnoto(null);
  };

  // ── validazione: identica a prima (bugfix #164) ──────────────────────────
  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (previewInFlight.current || submitting) return;
    setAvviso(null); setEsito(null); setErroreScrittura(null); setEsitoIgnoto(null);
    setTradeResult(null);
    setManualDivergenceMessage(null);
    if (letturaQty && !letturaQty.ok) { setAvviso({ render: tr => { const n = leggiNumero(qty, qtyLanguage, linguaCorrente()); return tr('trade.quantity_error', {a: n && !n.ok ? n.motivo : ''}); } }); return; }
    if (letturaPrice && !letturaPrice.ok) { setAvviso({ render: tr => { const n = leggiNumero(price, priceLanguage, linguaCorrente()); return tr('trade.price_error', {a: n && !n.ok ? n.motivo : ''}); } }); return; }
    if (!tickerUp || !qty || !price) { setAvviso({ render: tr => tr('trade.required_fields') }); return; }
    if (!ordineValido) { setAvviso({ render: tr => tr('trade.positive_values') }); return; }
    // Un'operazione datata va validata sul registro cronologico dal backend:
    // la posizione corrente può essere già chiusa o avere una quantità diversa.
    if (action !== 'BUY' && !tradeDay.trim()) {
      if (bookAssente) {
        setAvviso({ render: tr => tr('trade.positions_not_loaded', {a: posErr ? ` (${posErr})` : ''})
          + tr('trade.refresh_and_retry', {a: action}) });
        return;
      }
      if (!pos) {
        setAvviso({ render: tr => tr('trade.ticker_missing', {a: tickerUp, b: action})
          + tr('trade.buy_new_position') });
        return;
      }
      if ((action === 'SELL' || action === 'TRIM') && qtyN > pos.quantita) {
        setAvviso({ render: tr => tr('trade.quantity_exceeds', {a: exact(qtyN)})
          + `(${exact(pos.quantita)} ${tickerUp})` });
        return;
      }
    }
    previewInFlight.current = true;
    setPreparing(true);
    try {
      if (manualDivergenceDecision) {
        if (selectedDecision !== 'none' || !manualDivergenceRow
            || !statoDivergenza(manualDivergenceRow)) {
          throw new Error(tr('trade.manual_divergence_requires_blocked'));
        }
        if (!manualDivergenceReason.trim()) {
          throw new Error(tr('trade.manual_divergence_reason_required'));
        }
      }
      // ⚠ L'anteprima NON scrive nulla: prima qui partiva POST
      // /instrument-identities/verify, un INSERT in una tabella append-only
      // PRIMA del dialog — un «Annulla» lasciava la verifica per sempre e
      // log_trade la riusava. Ora verifica e divergenza viaggiano nel corpo del
      // trade: il backend le valida in anteprima e le scrive solo alla conferma,
      // nella stessa transazione del trade.
      const identita = identityNeeded && identityTargetDecision
        ? corpoIdentita(identityIsin, identitySource, identityVerifiedAt, identityReason) : undefined;
      const body: TradeRequest = {
        ticker: tickerUp, action, quantita: qtyN, prezzo: priceN, valuta,
        note: note || undefined, pm_rationale: rationale || undefined,
        data: dataTrade(tradeDay, tradeTime, oggiISO()),
        ...legameTrade(selectedDecision, decisions, tickerUp, action, !!identita, tiStorage),
        ...(identita ? { instrument_identity: identita } : {}),
        ...(manualDivergenceDecision ? { manual_divergence: {
          decision_id: Number(manualDivergenceDecision), reason: manualDivergenceReason.trim() } } : {}),
      };
      const preview = await Bellomberg.previewTrade(body);
      setPending({ ...congelaAnteprima(body, preview), discorde });
    } catch (err) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string };
      setAvviso(err instanceof FrontendTradeError ? { render: err.renderMessage } : e?.response?.data?.detail || e?.message || String(err));
    } finally {
      previewInFlight.current = false;
      setPreparing(false);
    }
  };

  const commit = async (p: Pending) => {
    setSubmitting(true);
    try {
      const r = await Bellomberg.logTrade(p.body);
      setEsito(esitoScrittura(r));
      setTradeResult(r);
      setErroreScrittura(null); setEsitoIgnoto(null);
      // La divergenza e' nel corpo CONGELATO all'anteprima (p.body), non nello
      // stato vivo del modulo, ed e' scritta dal backend nella transazione del
      // trade: qui si legge solo la ricevuta, nessuna seconda POST.
      if (p.body.manual_divergence) {
        const ev = r.manual_divergence;
        setManualDivergenceMessage(ev && ev.decision_id === p.body.manual_divergence.decision_id
          && Number.isSafeInteger(Number(ev.event_id)) && Number(ev.event_id) > 0
          ? { text: tr('trade.manual_divergence_recorded'), error: false }
          : { text: tr('trade.manual_divergence_response_missing'), error: true });
        setManualDivergenceDecision(''); setManualDivergenceReason('');
      }
      await carica();
      if (p.body.action === 'BUY' || p.body.action === 'SELL') setTicker('');
      setQty(''); setNote(''); setRationale('');
    } catch (err) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string };
      const testo = e?.response?.data?.detail || e?.message || String(err);
      setEsito(null);
      // ⚠ solo un 4xx dichiara il RIFIUTO. Su 5xx,
      // timeout o rete caduta la scrittura puo' essere gia' avvenuta, e dire
      // «non è stato scritto» spinge al doppio invio su una pagina senza annullo.
      if (scritturaRifiutata(err)) { setErroreScrittura(testo); setEsitoIgnoto(null); }
      else { setErroreScrittura(null); setEsitoIgnoto(testo); }
    } finally {
      setSubmitting(false);
    }
  };

  // il fuoco va sull'esito: prima cadeva su <body> perche' il bottone che
  // l'aveva diventava `disabled` nello stesso commit in cui il dialog smontava
  useEffect(() => {
    if (esito || erroreScrittura || esitoIgnoto) esitoRef.current?.focus();
  }, [esito, erroreScrittura, esitoIgnoto]);

  // ── il riepilogo di conferma: TUTTO dal corpo congelato ──────────────────
  const pendingRows = (p: Pending): ConfirmRow[] => {
    const b = p.body;
    const div = b.action === 'DIVIDEND';
    const rows: ConfirmRow[] = [
      { k: tr('trade.action'), v: b.action, tone: (b.action === 'SELL' || b.action === 'TRIM') ? 'crimson' : 'cyan' },
      { k: 'Ticker', v: b.ticker },
      { k: div ? tr('trade.shares') : tr('trade.quantity'), v: exact(b.quantita) },
      { k: div ? tr('trade.dividend_share') : tr('trade.price'), v: `${exact(b.prezzo)} ${b.valuta}` },
      { k: div ? tr('trade.proceeds') : tr('trade.value'), v: `${num(b.quantita * b.prezzo)} ${b.valuta}`, tone: 'amber' },
    ];
    const v = p.preview;
    rows.push(
      { k: tr('trade.trade_date'), v: v.data.replace('T', ' ') + (v.ora_convenzionale ? tr('trade.conventional_time') : '') },
      { k: tr('trade.cash_change'), v: eur(v.cash_delta_eur), tone: 'amber' },
      { k: tr('trade.cash_after'), v: eur(v.cash_disponibile_eur), tone: v.cash_disponibile_eur < 0 ? 'crimson' : undefined },
      { k: tr('trade.applied_fx'), v: `1 ${b.valuta} = ${exact(v.fx.tasso)} EUR · ${v.fx.fonte === 'identity' ? tr('trade.already_euros') : v.fx.fonte === 'storico' ? tr('trade.historical') : tr('trade.current_not_historical')}${v.fx.data ? ' · ' + v.fx.data : ''}` },
      { k: tr('trade.decision'), v: v.link_origin === 'explicit' ? `#${v.decisione?.id} · ${v.decisione?.status}`
        : v.link_origin === 'none' ? tr('trade.manual_no_decision') : tr('trade.unknown_link_full') },
    );
    if (v.fx.nota) rows.push({ k: tr('trade.fx_note'), v: v.fx.nota, tone: 'amber' });
    if (v.cassa_nota) rows.push({ k: tr('trade.cash_note'), v: v.cassa_nota });
    if (v.guardia_note) rows.push({ k: tr('trade.price_check'), v: v.guardia_note, tone: 'amber' });
    if (v.decisione?.nota) rows.push({ k: tr('trade.decision_status'), v: v.decisione.nota });
    // alias del ticker e divergenza manuale: si scrivono con questo trade, quindi
    // il PM li deve vedere qui, dall'anteprima validata e non dai campi vivi
    rows.push(...righeLegame(v));
    if (v.ricalcolo) {
      const r = v.ricalcolo;
      rows.push(
        { k: tr('trade.current_qty'), v: `${exact(r.prima.quantita)} → ${exact(r.dopo.quantita)}` },
        { k: tr('trade.position_opened'), v: `${r.prima.data_apertura || tr('trade.na')} → ${r.dopo.data_apertura || tr('trade.na')}` },
        { k: tr('trade.current_cost'), v: `${exact(r.prima.prezzo_medio)} → ${exact(r.dopo.prezzo_medio)} ${r.valuta}` },
        { k: tr('trade.realized'), v: `${num(r.prima.realized)} → ${num(r.dopo.realized)} ${r.valuta}` },
        { k: tr('trade.successive_trades'), v: r.trade_successivi.length ? r.trade_successivi.map(id => `#${id}`).join(', ') : tr('trade.nothing_to_recalculate') },
      );
      (r.note || []).forEach(nota => rows.push({ k: tr('trade.recalculation'), v: nota, tone: 'amber' }));
    }
    // ⚠ i due avvisi che contano entravano nel form e NON nel dialog: l'ultima
    // schermata prima di una scrittura irreversibile era muta sull'errore che
    // costa di piu'.
    if (p.discorde) {
      rows.push({
        k: tr('trade.currency'),
        v: tr('trade.currency_mismatch', {a: b.ticker, b: p.discorde.valutaBook})
          + `${p.discorde.valutaScelta}` + (p.discorde.fattoreCento ? tr('trade.hundred_difference') : ''),
        tone: 'crimson',
      });
    }
    if (b.pm_rationale) rows.push({ k: tr('trade.reason'), v: b.pm_rationale });
    if (b.note) rows.push({ k: tr('trade.note'), v: b.note });
    return rows;
  };

  // ── frecce sui verbi: roving tabindex, e l'indice parte da chi ha il fuoco ─
  // ══ IL LIBRETTO DELLA CASSA ═══════════════════════════════════════════
  const letturaImporto = useMemo(() => leggiNumero(mvImporto, cashLanguage, language), [mvImporto, cashLanguage, language]);
  const letturaData = useMemo(() => leggiDataValuta(mvData, mvOggi), [mvData, mvOggi, language]);
  const importoN = letturaImporto && letturaImporto.ok ? letturaImporto.valore : NaN;
  const dataISO = letturaData && letturaData.ok ? letturaData.iso : null;

  const movimentoMax = useMemo(
    () => movimenti.reduce((m, x) => Math.max(m, Math.abs(x.amount_eur || 0)), 0),
    [movimenti]);
  /** Il registro reso è una FINESTRA quando tocca il tetto. Il backend serve
   *  `count = len(rows)` (bellomberg_api.py:1835), cioè la finestra stessa: il
   *  totale vero questa pagina NON ce l'ha, e non può affermarlo. */
  const finestraPiena = movimenti.length >= LIMITE_MOVIMENTI;

  /** Il cumulato dei flussi, riga per riga.
   *  ⚠ DUE cose che NON è, entrambe rese a schermo invece che taciute:
   *  · non è il saldo cassa (anche i trade la muovono, e qui non ci sono):
   *    sulla rosa la colonna «saldo» dava una cifra incompatibile col
   *    versamento iniziale: un numero falso con la faccia di una misura;
   *  · non è il cumulato di TUTTO il registro quando le righe rese sono il
   *    tetto — l'accumulo parte da zero sulla più vecchia VISIBILE, quindi
   *    oltre la finestra ogni numero è sfalsato dello stesso offset. È lo
   *    stesso difetto della colonna «saldo», una scala più in là. */
  const flussi = useMemo(() => {
    // le righe arrivano più recenti PRIMA (ORDER BY date DESC, id DESC in
    // memory_db.py:987): il cumulato si costruisce partendo dalla più vecchia
    const out = new Map<number, number | null>();
    let acc = 0;
    for (let i = movimenti.length - 1; i >= 0; i--) {
      const m = movimenti[i];
      const v = m.amount_eur;
      // importo assente: non si inventa uno zero — si dichiara il buco
      if (typeof v !== 'number' || !isFinite(v)) { out.set(m.id, null); continue; }
      acc += (m.type === 'DEPOSIT' ? 1 : -1) * v;
      out.set(m.id, acc);
    }
    return out;
  }, [movimenti]);

  /** La riga già a registro con la STESSA terna (data, tipo, importo) — la
   *  stessa che il backend cerca in memory_db.py:965. Non SOSTITUISCE la
   *  guardia: la ANTICIPA, che è l'intero motivo per cui il PM ha scelto il
   *  libretto. Il confronto è sui centesimi perché il backend arrotonda a 2
   *  decimali prima dell'INSERT (memory_db.py:957).
   *  ⚠ Due limiti, dichiarati perché l'anticipo È la promessa dell'impianto:
   *  vede solo la finestra da LIMITE_MOVIMENTI (oltre quella tace, mentre il
   *  backend cerca su tutta la tabella e rifiuta lo stesso); e arrotonda
   *  half-up mentre `round()` di Python è half-even, quindi su un mezzo
   *  centesimo esatto i due possono divergere. In entrambi i casi la rete
   *  vera resta il 422, non questo. */
  const gemella = useMemo(() => {
    if (!isFinite(importoN) || !dataISO) return null;
    const c = Math.round(importoN * 100);
    return movimenti.find(m => m.date === dataISO && m.type === mvTipo
      && typeof m.amount_eur === 'number'
      && Math.round(m.amount_eur * 100) === c) || null;
  }, [movimenti, importoN, dataISO, mvTipo]);

  /** Il prelievo oltre la cassa il backend lo RIFIUTA (memory_db.py:941), non
   *  lo avvisa soltanto: qui si dice quello che succederà, non «attenzione». */
  const prelievoScoperto = mvTipo === 'WITHDRAWAL' && isFinite(importoN)
    && cassa != null && importoN > cassa;

  const cassaDopoMovimento = cassa != null && isFinite(importoN)
    ? cassa + (mvTipo === 'DEPOSIT' ? importoN : -importoN) : null;
  /** ⚠ «la cassa passa da X a Y» è una PREVISIONE, e va taciuta dove si sa già
   *  che non si avvererà: con una gemella a registro, un rifiuto a schermo o
   *  un prelievo scoperto il movimento verrà respinto — e la frase conviveva
   *  con «il backend la rifiuterà» a sette righe di distanza. */
  const previsioneCredibile = cassaDopoMovimento != null && !gemella && !mvRifiuto
    && !prelievoScoperto;

  const scriviMovimento = async (corpo: CorpoMovimento) => {
    // cintura oltre il `disabled`: nel ramo `conferma` la guardia duplicato del
    // backend è spenta, quindi un secondo invio scriverebbe per davvero. Il ref
    // non dipende dal ciclo di render come farebbe lo stato — il `disabled` è
    // una garanzia del runtime, questa è nostra.
    if (mvInVolo.current) return;
    mvInVolo.current = true;
    setMvInvio(true);
    let riuscito = false;
    try {
      const r = await Bellomberg.logCashMovement(corpo);
      setMvEsito(esitoMovimento(r));
      setMvRifiuto(null); setMvIgnoto(null); setMvAvviso(null);
      riuscito = true;
    } catch (err) {
      setMvEsito(null);
      const rif = leggiRifiuto(err);
      if (rif) {
        // il server ha RISPOSTO. Se sia stato scritto o no lo dice `nonScritto`,
        // NON il fatto che una risposta esista. Il corpo si congela col motivo:
        // «Confermo» rimanda QUELLO, non i campi vivi.
        setMvRifiuto({ rifiuto: rif, corpo }); setMvIgnoto(null);
      } else {
        // nessuna risposta: NON si sa se il movimento sia stato scritto.
        // Mai «non è stato scritto» su un timeout: spingerebbe al doppio invio.
        const e = err as { message?: string };
        setMvRifiuto(null);
        setMvIgnoto(e?.message || String(err));
      }
    } finally {
      mvInVolo.current = false;
      setMvInvio(false);
    }
    // ⚠ FUORI dal try: se `carica()` lanciasse lì dentro, il catch renderebbe
    // una scrittura RIUSCITA e con id noto come «esito ignoto». Si rilegge
    // sempre — anche dopo un esito ignoto, dove il registro è l'unico modo di
    // sapere com'è andata.
    if (riuscito) {
      setMvImporto(''); setMvNota('');
      const g = oggiISO();
      setMvData(g); setMvOggi(g); mvDataAuto.current = true;
    }
    await carica();
  };

  const inviaMovimento = (e: React.FormEvent) => {
    e.preventDefault();
    setMvAvviso(null); setMvEsito(null); setMvRifiuto(null); setMvIgnoto(null);
    if (!letturaImporto) { setMvAvviso({ render: tr => tr('trade.write_movement_amount') }); return; }
    if (!letturaImporto.ok) { setMvAvviso({ render: tr => { const n = leggiNumero(mvImporto, cashLanguage, linguaCorrente()); return tr('trade.amount_error', {a: n && !n.ok ? n.motivo : ''}); } }); return; }
    if (letturaData && !letturaData.ok) {
      setMvAvviso({ render: tr => { const date = leggiDataValuta(mvData, mvOggi); return tr('trade.value_date_error', {a: date && !date.ok ? date.motivo : ''}); } }); return;
    }
    const corpo: CorpoMovimento = { tipo: mvTipo, importo_eur: letturaImporto.valore };
    if (dataISO) corpo.data = dataISO;
    const n = mvNota.trim();
    if (n) corpo.nota = n;
    void scriviMovimento(corpo);
  };

  /** Rimanda il corpo congelato con `conferma: true`.
   *  ⚠ QUI il doppio clic torna possibile: la guardia duplicato del backend è
   *  la rete contro il doppio invio, e `conferma` la spegne. Il disable
   *  sull'invio non è igiene, è la sola difesa che resta (più `mvInVolo`). */
  const confermaMovimento = () => {
    if (!mvRifiuto || !mvRifiuto.rifiuto.rimediabile || mvInvio) return;
    void scriviMovimento({ ...mvRifiuto.corpo, conferma: true });
  };

  /** ⚠ IL REPERTO PIÙ GRAVE DELLA REVIEW, e nasceva da una cura.
   *  Il corpo si congela col motivo — giusto — ma il submit era spento finché
   *  il rifiuto stava a schermo. Correggendo l'importo dopo un «GUARDIA
   *  IMPORTO: 150000.00 EUR» l'unico bottone acceso restava «Confermo», che
   *  manda il corpo VECCHIO; e `conferma:true` spegne pure la rete
   *  anti-duplicato. Un clic solo, sul percorso più naturale dopo un
   *  fat-finger, e a registro entrava l'errore che la guardia esisteva per
   *  fermare. Cura: qualunque modifica ai campi butta il rifiuto, come faceva
   *  già il tipo VERSA/PRELEVA — cambi qualcosa, il verdetto te lo ridà il
   *  backend. */
  const scordaRifiuto = () => { if (mvRifiuto) setMvRifiuto(null); };

  const mvKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const altro = mvTipo === 'DEPOSIT' ? 'WITHDRAWAL' : 'DEPOSIT';
    setMvTipo(altro);
    setMvRifiuto(null); setMvEsito(null); setMvAvviso(null);
    const btns = Array.from(e.currentTarget.querySelectorAll('button'));
    (btns[altro === 'DEPOSIT' ? 0 : 1] as HTMLButtonElement | undefined)?.focus();
  };

  /** ⚠ `useState(oggiISO)` è un inizializzatore PIGRO: gira una volta sola al
   *  mount. L'app Electron resta aperta, e dopo mezzanotte il campo portava
   *  IERI — e lo spediva, perché valorizzato: il flusso sarebbe finito nel
   *  giorno sbagliato del TWR, cioè esattamente ciò che l'aiuto sotto il campo
   *  promette di decidere. Qui il giorno si rinfresca quando la finestra torna
   *  in primo piano, ma SOLO se il PM non ha toccato il campo: una data
   *  scritta a mano non gliela cambia nessuno. */
  useEffect(() => {
    const rinfresca = () => {
      const g = oggiISO();
      setMvOggi(g);
      if (mvDataAuto.current) setMvData(g);
    };
    window.addEventListener('focus', rinfresca);
    document.addEventListener('visibilitychange', rinfresca);
    return () => {
      window.removeEventListener('focus', rinfresca);
      document.removeEventListener('visibilitychange', rinfresca);
    };
  }, []);

  // Il fuoco dopo un rifiuto scavalcabile va su ANNULLA, non su «Confermo»:
  // stessa scelta del ConfirmDialog del Lotto D — l'INVIO accidentale annulla,
  // non scavalca due guardie.
  useEffect(() => {
    if (mvRifiuto?.rifiuto.rimediabile) mvAnnullaRef.current?.focus();
  }, [mvRifiuto]);

  // La riga gemella si porta in vista: «è evidenziata qui sotto» era falsa
  // dalla sesta riga in giù, dove `.lbm` scorre — e la visibilità di quella
  // riga è l'intero motivo per cui l'impianto C è stato scelto.
  useEffect(() => {
    if (gemella) mvGemellaRef.current?.scrollIntoView({ block: 'nearest' });
  }, [gemella]);

  const verbiKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const btns = Array.from(e.currentTarget.querySelectorAll('button'));
    const cur = btns.indexOf(document.activeElement as HTMLButtonElement);
    const i = cur >= 0 ? cur : ACTIONS.findIndex(a => a.v === action);
    const n = e.key === 'ArrowRight'
      ? (i + 1) % ACTIONS.length : (i - 1 + ACTIONS.length) % ACTIONS.length;
    cambiaVerbo(ACTIONS[n].v);
    (btns[n] as HTMLButtonElement | undefined)?.focus();
  };


  // ══ TESTATA E RICERCA PER NOME ════════════════════════════════════════
  // ⚠ Stato aggiunto IN CODA agli hook: il test i18n (trade-draft) indicizza
  // useState/useEffect per posizione, e quelli di prima non devono spostarsi.
  useEffect(() => {
    ricarica.current = () => { void carica(); };
    return () => { ricarica.current = null; };
  }, [carica, ricarica]);
  useEffect(() => { onTestata({ cassa, loading, letta: movLetto }); }, [cassa, loading, movLetto, onTestata]);

  const [cercaAperta, setCercaAperta] = useState(false);
  const [cercaAttiva, setCercaAttiva] = useState(0);
  const [mercato, setMercato] = useState<{ q: string; stato: 'attesa' | 'ok' | 'errore'; righe: MktSearchHit[] } | null>(null);
  const [valutaQuotata, setValutaQuotata] = useState<{ ticker: string; valuta: string; gestita: boolean } | null>(null);
  const [dettagli, setDettagli] = useState(false);
  // 10/10 (Opus 5.5): `trade_idea_storage` di GET /decisions (in coda agli hook: v. avviso sopra)
  const [tiStorage, setTiStorage] = useState<DecisionsResponse['trade_idea_storage']>(null);
  const tickerScelto = useRef('');
  const w = parole();

  const domanda = ticker.trim();
  const tenute = useMemo(() => {
    if (!domanda) return [];
    const q = domanda.toLowerCase();
    return positions.filter(p => (p.ticker || '').toLowerCase().includes(q)
      || (p.nome || '').toLowerCase().includes(q)).slice(0, 5);
  }, [positions, domanda]);
  // La ricerca di mercato parte dopo 300 ms di quiete e solo con il menu aperto.
  // Un errore (rete, endpoint assente) non blocca nulla: si scrive il ticker a mano.
  useEffect(() => {
    if (!cercaAperta || domanda.length < 2) { setMercato(null); return; }
    let vivo = true;
    setMercato(m => (m && m.q === domanda ? m : { q: domanda, stato: 'attesa', righe: [] }));
    const timer = setTimeout(() => {
      Promise.resolve().then(() => Bellomberg.mktSearch(domanda))
        .then(r => { if (vivo) setMercato({ q: domanda, stato: 'ok', righe: Array.isArray(r?.results) ? r.results : [] }); })
        .catch(() => { if (vivo) setMercato({ q: domanda, stato: 'errore', righe: [] }); });
    }, 300);
    return () => { vivo = false; clearTimeout(timer); };
  }, [cercaAperta, domanda]);
  const inBook = useMemo(() => new Set(positions.map(p => (p.ticker || '').toUpperCase())), [positions]);
  const altriMercati = useMemo(() => (mercato?.righe || [])
    .filter(h => h.symbol && TIPI_CERCABILI.includes((h.type || '').toUpperCase()) && !inBook.has(h.symbol.toUpperCase()))
    .slice(0, 8), [mercato, inBook]);
  type Opzione = { tipo: 'book'; pos: Position } | { tipo: 'mercato'; hit: MktSearchHit };
  const opzioni: Opzione[] = useMemo(() => [
    ...tenute.map(pos => ({ tipo: 'book' as const, pos })),
    ...altriMercati.map(hit => ({ tipo: 'mercato' as const, hit })),
  ], [tenute, altriMercati]);
  const menuVisibile = cercaAperta && domanda.length > 0
    && (opzioni.length > 0 || (domanda.length >= 2 && mercato != null));
  useEffect(() => { if (cercaAttiva > opzioni.length - 1) setCercaAttiva(0); }, [opzioni.length, cercaAttiva]);

  // I Dettagli si aprono da soli quando contengono qualcosa che pesa sulla scrittura.
  const dettagliForzati = !['none', 'unknown'].includes(selectedDecision) || !!manualDivergenceDecision || !!tradeDay || !!tradeTime;
  useEffect(() => { if (dettagliForzati) setDettagli(true); }, [dettagliForzati]);

  const scegliMercato = (hit: MktSearchHit) => {
    const simbolo = hit.symbol.toUpperCase();
    tickerScelto.current = simbolo;
    setTicker(simbolo); setCercaAperta(false); setValutaQuotata(null);
    setAvviso(null); setEsito(null); setErroreScrittura(null); setEsitoIgnoto(null);
    // la valuta viene dalla quotazione; il prezzo no: dev'essere quello eseguito
    Promise.resolve().then(() => Bellomberg.mktQuote(simbolo)).then(q => {
      if (tickerScelto.current !== simbolo) return;
      const grezza = (q?.currency || '').trim();
      if (!grezza) return;
      const v = grezza === 'GBp' ? 'GBX' : grezza.toUpperCase();
      const gestita = CURRENCIES.includes(v);
      setValutaQuotata({ ticker: simbolo, valuta: v, gestita });
      if (gestita) setValuta(cur => (action === 'DIVIDEND' ? cur : v));
    }).catch(() => { /* senza quotazione la valuta resta da scegliere */ });
  };
  const scegli = (o: Opzione) => {
    if (o.tipo === 'book') { tickerScelto.current = o.pos.ticker.toUpperCase(); setValutaQuotata(null); selectPosition(o.pos); setCercaAperta(false); }
    else scegliMercato(o.hit);
  };
  const tickerKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') { if (cercaAperta) { e.preventDefault(); setCercaAperta(false); } return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!opzioni.length) return;
      e.preventDefault();
      if (!cercaAperta) { setCercaAperta(true); setCercaAttiva(0); return; }
      const n = opzioni.length;
      setCercaAttiva(i => (e.key === 'ArrowDown' ? (i + 1) % n : (i - 1 + n) % n));
      return;
    }
    if (e.key === 'Enter' && menuVisibile && opzioni[cercaAttiva]) { e.preventDefault(); scegli(opzioni[cercaAttiva]); }
  };

  const verboTesto = w.verbs[action];
  const nomeTk = pos ? nomeTitolo(pos) : null;
  const valutaNota = valutaQuotata && valutaQuotata.ticker === tickerUp ? valutaQuotata : null;
  const vendita = !entra && action !== 'DIVIDEND';
  const scalaOrdini = Math.max(delta ?? 0, ...ultimi.map(t => t.eur ?? 0), 1);
  // ⚠ un importo mancante NON vale 0 e un tipo sconosciuto NON e' un prelievo:
  // il totale diventa n.d. col motivo; a finestra piena e' un minimo («≥»).
  const totaleMovimenti = useMemo(() => totaliMovimenti(movimenti, LIMITE_MOVIMENTI), [movimenti, language]);
  const totaleReso = (v: number | null, segnoTesto: string, motivo: string | null) =>
    v == null ? <span title={motivo || undefined}>{tr('trade.na')}</span>
      : (totaleMovimenti.parziale ? '≥ ' : '') + (v ? segnoTesto : '') + eur(v, 0);
  const flussoUltimo = movimenti.length ? flussi.get(movimenti[0].id) ?? null : null;
  const flussoDopo = flussoUltimo != null && isFinite(importoN) && !finestraPiena
    ? flussoUltimo + (mvTipo === 'DEPOSIT' ? importoN : -importoN) : null;
  const dettagliNota = [
    tradeDay ? w.detailsDated(tradeDay + (tradeTime ? ' ' + tradeTime : '')) : w.detailsNow,
    manualDivergenceDecision ? w.detailsDivergence
      : selectedDecision === 'none' ? w.detailsManual
        : selectedDecision === 'unknown' ? w.detailsUnknown : w.detailsLinked(selectedDecision),
  ].join(' · ');

  return <TradeViewBoundary render={() => (
    <div className="f7c te-views" aria-busy={loading}>

      {/* ══════════════ OPERAZIONE ══════════════ */}
      <div className="te-grid te-op" hidden={view !== 'trade'}>
        <div className="te-col">
          <Card titolo={w.newOrder} className="te-ticket" azioni={<span className="bbn-card-note">{w.writesOfficial}</span>}>
            <form onSubmit={submit} className="te-body">
              <div className="bbn-seg is-full" role="radiogroup" aria-label={tr('trade.trade_type')} onKeyDown={verbiKeyDown}>
                {ACTIONS.map(a => (
                  <button key={a.v} type="button" role="radio" aria-checked={a.v === action}
                          className={a.v === action ? 'is-on' : undefined}
                          aria-label={a.label} title={a.label} tabIndex={a.v === action ? 0 : -1}
                          onClick={() => cambiaVerbo(a.v)}>{w.verbs[a.v]}</button>
                ))}
              </div>
              <p className="te-gloss">{w.gloss[action]}</p>

              <div className="te-field te-combo">
                <label htmlFor="f7-tk">{w.instrument}</label>
                <div className="te-input">
                  {pos && <IconaTitolo ticker={pos.ticker} nome={pos.nome} dimensione="xs" />}
                  <input id="f7-tk" value={ticker} placeholder={w.searchPlaceholder} autoComplete="off"
                         role="combobox" aria-autocomplete="list" aria-expanded={menuVisibile} aria-controls="f7-tk-lista"
                         aria-activedescendant={menuVisibile && opzioni[cercaAttiva] ? `f7-tk-o${cercaAttiva}` : undefined}
                         onChange={e => { setTicker(e.target.value.toUpperCase()); setCercaAperta(true); setCercaAttiva(0); }}
                         onFocus={() => { if (domanda) setCercaAperta(true); }}
                         onBlur={() => setCercaAperta(false)}
                         onKeyDown={tickerKeyDown} />
                  {!bookAssente && tickerUp && <span className={'bbn-pill ' + (pos ? 'is-acc' : 'is-piatto')}>{pos ? w.inPortfolio : w.newName}</span>}
                </div>
                <div className={'te-help' + (bookAssente && tickerUp ? ' is-bad' : '')}>
                  {bookAssente
                    ? (tickerUp ? tr('trade.book_not_loaded') : ' ')
                    : pos
                      ? <>{nomeTk && nomeTk !== pos.ticker ? nomeTk + ' · ' : ''}{tr('trade.holding_cost', { a: exact(pos.quantita), b: num(pos.prezzo_medio), c: pos.valuta })}</>
                      : valutaNota ? (valutaNota.gestita ? w.currencyFromQuote(valutaNota.valuta) : w.currencyUnsupported(valutaNota.valuta))
                        : tickerUp ? tr('trade.not_active') : ' '}
                </div>
                {menuVisibile && (
                  <div className="te-suggest" id="f7-tk-lista" role="listbox" aria-label={w.suggestLabel}
                       onMouseDown={e => e.preventDefault()}>
                    {tenute.length > 0 && <h4>{w.suggestHeld}</h4>}
                    {opzioni.map((o, i) => {
                      const attiva = i === cercaAttiva;
                      const primaMercato = o.tipo === 'mercato' && (i === 0 || opzioni[i - 1].tipo === 'book');
                      return <div key={o.tipo === 'book' ? 'b' + o.pos.ticker : 'm' + o.hit.symbol}>
                        {primaMercato && <h4>{w.suggestMarket}</h4>}
                        <div id={`f7-tk-o${i}`} role="option" aria-selected={attiva}
                             className={'te-sg' + (attiva ? ' is-active' : '')}
                             onMouseEnter={() => setCercaAttiva(i)} onClick={() => scegli(o)}>
                          {o.tipo === 'book' ? <>
                            <IconaTitolo ticker={o.pos.ticker} nome={o.pos.nome} dimensione="sm" />
                            <span className="te-sg-name"><b>{o.pos.ticker}</b><span>{nomeTitolo(o.pos)} · {w.heldShares(exact(o.pos.quantita))}</span></span>
                            <span className="te-sg-side num">{o.pos.valuta}{o.pos.prezzo_live != null ? ' · ' + num(o.pos.prezzo_live) : ''}</span>
                          </> : <>
                            <IconaTitolo ticker={o.hit.symbol} nome={o.hit.name} dimensione="sm" />
                            <span className="te-sg-name"><b>{o.hit.symbol}</b><span>{[o.hit.name, o.hit.exchange].filter(Boolean).join(' · ')}</span></span>
                            <span className="te-sg-side">{(o.hit.type || '').toLowerCase()}</span>
                          </>}
                        </div>
                      </div>;
                    })}
                    {domanda.length >= 2 && mercato?.stato === 'attesa' && <p className="te-sg-state">{w.suggestSearching}</p>}
                    {domanda.length >= 2 && mercato?.stato === 'ok' && altriMercati.length === 0 && <p className="te-sg-state">{w.suggestNone}</p>}
                    {domanda.length >= 2 && mercato?.stato === 'errore' && <p className="te-sg-state">{w.suggestError}</p>}
                    <p className="te-sg-foot">{w.suggestHint}</p>
                  </div>
                )}
              </div>

              {/* ⚠ type="text" e non "number": su type=number il browser CANCELLA
                  il separatore decimale non della sua locale — `158,50` diventa
                  `15850`, senza badInput. Misurato sull'app viva. */}
              <div className="te-row3">
                <div className="te-field">
                  <label htmlFor="f7-qt">{action === 'DIVIDEND' ? tr('trade.shares') : tr('trade.quantity')}</label>
                  <div className={'te-input' + (letturaQty && !letturaQty.ok ? ' is-bad' : '')}>
                    <input id="f7-qt" className="num" type="text" inputMode="decimal"
                           title={tr('numeri.grafia_richiesta', { esempio: qtyLanguage === 'it' ? '1.234,56' : '1,234.56' })}
                           autoComplete="off" value={qty}
                           placeholder={action === 'DIVIDEND' ? tr('trade.example_shares') : tr('trade.example_qty')}
                           aria-invalid={!!(letturaQty && !letturaQty.ok)}
                           onChange={e => setQty(e.target.value)} />
                    <span className="te-suf">{w.shares}</span>
                  </div>
                  {letturaQty && !letturaQty.ok && <div className="te-help is-bad">{letturaQty.motivo}</div>}
                </div>
                <div className="te-field">
                  <label htmlFor="f7-pz">{action === 'DIVIDEND' ? tr('trade.euro_per_share') : tr('trade.price')}</label>
                  <div className={'te-input' + (letturaPrice && !letturaPrice.ok ? ' is-bad' : '')}>
                    <input id="f7-pz" className="num" type="text" inputMode="decimal"
                           title={tr('numeri.grafia_richiesta', { esempio: priceLanguage === 'it' ? '1.234,56' : '1,234.56' })}
                           autoComplete="off" value={price}
                           placeholder={action === 'DIVIDEND' ? tr('trade.example_dividend') : tr('trade.example_price')}
                           aria-invalid={!!(letturaPrice && !letturaPrice.ok)}
                           onChange={e => setPrice(e.target.value)} />
                    <span className="te-suf">{action === 'DIVIDEND' ? 'EUR' : valuta}</span>
                  </div>
                  {letturaPrice && !letturaPrice.ok ? <div className="te-help is-bad">{letturaPrice.motivo}</div>
                    : pos && pos.prezzo_live != null && action !== 'DIVIDEND' ? (
                      <div className="te-help">{w.live(`${num(pos.prezzo_live)} ${pos.valuta}`)}
                        {ordineValido && tr('trade.distance_live', { a: segno((priceN / pos.prezzo_live - 1) * 100) })}</div>
                    ) : null}
                </div>
                <div className="te-field">
                  <label htmlFor="f7-vl">{tr('trade.currency')}</label>
                  <div className="te-input">
                    <select id="f7-vl" value={valuta} disabled={action === 'DIVIDEND'} onChange={e => setValuta(e.target.value)}>
                      {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </div>
                  <div className="te-help num">
                    {action === 'DIVIDEND' ? tr('trade.dividends_euro')
                      : cambio.fonte === 'assente' ? tr('trade.fx_absent_upper')
                        : `1 ${valuta} = ${num(cambio.tasso, 6)} EUR`}
                  </div>
                </div>
              </div>

              <div className="te-row2 te-row-total">
                <div className={'te-total' + (sim.valido ? '' : ' is-empty')}>
                  <span className="te-total-k">{action === 'DIVIDEND' ? w.totalProceeds : w.total}</span>
                  <span className="te-total-v num">{sim.valido && conv.eur != null ? eur(conv.eur) : sim.valido ? `${num(conv.locale)} ${valuta}` : '— €'}</span>
                  <span className="te-total-chain num">
                    {!sim.valido ? w.totalEmpty
                      : conv.eur != null
                        ? `${exact(qtyN)} × ${num(priceN)} ${valuta} = ${num(conv.locale)} ${valuta} · ${w.fxRate(num(cambio.tasso, 6))}`
                        : <>{exact(qtyN)} × {num(priceN)} {valuta} · <b>{tr('trade.fx_word')} {valuta}{tr('trade.eur_unavailable')}</b>{tr('trade.eur_not_calculable_here')}</>}
                  </span>
                  {sim.valido && cambio.fonte === 'assente' && (
                    <span className="te-total-chain">{tr('trade.no_fx_source')} {valuta}{tr('trade.no_fx_payload')}{ratesErr ? ` (${ratesErr})` : ''}. {tr('trade.no_invented_fx')}</span>
                  )}
                </div>
                <div className="te-field">
                  <label htmlFor="f7-rz">{w.rationale} <span className="te-muted">· {w.rationaleHint}</span></label>
                  <textarea id="f7-rz" className="te-ta" rows={3} value={rationale}
                            placeholder={tr('trade.rationale_example')}
                            onChange={e => setRationale(e.target.value)} />
                </div>
              </div>

              <div className="te-field">
                <label htmlFor="f7-nt">{tr('trade.note')}</label>
                <div className="te-input">
                  <input id="f7-nt" value={note} placeholder={tr('trade.free_text')} onChange={e => setNote(e.target.value)} />
                </div>
              </div>

              <details className="te-more" open={dettagli} onToggle={e => setDettagli((e.currentTarget as HTMLDetailsElement).open)}>
                <summary><ChevronRight size={16} className="te-chev" aria-hidden="true" /> {w.details}<span className="te-more-note">{dettagliNota}</span></summary>
                <div className="te-more-body">
                  <div className="te-row2">
                    <div className="te-field">
                      <label htmlFor="f7-data">{w.execDate}</label>
                      <div className="te-input"><input id="f7-data" type="date" min="2000-01-01" max={oggiISO()}
                        value={tradeDay} onChange={e => setTradeDay(e.target.value)} /></div>
                    </div>
                    <div className="te-field">
                      <label htmlFor="f7-ora">{w.execTime}</label>
                      <div className="te-input"><input id="f7-ora" type="time" step="1"
                        value={tradeTime} onChange={e => setTradeTime(e.target.value)} /></div>
                    </div>
                  </div>
                  <div className="te-help">{tr('trade.date_help')}</div>
                  {tradeDay && <Nota tono="warn">{tr('trade.dated_trade_help')}</Nota>}

                  <div className="te-field">
                    <label htmlFor="f7-decisione">{tr('trade.decision_link')}</label>
                    <div className="te-input">
                      <Link2 size={16} className="te-muted" aria-hidden="true" />
                      <select id="f7-decisione" value={selectedDecision}
                        onChange={e => {
                          setSelectedDecision(e.target.value);
                          if (e.target.value !== 'none') { setManualDivergenceDecision(''); setManualDivergenceReason(''); }
                        }}>
                        <option value="none">{tr('trade.manual_no_decision')}</option>
                        <option value="unknown">{tr('trade.unknown_link')}</option>
                        {/* ⚠ si confronta col ticker INSERITO: con `d.ticker` la decisione era
                            confrontata con se stessa e il menu elencava decisioni di qualsiasi
                            ticker. Un ticker broker diverso resta ammesso solo per la decisione
                            gia' scelta, e chiede la verifica ISIN qui sotto. */}
                        {!['none', 'unknown'].includes(selectedDecision)
                          && !decisions.some(d => String(d.id) === selectedDecision && compatibileQui(d))
                          && <option value={selectedDecision}>#{selectedDecision} {tr('trade.incompatible_option')}</option>}
                        {decisions.filter(compatibileQui).map(d => (
                          <option key={d.id} value={String(d.id)}>#{d.id} · {d.action} {d.ticker} · {d.timestamp.slice(0, 10)} · {d.status}
                            {d.esecuzione ? tr('trade.executed_amount', { a: eur(d.esecuzione.eur), b: d.esecuzione.inferito ? tr('trade.inferred') : '' }) : ''}</option>
                        ))}
                      </select>
                    </div>
                    <div className={'te-help' + (decisionsErr ? ' is-bad' : '')}>
                      {decisionsErr ? tr('trade.decisions_unavailable', { a: decisionsErr }) : tr('trade.decision_help')}
                    </div>
                    {/* 10/10 (Opus 5.5): provenienza Trade Idea non letta = nessuna decisione senza
                        `trade_idea` e' collegabile; il motivo si dichiara, non si lascia un menu vuoto muto. */}
                    {!decisionsErr && tiStorage != null && <div className="te-note is-warn" role="status" data-te-collega-sospeso>
                      {tr('trade.link_suspended_unreadable')}</div>}
                  </div>

                  <div className="te-field">
                    <label htmlFor="f7-divergence">{w.divergence}</label>
                    <div className="te-input">
                      <select id="f7-divergence" value={manualDivergenceDecision}
                        onChange={e => {
                          setManualDivergenceDecision(e.target.value);
                          setManualDivergenceMessage(null);
                          if (e.target.value) setSelectedDecision('none');
                        }}>
                        <option value="">{tr('trade.manual_divergence_none')}</option>
                        {decisions.filter(d => statoDivergenza(d) !== null
                          && ((['BUY', 'ADD'].includes(d.action) && ['BUY', 'ADD'].includes(action))
                            || (['SELL', 'TRIM'].includes(d.action) && ['SELL', 'TRIM'].includes(action))))
                          .map(d => <option key={d.id} value={String(d.id)}>#{d.id} · {d.action} {d.proposal_ticker || d.ticker} · {etichettaStatoDivergenza(d.assessment_status)}</option>)}
                      </select>
                    </div>
                    <div className="te-help">{w.divergenceHelp}</div>
                  </div>
                  {manualDivergenceRow && <div className="te-subcard is-bad">
                    <p>{statoDivergenza(manualDivergenceRow)
                      ? spiegazioneDivergenza(statoDivergenza(manualDivergenceRow)!, manualDivergenceRow.proposal_ticker || manualDivergenceRow.ticker)
                      : tr('trade.manual_divergence_requires_blocked')}</p>
                    <div className="te-input">
                      <input aria-label={tr('trade.manual_divergence_reason_required')}
                        placeholder={tr('trade.manual_divergence_reason_required')}
                        value={manualDivergenceReason} onChange={e => setManualDivergenceReason(e.target.value)} />
                    </div>
                  </div>}
                  {identityTargetDecision && (identityNeeded
                    ? <div className="te-subcard is-warn">
                        <p><b>{w.identity}.</b> {tr('trade.identity_different_tickers', { proposal: identityProposalTicker, execution: tickerUp })}</p>
                        <div className="te-row2">
                          <div className="te-input"><input aria-label={tr('trade.identity_isin_label')} placeholder={tr('trade.identity_isin_label')}
                            value={identityIsin} onChange={e => setIdentityIsin(e.target.value)} /></div>
                          <div className="te-input"><input aria-label={tr('trade.identity_source_label')} placeholder={tr('trade.identity_source_label')}
                            value={identitySource} onChange={e => setIdentitySource(e.target.value)} /></div>
                          <div className="te-input"><input aria-label={tr('trade.identity_verified_at_label')} type="datetime-local"
                            value={identityVerifiedAt} onChange={e => setIdentityVerifiedAt(e.target.value)} /></div>
                          <div className="te-input"><input aria-label={tr('trade.identity_reason_label')} placeholder={tr('trade.identity_reason_label')}
                            value={identityReason} onChange={e => setIdentityReason(e.target.value)} /></div>
                        </div>
                      </div>
                    : <div className="te-help">{tr('trade.identity_same_ticker', { proposal: identityProposalTicker, execution: tickerUp || identityProposalTicker })}</div>)}
                </div>
              </details>
              {identityTargetDecision && identityNeeded && !dettagli && <Nota tono="warn">{tr('trade.identity_check_required_short')}</Nota>}

              {/* ── AVVISI: una riga, il resto nei dettagli ── */}
              {discorde && (
                <Nota tono="warn"><b>{tr('trade.currency_differs')}</b> {tickerUp} {tr('trade.quoted_in')} {discorde.valutaBook}{tr('trade.you_chose')} {discorde.valutaScelta}
                  {discorde.fattoreCento && <> — <b>{tr('trade.factor_hundred')}</b></>}{tr('trade.check_price')}</Nota>
              )}
              {sim.caricoMuto && sim.caricoMuto !== 'ordine-incompleto' && (
                <Nota tono="info" dettaglio={sim.caricoMuto === 'valuta-discorde' ? tr('trade.no_mixed_currency_average') : undefined}>
                  <b>{tr('trade.cost_after_unknown')}</b>{' — '}{perche(sim.caricoMuto, discorde ? [discorde.valutaBook, discorde.valutaScelta] : undefined)}.
                </Nota>
              )}
              {sfora && (
                <Nota tono="bad" dettaglio={<>{tr('trade.already_executed_intro')} {tr('trade.already_executed')} {tr('trade.at_broker')}</>}>
                  <b>{tr('trade.not_covered_shortfall')} {eur(sim.mancano)}.</b>
                </Nota>
              )}
              {avviso && <Nota tono="bad" avviso>{noticeText(avviso, tr)}</Nota>}

              <button type="submit" disabled={submitting || preparing}
                      className={'bbn-btn is-primary te-submit' + (sfora ? ' is-danger' : '')}>
                {submitting ? <RefreshCw size={15} className="animate-spin" aria-hidden="true" /> : <Check size={15} aria-hidden="true" />}
                {submitting ? w.writing : preparing ? w.checking : w.submit(verboTesto)}
              </button>
            </form>

            {/* ── L'ESITO: annunciato, e raggiungibile dal fuoco ── */}
            <div ref={esitoRef} tabIndex={-1} role="status" aria-live="polite" className="te-esito">
              {erroreScrittura && (
                <Nota tono="bad" verbatim={erroreScrittura}><b>{tr('trade.trade_not_written')}</b> {tr('trade.backend_rejected')}</Nota>
              )}
              {esitoIgnoto && (
                <Nota tono="unknown" verbatim={esitoIgnoto}><b>{tr('trade.unknown_outcome')}</b> {tr('trade.request_sent_no_confirmation')} <b>{tr('trade.may_be_written')}</b>{tr('trade.check_movements')} <b>{tr('trade.before')}</b> {tr('trade.retry_duplicate_warning')}</Nota>
              )}
              {esito && !esito.riconosciuta && (
                <Nota tono="unknown"><b>{tr('trade.unrecognized_response')}</b>{tr('trade.has_neither')} <code>ok</code> {tr('trade.nor')}
                  <code> trade_id</code>{tr('trade.cannot_assert_trade')}</Nota>
              )}
              {esito && esito.riconosciuta && esito.stato === 'aggiornata' && (
                <Nota tono="good"><b>{tr('trade.trade_ref', { id: esito.tradeId })}</b> {tr('trade.recorded_available_cash')} <b>{eur(esito.cassa)}</b>.</Nota>
              )}
              {esito && esito.riconosciuta && esito.stato === 'aggiornata-con-nota' && (
                <Nota tono="warn" verbatim={`« ${esito.nota} »`}><b>{tr('trade.trade_ref', { id: esito.tradeId })}</b> {tr('trade.recorded_cash')} <b>{eur(esito.cassa)}</b>, <b>{tr('trade.backend_note_attached')}</b>:</Nota>
              )}
              {esito && esito.riconosciuta && esito.stato === 'non-aggiornata' && (
                <Nota tono="warn" verbatim={`« ${esito.nota} »`}><b>{tr('trade.trade_ref', { id: esito.tradeId })}</b> {tr('trade.recorded_comma')} <b>{tr('trade.cash_not_updated')}</b>{tr('trade.backend_says_dot')}</Nota>
              )}
              {esito && esito.riconosciuta && esito.stato === 'muta' && (
                <Nota tono="warn"><b>{tr('trade.trade_ref', { id: esito.tradeId })}</b> {tr('trade.recorded_comma')} <b>{tr('trade.cash_update_not_shown')}</b> {tr('trade.cash_missing_reason')}</Nota>
              )}
              {/* GUARDIA PREZZI: ortogonale allo stato cassa — la scrittura è
                  passata, ma il prezzo è lontano dall'ultimo riferimento. */}
              {esito && esito.riconosciuta && esito.guardia && (
                <Nota tono="warn" verbatim={`« ${esito.guardia} »`}><b>{tr('trade.price_guard')}</b> {tr('trade.price_guard_explanation')}</Nota>
              )}
              {tradeResult?.data && <Nota tono="info">
                {tr('trade.date_prefix')} <b>{tradeResult.data.replace('T', ' ')}</b>
                {tradeResult.ora_convenzionale && tr('trade.conventional_time')}.
                {' '}{tr('trade.link_prefix')} {tradeResult.link_origin === 'explicit' ? tr('trade.decision_id', { a: tradeResult.decisione?.id ?? tr('trade.na') })
                  : tradeResult.link_origin === 'none' ? tr('trade.manual_link') : tr('trade.not_declared')}.
                {tradeResult.fx && <> {tr('trade.fx_prefix')} {exact(tradeResult.fx.tasso)} · {tr(tradeResult.fx.fonte === 'identity' ? 'trade.already_euros' : tradeResult.fx.fonte === 'storico' ? 'trade.historical' : 'trade.current_not_historical')}.
                  {' '}{tradeResult.fx.nota}</>}
                {tradeResult.ricalcolo && <> {tr('trade.successive_checked')} {tradeResult.ricalcolo.trade_successivi.length}.
                  {' '}{tradeResult.ricalcolo.note?.join(' ')}</>}
              </Nota>}
              {tradeResult?.performance_note && <Nota tono="warn">{tr('trade.trade_recorded')} {tradeResult.performance_note}</Nota>}
              {manualDivergenceMessage && <Nota tono={manualDivergenceMessage.error ? 'bad' : 'good'}>{manualDivergenceMessage.text}</Nota>}
            </div>
          </Card>

          {/* ── GLI ULTIMI ORDINI ── */}
          <Card titolo={w.recent} className="te-recent te-stretch" azioni={<span className="bbn-card-note">{w.recentNote}</span>}>
            {tradesErr ? (
              <p className="bbn-empty">{tr('trade.history_unavailable_prefix')} <b>{tradesErr}</b>.</p>
            ) : ultimi.length === 0 && !sim.valido ? (
              <p className="bbn-empty">{tr('trade.no_archived_movements')}</p>
            ) : (
              <ul className="te-orders">
                {sim.valido && (
                  <li className="te-orow is-now">
                    <span className="te-orow-d">{w.now}</span>
                    <span className="te-orow-tk">{tickerUp}</span>
                    <span className="te-bar" aria-hidden="true">{delta != null && <i style={{ width: pct(delta / scalaOrdini) }} />}</span>
                    <span className="te-orow-v num"><span className={'te-verb ' + (entra ? 'is-in' : 'is-out')}>{action === 'DIVIDEND' ? tr('trade.div_short') : action}</span>{eur(conv.eur, 0)}</span>
                  </li>
                )}
                {ultimi.map((t, i) => (
                  <li className="te-orow" key={`${t.data}-${t.ticker}-${i}`} title={`${num(t.locale)} ${t.valuta}`}>
                    <span className="te-orow-d num">{gg(t.data)}</span>
                    <span className="te-orow-tk">{t.ticker}</span>
                    <span className="te-bar" aria-hidden="true">{t.eur != null && <i style={{ width: pct(t.eur / scalaOrdini) }} />}</span>
                    <span className="te-orow-v num">
                      <span className={'te-verb ' + (['BUY', 'ADD'].includes(t.action) ? 'is-in' : 'is-out')}>{t.action === 'DIVIDEND' ? tr('trade.div_short') : t.action}</span>
                      {t.eur != null ? eur(t.eur, 0) : <span className="te-muted">{tr('trade.fx_word')} {t.valuta} {tr('trade.na')}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="te-col">
          {/* ── EFFETTO SUL PORTAFOGLIO ── */}
          <Card titolo={w.preview} className="te-preview"
                azioni={<><span className="bbn-chip">{tr('trade.live_book_simulation')}</span><span className="bbn-grow" />
                  <span className="bbn-card-note">{sim.valutazione === 'eseguito-proxy' ? w.previewProxy : w.previewMarket}</span></>}>
            <div className="te-body">
              {posErr ? (
                <Nota tono="bad"><b>{tr('trade.portfolio_unavailable')}</b> — {posErr}. {tr('trade.cashbar_missing_book')}</Nota>
              ) : cassa == null ? (
                <Nota tono="warn"><b>{tr('trade.cash_payload_absent')}</b> — {portfolioValues(snap).note}.{' '}
                  {tr('trade.missing_nonnumeric_capacity')} <b>{tr('trade.cannot_verify')}</b>.</Nota>
              ) : cassa <= 0 ? (
                <Nota tono="warn"><b>{cassa === 0 ? tr('trade.zero_cash') : tr('trade.overdraft', { a: eur(-cassa) })}</b>
                  {' '}{tr('trade.is_a_measurement')} <b>{tr('trade.measurement')}</b>.</Nota>
              ) : (
                <div className="te-cashwrap">
                  <span className="te-k">{w.cash}</span>
                  {pezzo && sim.cassaDopo != null ? (
                    <div className="te-cashline">
                      <span className="te-cash-from num">{eur(cassa)}</span><span className="te-muted" aria-hidden="true">→</span>
                      <span className={'te-cash-to num' + (sfora ? ' is-bad' : '')}>{eur(sim.cassaDopo)}</span>
                      <span className={'bbn-pill ' + (sfora ? 'is-giu' : entra ? 'is-piatto' : 'is-su')}>{entra ? '−' : '+'}{eur(delta)}</span>
                    </div>
                  ) : (
                    <div className="te-cashline"><span className="te-cash-to num">{eur(cassa)}</span><span className="te-muted">{w.inCash}</span></div>
                  )}
                  <div className={'te-cashbar' + (sfora ? ' is-bad' : '')} role="img"
                       aria-label={pezzo && sim.cassaDopo != null ? `${w.cash} ${eur(cassa)} → ${eur(sim.cassaDopo)}` : `${w.cash} ${eur(cassa)}`}>
                    {!pezzo ? <i className="te-rest" style={{ width: '100%' }} />
                      : entra ? <>
                        <i className="te-rest" style={{ width: pct(sim.cassaDopo != null && sim.cassaDopo > 0 ? sim.cassaDopo / cassa : 0) }} />
                        <i className="te-bite" style={{ left: pct(sim.cassaDopo != null && sim.cassaDopo > 0 ? sim.cassaDopo / cassa : 0), width: pct(Math.min(1, (delta ?? 0) / cassa)) }} />
                      </> : <>
                        <i className="te-rest" style={{ width: pct(pezzo.da) }} />
                        <i className="te-bite is-in" style={{ left: pct(pezzo.da), width: pct(pezzo.largo) }} />
                      </>}
                  </div>
                  <div className="te-legend">
                    <span><i className="te-sw te-sw-rest" />{w.restInCash}</span>
                    {pezzo && <span><i className={'te-sw ' + (sfora ? 'te-sw-bad' : entra ? 'te-sw-bite' : 'te-sw-in')} />
                      {sfora ? w.beyondCash : entra ? w.usedByOrder : w.enteringCash}
                      {entra && delta != null && <> · {w.ofCash(num((delta / cassa) * 100, 1))}</>}</span>}
                  </div>
                </div>
              )}
              {sim.valido && entra && sim.copre === null && cassa != null && (
                <Nota tono="unknown"><b>{tr('trade.capacity_unknown')}</b> — {tr('trade.euro_value_unknown')}.</Nota>
              )}

              <div className="te-tiles">
                <Tessera k={sim.valido && tickerUp ? w.position(tickerUp) : w.positionGeneric} vuota={!sim.valido} attesa={w.waiting}
                  v={sim.qtaMuta ? '—' : sim.qtaDopo != null ? `${exact(sim.qtaDopo)}` : tr('trade.na')}
                  sotto={sim.qtaMuta ? perche(sim.qtaMuta)
                    : sim.nuovaPosizione ? w.newPosition
                      : sim.qtaPrima != null && sim.qtaDopo != null
                        ? <>{w.from(exact(sim.qtaPrima))} · <span className={sim.qtaDopo >= sim.qtaPrima ? 'te-up' : 'te-down'}>{(sim.qtaDopo - sim.qtaPrima > 0 ? '+' : '') + exact(sim.qtaDopo - sim.qtaPrima)}</span></>
                        : tr('trade.na')} />
                <Tessera k={w.cost} vuota={!sim.valido} attesa={w.waiting}
                  v={sim.caricoMuto ? '—' : `${num(sim.caricoDopo)} ${pos?.valuta || valuta}`}
                  sotto={sim.caricoMuto ? perche(sim.caricoMuto, discorde ? [discorde.valutaBook, discorde.valutaScelta] : undefined)
                    : sim.caricoInvariato ? w.unchanged
                      : sim.caricoPrima != null && sim.caricoDopo != null ? <>{w.from(num(sim.caricoPrima))} · {segno(sim.caricoDopo - sim.caricoPrima)}</>
                        : tr('trade.new_cost')} />
                <Tessera k={w.weight} vuota={!sim.valido} attesa={w.waiting}
                  v={sim.pesoMuto ? '—' : num(sim.pesoDopo) + '%'}
                  sotto={sim.pesoMuto ? perche(sim.pesoMuto)
                    : sim.pesoPrima != null && sim.pesoDopo != null ? <>{w.from(num(sim.pesoPrima) + '%')} · {segno(sim.pesoDopo - sim.pesoPrima)} pt</> : tr('trade.na')} />
                <Tessera k={w.nav} vuota={!sim.valido} attesa={w.waiting}
                  v={sim.navMuto ? '—' : eur(sim.navDopo, 0)}
                  sotto={sim.navMuto ? perche(sim.navMuto)
                    : sim.navDelta != null ? (Math.abs(sim.navDelta) < 0.005 ? w.unchanged : <>{w.from(eur(sim.navPrima, 0))} · {segno(sim.navDelta)} €</>)
                      : tr('trade.na')} />
              </div>

              <div className="te-size">
                <div className="te-size-txt">
                  {sim.valido && volte != null && taglie.mediana != null ? <>
                    <b>{w.sizeTimes(num(volte, 1) + '×', eur(taglie.mediana, 0))}</b>
                    {taglie.massimo != null && delta != null && <> · <span className={delta > taglie.massimo ? 'te-down' : undefined}>
                      {delta > taglie.massimo ? w.sizeAboveMax(eur(taglie.massimo, 0)) : w.sizeBelowMax(eur(taglie.massimo, 0))}</span></>}.
                    <br /><span className="te-small">{w.sizeBasis(taglie.misurate.length)}</span>
                  </> : taglie.mediana != null ? w.sizeEmpty(eur(taglie.mediana, 0)) : w.sizeEmptyNoHistory}
                  {(taglie.senzaCambio > 0 || taglie.senzaImporto > 0 || tradesErr) && <span className="te-small">
                    {taglie.senzaCambio > 0 && <> {taglie.senzaCambio} {tr('trade.orders_no_marker')} {taglie.scoperte.join(', ')}.</>}
                    {/* integrazione G9a+G9b: ordini con quantita'/prezzo illeggibile esclusi dalla mediana: si dichiarano */}
                    {taglie.senzaImporto > 0 && <> {tr('trade.orders_no_amount', { n: String(taglie.senzaImporto) })}.</>}
                    {tradesErr && <> {tr('trade.history_no_markers')}.</>}</span>}
                </div>
                <Istogramma valori={taglie.misurate.map(t => t.eur as number)} mio={sim.valido ? delta : null}
                  mediana={taglie.mediana} etichetta={w.sizeChart} />
              </div>

              {sim.valido && (
                <details className="te-how">
                  <summary>{w.moreInfo}</summary>
                  <p>{tr('trade.this_is_a')} {tr('trade.simulation')}{tr('trade.cash_moves_entered')} {tr('trade.entered')}{tr('trade.securities_valued')}{' '}
                    {sim.valutazione === 'mercato' ? <>{tr('trade.at')} {tr('trade.market_price')}</>
                      : sim.valutazione === 'eseguito-proxy' ? <>{tr('trade.at')} {tr('trade.entered_price')}{tr('trade.new_name_no_market_price')}</>
                        : <>{tr('trade.no_price_valuation')}</>}.
                    {' '}{tr('trade.nav_difference')} {tr('trade.our_arithmetic')}{tr('trade.backend_usually_matches')}</p>
                </details>
              )}
            </div>
          </Card>

          {/* ── IL PORTAFOGLIO: clic = precompila il ticket ── */}
          <Card titolo={w.portfolio} className="te-book te-stretch"
                azioni={<span className="bbn-card-note">{bookAssente ? tr('trade.unavailable') : w.portfolioNote(book.righe.length)}</span>}>
            {posErr ? (
              <p className="bbn-empty">{tr('trade.positions_unavailable_upper')} {posErr}{tr('trade.book_error_refresh')}</p>
            ) : loading && book.righe.length === 0 ? (
              <p className="bbn-empty">{tr('trade.positions_loading')}</p>
            ) : book.righe.length === 0 ? (
              <p className="bbn-empty">{tr('trade.no_position_buy')}</p>
            ) : <>
              <div className="te-lhead" aria-hidden="true"><span /><span>{w.colTitle}</span><span>{w.colValue}</span><span>{w.colPl}</span></div>
              <ul className="te-rows">{book.righe.map(r => {
                const on = r.pos.ticker.toUpperCase() === tickerUp;
                return <li key={r.pos.ticker}>
                  {/* un comando VERO: uno spazio battuto per scorrere non deve
                      sovrascrivere in silenzio il modulo che stavi compilando */}
                  <button type="button" className={'te-row' + (on ? ' is-on' : '')} aria-pressed={on}
                          aria-label={tr('trade.use_ticker', { a: r.pos.ticker })} onClick={() => selectPosition(r.pos)}>
                    <IconaTitolo ticker={r.pos.ticker} nome={r.pos.nome} dimensione="sm" />
                    <span className="te-row-name"><b>{r.pos.ticker}</b>
                      <span>{nomeTitolo(r.pos) !== r.pos.ticker.split('.')[0] ? nomeTitolo(r.pos) + ' · ' : ''}{w.carry(exact(r.pos.quantita), num(r.pos.prezzo_medio), r.peso != null ? num(r.peso) + '%' : tr('trade.na'))}</span></span>
                    <span className="te-row-value"><b className="num">{eur(r.pos.valore_mercato, 0)}</b>
                      <span className="num">{r.pos.prezzo_live != null ? w.live(num(r.pos.prezzo_live)) : tr('trade.na')}</span></span>
                    <span className="te-row-pl">
                      {r.pos.pl_pct == null ? <span className="bbn-pill is-piatto">{tr('trade.na')}</span>
                        : <PastigliaVariazione valore={r.pos.pl_pct}><span className="num">{segno(r.pos.pl_pct)}%</span></PastigliaVariazione>}
                    </span>
                  </button>
                </li>;
              })}</ul>
              {book.senzaValore > 0 && <p className="te-foot">{tr('trade.missing_value_at_end', { a: book.senzaValore })}</p>}
            </>}
          </Card>
        </div>
      </div>

      {/* ══════════════ CASSA ══════════════ */}
      <div className="te-grid te-cash" hidden={view !== 'cash'}>
        <Card titolo={w.cashMovement} className="te-cashform" azioni={<span className="bbn-card-note">{w.cashTwr}</span>}>
          <form onSubmit={inviaMovimento} className="te-body te-fill">
            <div className="bbn-seg is-full" role="radiogroup" aria-label={tr('trade.deposit_or_withdraw')} onKeyDown={mvKeyDown}>
              {([['DEPOSIT', w.deposit], ['WITHDRAWAL', w.withdraw]] as const).map(([t, l]) => (
                <button key={t} type="button" role="radio" aria-checked={t === mvTipo} className={t === mvTipo ? 'is-on' : undefined}
                        tabIndex={t === mvTipo ? 0 : -1}
                        onClick={() => { setMvTipo(t); setMvRifiuto(null); setMvEsito(null); setMvAvviso(null); }}>{l}</button>
              ))}
            </div>

            {/* ⚠ type="text" e non "number", come tutto il resto di F7.
                ⚠ ogni onChange BUTTA il rifiuto pendente: v. `scordaRifiuto` */}
            <div className="te-field te-amount">
              <label htmlFor="f7-mv-imp">{tr('trade.amount')}</label>
              <div className={'te-input' + (letturaImporto && !letturaImporto.ok ? ' is-bad' : '')}>
                <input id="f7-mv-imp" ref={mvImportoRef} className="num" type="text" inputMode="decimal"
                       title={tr('numeri.grafia_richiesta', { esempio: cashLanguage === 'it' ? '1.234,56' : '1,234.56' })}
                       autoComplete="off" value={mvImporto} placeholder={tr('trade.amount_example')}
                       aria-invalid={!!(letturaImporto && !letturaImporto.ok)}
                       onChange={e => { setMvImporto(e.target.value); scordaRifiuto(); }} />
                <span className="te-suf">EUR</span>
              </div>
              <div className={'te-help' + ((letturaImporto && !letturaImporto.ok) || prelievoScoperto ? ' is-bad' : '')}>
                {letturaImporto && !letturaImporto.ok ? letturaImporto.motivo
                  : prelievoScoperto ? tr('trade.withdrawal_refused', { a: eur(cassa) })
                    : cassa == null ? tr('trade.cash_capacity_unavailable') : w.amountHint}
              </div>
            </div>

            <div className="te-row2">
              <div className="te-field">
                <label htmlFor="f7-mv-dt">{tr('trade.value_date')}</label>
                <div className={'te-input' + (letturaData && !letturaData.ok ? ' is-bad' : '')}>
                  <input id="f7-mv-dt" className="num" type="text" inputMode="numeric" autoComplete="off" value={mvData} placeholder={mvOggi}
                         aria-invalid={!!(letturaData && !letturaData.ok)}
                         onChange={e => { mvDataAuto.current = false; setMvData(e.target.value); scordaRifiuto(); }} />
                  <span className="te-suf">ISO</span>
                </div>
                <div className={'te-help' + (letturaData && !letturaData.ok ? ' is-bad' : '')}>
                  {letturaData && !letturaData.ok ? letturaData.motivo : tr('trade.flow_date_help')}
                </div>
              </div>
              <div className="te-field">
                <label htmlFor="f7-mv-nt">{tr('trade.description')}</label>
                <div className="te-input">
                  <input id="f7-mv-nt" type="text" autoComplete="off" value={mvNota} placeholder={tr('trade.bank_transfer_example')}
                         onChange={e => { setMvNota(e.target.value); scordaRifiuto(); }} />
                </div>
                <div className="te-help">{tr('trade.stored_in')} <code>note</code>{tr('trade.trimmed_edges')}</div>
              </div>
            </div>

            {/* «la cassa passa da X a Y» è una PREVISIONE: si tace dove si sa già
                che non si avvererà (riga gemella, rifiuto a schermo, prelievo scoperto). */}
            {previsioneCredibile && cassaDopoMovimento != null && cassa != null && (
              <div className="te-effect" aria-label={w.effect}>
                <span className="te-k">{w.effect}</span>
                <div className="te-effect-row"><span>{w.cashAvailable}</span>
                  <span className="num">{eur(cassa)} → <b>{eur(cassaDopoMovimento)}</b>{' '}
                    <span className={'bbn-pill ' + (mvTipo === 'DEPOSIT' ? 'is-su' : 'is-giu')}>{mvTipo === 'DEPOSIT' ? '+' : '−'}{eur(importoN)}</span></span></div>
                {cassa > 0 && cassaDopoMovimento > 0 && (() => {
                  const scalaMv = Math.max(cassa, cassaDopoMovimento);
                  const base = Math.min(cassa, cassaDopoMovimento) / scalaMv;
                  return <div className="te-cashbar is-thin" aria-hidden="true">
                    <i className="te-rest" style={{ width: pct(base) }} />
                    <i className={'te-bite ' + (mvTipo === 'DEPOSIT' ? 'is-in' : 'is-out')} style={{ left: pct(base), width: pct(1 - base) }} />
                  </div>;
                })()}
                {flussoUltimo != null && flussoDopo != null && <div className="te-effect-row"><span>{w.flowsTwr}</span>
                  <span className="num">{eur(flussoUltimo, 0)} → <b>{eur(flussoDopo, 0)}</b></span></div>}
                {dataISO && <div className="te-effect-row"><span>{w.valueDate}</span>
                  <span className="num"><b>{gg(dataISO)}/{dataISO.slice(0, 4)}</b>{dataISO === mvOggi ? ' · ' + w.today : ''}</span></div>}
              </div>
            )}

            {mvAvviso && <Nota tono="bad" avviso>{noticeText(mvAvviso, tr)}</Nota>}
            {/* L'ANTICIPO DELLA GUARDIA: la riga gemella esiste già. Non blocca —
                la guardia vera è del backend — ma toglie la sorpresa. */}
            {gemella && !mvRifiuto && (
              <Nota tono="warn"><b>{tr('trade.row_exists')}</b> (id={gemella.id}{tr('trade.duplicate_row_explanation')}</Nota>
            )}

            {/* `disabled` sul solo invio in volo: ogni modifica ai campi butta il
                rifiuto, quindi REGISTRA e CONFERMO non sono mai vivi sullo stesso corpo. */}
            <button type="submit" className="bbn-btn is-primary te-submit" disabled={mvInvio || !!mvRifiuto}>
              <Check size={15} aria-hidden="true" />
              {mvInvio ? w.sending : mvTipo === 'DEPOSIT' ? w.recordDeposit : w.recordWithdrawal}
            </button>

            {/* La live region è l'annunciatore INVISIBILE: un `aria-live` che entra
                nell'albero INSIEME al testo non viene annunciato. */}
            <div className="lb-annuncio te-sr" role="status" aria-live="polite">
              {mvRifiuto ? tr('trade.movement_rejected_reason', { a: mvRifiuto.rifiuto.motivo })
                : mvIgnoto ? tr('trade.server_no_response')
                  : mvEsito ? (mvEsito.riconosciuta ? tr('trade.movement_recorded_id', { a: mvEsito.movimentoId }) : tr('trade.unrecognized_response_dot')) : ''}
            </div>
            {mvRifiuto && <>
              <Nota tono="warn" verbatim={`« ${mvRifiuto.rifiuto.motivo} »`}>
                <b>{mvRifiuto.rifiuto.quale === 'duplicato' ? tr('trade.duplicate_registered')
                  : mvRifiuto.rifiuto.quale === 'soglia' ? tr('trade.threshold_confirmation') : tr('trade.movement_rejected')}</b>{' '}
                {/* «NON è stato scritto» solo dove il backend lo GARANTISCE (422/401). */}
                {mvRifiuto.rifiuto.nonScritto
                  ? <>{tr('trade.the_movement')} <b>{tr('trade.not_upper')}</b> {tr('trade.was_recorded')}</>
                  : <>{tr('trade.backend_responded')} <b>HTTP {mvRifiuto.rifiuto.status}</b>: <b>{tr('trade.cannot_say')}</b> {tr('trade.check_register_before_retry')}</>}
                {' '}{tr('trade.backend_says')}
              </Nota>
              {mvRifiuto.rifiuto.rimediabile && <>
                {/* ANNULLA prende il fuoco e ha la STESSA larghezza: l'INVIO
                    accidentale non deve scavalcare due guardie. */}
                <div className="te-cash-confirm">
                  <button type="button" className="bbn-btn te-confirm-cancel" ref={mvAnnullaRef} disabled={mvInvio}
                          onClick={() => { setMvRifiuto(null); mvImportoRef.current?.focus(); }}>{w.cancel}</button>
                  <button type="button" className="bbn-btn is-primary te-confirm-go" onClick={confermaMovimento} disabled={mvInvio}>
                    {mvInvio ? w.sending : w.confirmIntentional}
                  </button>
                </div>
                {/* La chiave è UNA per DUE guardie: chi conferma deve sapere che spegne anche l'altra. */}
                <p className="te-help">
                  {tr('trade.confirm_resends')} <b>{tr('trade.same')}</b> {tr('trade.movement_with')}{' '}
                  <code>conferma=true</code>{tr('trade.disables')} <b>{tr('trade.both')}</b> {tr('trade.overridable_checks')} <b>{tr('trade.and')}</b> {tr('trade.amount_guard_too')}
                </p>
              </>}
            </>}
            {mvIgnoto && (
              <Nota tono="unknown" verbatim={mvIgnoto}><b>{tr('trade.unknown_outcome_short')}</b>{tr('trade.server_no_reply_so')}{' '}
                <b>{tr('trade.do_not_know')}</b> {tr('trade.whether_movement_written')} <b>{tr('trade.reading_again')}</b> {tr('trade.register_check_row')}</Nota>
            )}
            {mvEsito && (mvEsito.riconosciuta ? (
              <Nota tono={mvEsito.stato === 'aggiornata' ? 'good' : mvEsito.stato === 'muta' ? 'unknown' : 'warn'}
                    verbatim={mvEsito.nota ? `« ${mvEsito.nota} »` : undefined}>
                <b>{tr('trade.movement')} {mvEsito.movimentoId} {tr('trade.recorded_dot')}</b>{' '}
                {/* i quattro stati hanno quattro rami */}
                {mvEsito.stato === 'aggiornata' ? <>{tr('trade.cash_updated')} <b>{eur(mvEsito.cassa)}</b>.</>
                  : mvEsito.stato === 'aggiornata-con-nota' ? <>{tr('trade.cash_updated_to')} <b>{eur(mvEsito.cassa)}</b>{tr('trade.with_backend_note')}</>
                    : mvEsito.stato === 'muta' ? <>{tr('trade.the_response')} <b>{tr('trade.no_cash_in_response')}</b>{tr('trade.register_written_value_unknown')}</>
                      : <>{tr('trade.the_cash')} <b>{tr('trade.not')}</b> {tr('trade.cash_register_diverge')} <b>{tr('trade.diverge')}</b>.</>}
              </Nota>
            ) : (
              <Nota tono="unknown"><b>{tr('trade.unrecognized_response')}</b>{tr('trade.has_neither')} <code>ok</code> {tr('trade.nor')}{' '}
                <code>movement_id</code>{tr('trade.cannot_assert_movement')}</Nota>
            ))}

            <Nota tono="info" spinta>{w.confirmNote}</Nota>
          </form>
        </Card>

        <div className="te-col">
          <section className="bbn-card te-hero" aria-label={w.cashAvailable}>
            <div><div className="te-k">{w.cashAvailable}</div>
              <div className="te-hero-big num">{bookAssente ? tr('trade.na') : cassa != null ? eur(cassa) : tr('trade.na')}</div></div>
            <div><div className="te-k">{w.deposited}</div><div className="te-hero-v num te-up">{movLetto && !movErr ? totaleReso(totaleMovimenti.versati, '+', totaleMovimenti.motivoVersati) : tr('trade.na')}</div></div>
            <div><div className="te-k">{w.withdrawn}</div><div className="te-hero-v num">{movLetto && !movErr ? totaleReso(totaleMovimenti.prelevati, '−', totaleMovimenti.motivoPrelevati) : tr('trade.na')}</div></div>
            <div><div className="te-k">{w.movements}</div><div className="te-hero-v num">{movLetto && !movErr ? (finestraPiena ? '≥ ' : '') + movimenti.length : tr('trade.na')}</div></div>
          </section>
          {movLetto && !movErr && (totaleMovimenti.motivoVersati || totaleMovimenti.motivoPrelevati || totaleMovimenti.parziale) && (
            <p className="te-help is-bad">{[...new Set([totaleMovimenti.motivoVersati, totaleMovimenti.motivoPrelevati]
              .filter(Boolean)), totaleMovimenti.parziale ? tr('trade.totals_partial') : null].filter(Boolean).join(' · ')}</p>
          )}

          {movLetto && !movErr && movimenti.length > 0 && (
            <Card titolo={w.flowsChart} className="te-flows" azioni={<span className="bbn-card-note">{w.flowsChartNote}</span>}>
              <div className="te-body"><GraficoFlussi movimenti={movimenti} flussi={flussi} etichetta={w.flowsChartLabel} oggi={w.today} /></div>
            </Card>
          )}

          <Card titolo={w.register} className="te-register te-stretch"
                azioni={<span className="bbn-card-note">
                  {!movLetto ? tr('trade.register_loading') : movErr ? tr('trade.register_unread')
                    : finestraPiena ? tr('trade.at_least_movements', { a: movimenti.length, b: cassa != null ? eur(cassa) : tr('trade.na') })
                      : w.registerCount(movimenti.length)}</span>}>
            {!movLetto ? <p className="bbn-empty">{tr('trade.reading_register')}</p>
              : movErr ? <p className="bbn-empty">{tr('trade.register_unread_prefix')} <b>{movErr}</b>{tr('trade.unread_register_help')}</p>
                : movimenti.length === 0 ? <p className="bbn-empty">{tr('trade.no_cash_movements')}</p>
                  : <>
                    <div className="te-mhead" aria-hidden="true"><span /><span>{w.colMove}</span><span>{w.colSize}</span><span>{w.colAmount}</span><span>{w.colFlows}</span></div>
                    <ul className="te-moves">{movimenti.map(m => {
                      const dupe = gemella != null && m.id === gemella.id;
                      const cum = flussi.get(m.id);
                      const val = typeof m.amount_eur === 'number' && isFinite(m.amount_eur) ? m.amount_eur : null;
                      const dentro = m.type === 'DEPOSIT';
                      const verso = dentro ? '+' : '−';
                      // l'importo di riga è reso senza decimali: il valore ESATTO sta nel
                      // title e nell'aria-label, insieme all'anno che `gg()` non rende.
                      const esatto = val == null ? tr('trade.amount_na')
                        : `${m.date} · ${dentro ? tr('trade.deposit_lower') : tr('trade.withdrawal_lower')} ${verso}${num(Math.abs(val))} €`;
                      return <li key={m.id} ref={dupe ? mvGemellaRef : undefined}
                                 className={'te-mrow' + (dupe ? ' is-twin' : '')}
                                 aria-label={esatto + (m.note ? ` — ${m.note}` : '')} title={esatto + (m.note ? `\n${m.note}` : '')}>
                        <span className={'te-dir' + (dentro ? '' : ' is-out')} aria-hidden="true">{dentro ? <ArrowDownLeft size={16} /> : <ArrowUpRight size={16} />}</span>
                        <span className="te-mrow-name"><b>{dentro ? tr('trade.deposit_lower') : tr('trade.withdrawal_lower')}</b>
                          <span className="num">{gg(m.date)}/{(m.date || '').slice(0, 4)}{m.note ? ' · ' + m.note : ''}</span></span>
                        <span className="te-bar" aria-hidden="true">{movimentoMax > 0 && val != null && <i className={dentro ? 'is-in' : 'is-out'} style={{ width: pct(Math.abs(val) / movimentoMax) }} />}</span>
                        <span className={'te-mrow-amt num' + (dentro ? ' te-up' : ' te-down')}>{val == null ? tr('trade.amount_na') : verso + eur(Math.abs(val))}</span>
                        <span className="te-mrow-cum num">{cum != null ? eur(cum, 0) : tr('trade.na')}</span>
                      </li>;
                    })}</ul>
                    {/* Le note stanno FUORI dalla lista: con 200 righe finirebbero in fondo allo scroll. */}
                    {finestraPiena && <p className="te-foot">
                      {tr('trade.rows_shown')} <b>{movimenti.length}</b>{tr('trade.window_at_limit')} <b>{tr('trade.cannot_see_older')}</b>{tr('trade.flows_partial')}
                    </p>}
                    <p className="te-foot">{w.flowsFoot}</p>
                  </>}
          </Card>
        </div>
      </div>

      {pending && (
        <ConfirmDialog
          open
          pagePresentation={true}
          variant="nuova"
          tone={pending.preview.cash_disponibile_eur < 0 || pending.discorde ? 'crimson' : 'amber'}
          title={tr('trade.confirm_trade_title')}
          intro={tr('trade.confirm_trade_intro')}
          lead={<div className="bbn-confirm-lead">
            <span className="bbn-confirm-verb">{pending.body.action === 'DIVIDEND' ? tr('trade.div_short') : pending.body.action}</span>
            <span className="bbn-confirm-what"><span>{pending.body.ticker} · {exact(pending.body.quantita)} × {exact(pending.body.prezzo)} {pending.body.valuta}</span>
              <b className="num">{eur(Math.abs(pending.preview.cash_delta_eur))}</b></span>
            <span className={'bbn-pill ' + (pending.preview.cash_disponibile_eur < 0 ? 'is-giu' : 'is-piatto')}>{tr('trade.cash_after')} {eur(pending.preview.cash_disponibile_eur)}</span>
          </div>}
          rows={pendingRows(pending)}
          warn={(pending.preview.cash_disponibile_eur < 0
            ? tr('trade.confirm_shortfall', { a: eur(pending.preview.cash_disponibile_eur) }) : '')
            + tr('trade.preview_expiry', { a: pending.preview.expires_in_seconds })
            + tr('trade.confirm_register_change')
            + tr('trade.confirm_atomic_no_undo')}
          confirmLabel={w.record(w.verbs[pending.body.action as Verbo] ?? pending.body.action)}
          cancelLabel={w.cancel}
          onConfirm={() => { const p = pending; setPending(null); commit(p); }}
          onCancel={() => setPending(null)}
        />
      )}
    </div>
  )} />;
}

const emptyOpening = (): OpeningDraft => ({ ticker: '', nome: '', quantita: '', prezzo_medio: '',
  valuta: 'EUR', giorno: '', ora: '', precisione: 'day', provenienza: '', nota: '' });
type FrozenOpening = ReturnType<typeof congelaPosizioneIniziale>;
const errorDetail = (error: unknown): string => {
  const e = error as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = e?.response?.data?.detail ?? e?.message ?? String(error);
  return typeof detail === 'string' ? detail : JSON.stringify(detail);
};
const errorNotice = (error: unknown): Notice => error instanceof FrontendTradeError
  ? { render: error.renderMessage } : errorDetail(error);

/** Independent channel: the only network calls here are opening-position endpoints. */
function PositionOpeningEntry() {
  const t = useT(), language = useLingua();
  const [draft, setDraft] = useState<OpeningDraft>(emptyOpening);
  const [inputLanguages, setInputLanguages] = useState<Partial<Record<'quantita' | 'prezzo_medio', Lingua>>>({});
  const [pending, setPending] = useState<FrozenOpening | null>(null);
  const [busy, setBusy] = useState<'preview' | 'write' | null>(null);
  const inFlight = useRef(false);
  const [validationAttempted, setValidationAttempted] = useState(false);
  const [failure, setFailure] = useState<{ kind: 'preview' | 'rejected' | 'uncertain'; detail: Notice } | null>(null);
  const [receipt, setReceipt] = useState<OpeningResult | null>(null);
  const [sent, setSent] = useState<FrozenOpening | null>(null);
  const [rows, setRows] = useState<OpeningRecord[] | null>(null);
  const [listError, setListError] = useState<Notice | null>(null);
  const [reading, setReading] = useState(false);
  const [viewed, setViewed] = useState<OpeningRecord | null>(null);
  const [readback, setReadback] = useState<{ ok: boolean; detail?: Notice } | null>(null);
  const statusRef = useRef<HTMLDivElement>(null);
  const locked = !!receipt || failure?.kind === 'uncertain';
  const frozen = locked || !!pending || !!busy;
  const format = (value: number | null) => value == null ? t('trade.opening_na')
    : value.toLocaleString(localeDi(language), { maximumSignificantDigits: 21, useGrouping: true });
  const parsed = useMemo(() => {
    try { return { body: preparaPosizioneIniziale(draft, language, oggiISO(), inputLanguages), error: null }; }
    catch (error) { return { body: null, error: errorDetail(error) }; }
  }, [draft, language, inputLanguages]);
  const change = <K extends keyof OpeningDraft>(key: K, value: OpeningDraft[K]) => {
    if (frozen) return;
    if (key === 'quantita' || key === 'prezzo_medio') {
      const numericKey = key as 'quantita' | 'prezzo_medio';
      setInputLanguages(old => ({ ...old,
        [numericKey]: String(value).trim() === '' ? undefined : draft[numericKey].trim() ? old[numericKey] ?? language : language }));
    }
    setDraft(old => ({ ...old, [key]: value })); setFailure(null);
  };
  const refresh = useCallback(async () => {
    setReading(true);
    try { setRows(leggiPosizioniIniziali(await Bellomberg.openingPositions())); setListError(null); }
    catch (error) { setRows(null); setListError(errorNotice(error)); }
    finally { setReading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { if (receipt || failure) statusRef.current?.focus(); }, [receipt, failure]);
  const preview = async (event: React.FormEvent) => {
    event.preventDefault();
    if (inFlight.current || frozen) return;
    setValidationAttempted(true);
    if (!parsed.body) return;
    const body = { ...parsed.body };
    inFlight.current = true; setBusy('preview'); setFailure(null);
    try { setPending(congelaPosizioneIniziale(body, await Bellomberg.previewOpeningPosition(body))); }
    catch (error) { setFailure({ kind: 'preview', detail: errorNotice(error) }); }
    finally { inFlight.current = false; setBusy(null); }
  };
  const commit = async (value: FrozenOpening) => {
    if (inFlight.current || locked) return;
    inFlight.current = true; setBusy('write'); setPending(null); setSent(value);
    try { setReceipt(leggiRicevutaPosizione(value.body, await Bellomberg.createOpeningPosition(value.body))); setFailure(null); }
    catch (error) { setFailure({ kind: scritturaRifiutata(error) ? 'rejected' : 'uncertain', detail: errorNotice(error) }); }
    finally { inFlight.current = false; setBusy(null); }
    // A failed read never changes a committed write into an uncertain one.
    void refresh();
  };
  const readTicker = async (ticker: string) => {
    setReadback(null);
    try {
      const value = await Bellomberg.openingPosition(ticker);
      const row = leggiPosizioniIniziali({ openings: [value?.opening] })[0];
      if (row.ticker !== ticker) throw new FrontendTradeError(() => tr('trade.opening_list_invalid'));
      setViewed(row); setReadback({ ok: true });
    } catch (error) { setReadback({ ok: false, detail: errorNotice(error) }); }
  };
  const balanceRows = (value: OpeningRecord | FrozenOpening['preview']['opening']): ConfirmRow[] => [
    { k: t('trade.opening_ticker'), v: value.ticker },
    { k: t('trade.opening_qty'), v: format(value.quantita) },
    { k: t('trade.opening_cost'), v: `${format(value.prezzo_medio)} ${value.valuta}` },
    { k: t('trade.opening_date'), v: value.as_of.replace('T', ' ') },
    { k: t('trade.opening_precision'), v: t(value.precisione_data === 'day' ? 'trade.opening_day' : 'trade.opening_second') },
    { k: t('trade.opening_acquisition'), v: t('trade.opening_unknown'), tone: 'amber' },
    { k: t('trade.opening_source'), v: value.provenienza },
    ...(value.nome ? [{ k: t('trade.opening_name'), v: value.nome }] : []),
    ...(value.nota ? [{ k: t('trade.opening_note'), v: value.nota }] : []),
  ];
  const receiptRecord = viewed ?? receipt?.opening;
  const w = parole();
  return <TradeViewBoundary render={() => <div className="te-grid te-open" data-opening-entry>
    <Card titolo={w.openingTitle} className="te-openform" azioni={<span className="bbn-pill is-piatto">{w.openingPill}</span>}>
      <div className="te-body">
        <Nota tono="info">{w.openingIntro}</Nota>
        <form onSubmit={preview} className="te-body te-flush">
          <fieldset disabled={frozen} className="te-fieldset">
            <div className="te-group">
              <div className="te-group-title"><span className="te-n">1</span>{w.groupInstrument}</div>
              <div className="te-row2">
                <div className="te-field"><label htmlFor="f7-op-ticker">{t('trade.opening_ticker')}</label>
                  <div className="te-input"><input id="f7-op-ticker" autoComplete="off" value={draft.ticker} placeholder={w.openingTickerPh}
                    onChange={e => change('ticker', e.target.value)} /></div></div>
                <div className="te-field"><label htmlFor="f7-op-name">{t('trade.opening_name')}</label>
                  <div className="te-input"><input id="f7-op-name" value={draft.nome} placeholder={w.openingNamePh}
                    onChange={e => change('nome', e.target.value)} /></div></div>
              </div>
            </div>
            <div className="te-divider" />
            <div className="te-group">
              <div className="te-group-title"><span className="te-n">2</span>{w.groupBalance}</div>
              <div className="te-row2">
                <div className="te-field"><label htmlFor="f7-op-qty">{t('trade.opening_qty')}</label>
                  <div className="te-input"><input id="f7-op-qty" className="num" type="text" inputMode="decimal" autoComplete="off" value={draft.quantita}
                    title={t('numeri.grafia_richiesta', { esempio: (inputLanguages.quantita ?? language) === 'it' ? '1.234,56' : '1,234.56' })}
                    onChange={e => change('quantita', e.target.value)} /></div></div>
                <div className="te-field"><label htmlFor="f7-op-cost">{t('trade.opening_cost')}</label>
                  <div className="te-input"><input id="f7-op-cost" className="num" type="text" inputMode="decimal" autoComplete="off" value={draft.prezzo_medio}
                    title={t('numeri.grafia_richiesta', { esempio: (inputLanguages.prezzo_medio ?? language) === 'it' ? '1.234,56' : '1,234.56' })}
                    onChange={e => change('prezzo_medio', e.target.value)} /><span className="te-suf">{draft.valuta}</span></div></div>
              </div>
              <div className="te-field"><label htmlFor="f7-op-currency">{t('trade.opening_currency')}</label>
                <div className="te-input"><select id="f7-op-currency" value={draft.valuta} onChange={e => change('valuta', e.target.value)}>
                  {CURRENCIES.map(currency => <option key={currency}>{currency}</option>)}
                </select></div>
                <div className="te-help">{t('trade.opening_zero')}</div></div>
            </div>
            <div className="te-divider" />
            <div className="te-group">
              <div className="te-group-title"><span className="te-n">3</span>{w.groupDocs}</div>
              <div className="te-row2">
                <div className="te-field"><label htmlFor="f7-op-day">{t('trade.opening_date')}</label>
                  <div className="te-input"><input id="f7-op-day" type="date" min="2000-01-01" max={oggiISO()} value={draft.giorno}
                    onChange={e => change('giorno', e.target.value)} /></div></div>
                <div className="te-field"><label htmlFor="f7-op-precision">{t('trade.opening_precision')}</label>
                  <div className="te-input"><select id="f7-op-precision" value={draft.precisione}
                    onChange={e => change('precisione', e.target.value as OpeningDraft['precisione'])}>
                    <option value="day">{w.precisionDay}</option><option value="second">{w.precisionSecond}</option>
                  </select></div></div>
              </div>
              {draft.precisione === 'second' && <div className="te-field"><label htmlFor="f7-op-time">{t('trade.opening_time')}</label>
                <div className="te-input"><input id="f7-op-time" type="time" step="1" value={draft.ora}
                  onChange={e => change('ora', e.target.value)} /></div></div>}
              <div className="te-field"><label htmlFor="f7-op-source">{t('trade.opening_source')}</label>
                <div className="te-input"><FileText size={16} className="te-muted" aria-hidden="true" />
                  <input id="f7-op-source" value={draft.provenienza} placeholder={t('trade.opening_source_example')}
                    onChange={e => change('provenienza', e.target.value)} /></div></div>
              <div className="te-field"><label htmlFor="f7-op-note">{t('trade.opening_note')}</label>
                <textarea id="f7-op-note" className="te-ta" rows={2} value={draft.nota} onChange={e => change('nota', e.target.value)} /></div>
            </div>
          </fieldset>
          {validationAttempted && parsed.error && <Nota tono="bad" allerta>{parsed.error}</Nota>}
          <button type="submit" className="bbn-btn is-primary te-submit" disabled={frozen}>
            {busy ? <RefreshCw size={15} className="animate-spin" aria-hidden="true" /> : <Check size={15} aria-hidden="true" />}
            {busy === 'preview' ? w.checking : busy === 'write' ? w.writing : w.verifyBalance}</button>
        </form>
        <div ref={statusRef} tabIndex={-1} role="status" aria-live="polite" className="te-esito">
          {failure && <Nota tono={failure.kind === 'uncertain' ? 'unknown' : 'bad'} verbatim={noticeText(failure.detail, t)}>
            {failure.kind !== 'preview' && <b>{t(failure.kind === 'uncertain' ? 'trade.opening_uncertain' : 'trade.opening_rejected')}</b>}</Nota>}
          {receipt && <Nota tono="good">{t('trade.opening_saved')} · #{receipt.opening.id} · {receipt.opening.ticker}</Nota>}
          {receipt?.performance_note && <Nota tono="warn">{receipt.performance_note}</Nota>}
          {locked && <p className="te-help">{t('trade.opening_locked')}</p>}
        </div>
        {(sent || receipt) && <div className="te-actions">
          {sent && <button type="button" className="bbn-btn" onClick={() => void readTicker(sent.body.ticker)}>{w.readBack(sent.body.ticker)}</button>}
          {receipt && <button type="button" className="bbn-btn" onClick={() => {
            setDraft(emptyOpening()); setInputLanguages({}); setReceipt(null); setSent(null); setViewed(null); setReadback(null); setFailure(null); setValidationAttempted(false);
          }}>{w.nextBalance}</button>}
        </div>}
        {readback && <div role="status"><Nota tono={readback.ok ? 'good' : 'bad'}>
          {t(readback.ok ? 'trade.opening_readback_ok' : 'trade.opening_readback_error')}{readback.detail && ` · ${noticeText(readback.detail, t)}`}</Nota></div>}
      </div>
    </Card>

    <div className="te-col">
      <section className="bbn-card te-facts" aria-label={w.registers}>
        <div className="te-body">
          <div className="te-row2">
            <div className="te-fact is-yes"><h3><CheckCircle2 size={16} aria-hidden="true" /> {w.registers}</h3>
              <ul>{w.registersList.map(x => <li key={x}>{x}</li>)}</ul></div>
            <div className="te-fact is-no"><h3><XCircle size={16} aria-hidden="true" /> {w.notCreates}</h3>
              <ul>{w.notCreatesList.map(x => <li key={x}>{x}</li>)}</ul></div>
          </div>
          <Nota tono="warn" dettaglio={t('trade.opening_coverage')} riassunto={w.coverageMore}><b>{w.coverage}</b> {w.coverageShort}</Nota>
        </div>
      </section>
      <div className="te-open-pair">
        <Card titolo={w.openingRegister} className="te-stretch" conteggio={rows ? w.openingCount(rows.length) : undefined}
              azioni={<button type="button" className="bbn-icon-btn" data-qa="opening-refresh" disabled={reading} onClick={() => void refresh()}
                aria-label={t('trade.opening_refresh')} title={t('trade.opening_refresh')}>
                <RefreshCw size={14} className={reading ? 'animate-spin' : ''} aria-hidden="true" /></button>}>
          <div aria-busy={reading} className="te-reg">
            {reading && !rows && <p className="bbn-empty">{t('trade.opening_loading')}</p>}
            {listError && <div className="te-body"><Nota tono="bad">{t('trade.opening_read_error')}: {noticeText(listError, t)}</Nota></div>}
            {rows?.length === 0 && <div className="te-empty"><span className="te-empty-ico" aria-hidden="true"><FileText size={22} /></span>
              <b>{w.openingEmptyTitle}</b><span>{w.openingEmpty}</span><span className="te-sr">{t('trade.opening_empty')}</span></div>}
            {rows && rows.length > 0 && <ul className="te-rows">{rows.map(row => <li key={row.id}>
              <button type="button" className={'te-row is-compact' + (receiptRecord?.id === row.id ? ' is-on' : '')}
                      aria-label={`${t('trade.opening_view')} #${row.id} · ${row.ticker}`} onClick={() => void readTicker(row.ticker)}>
                <IconaTitolo ticker={row.ticker} nome={row.nome} dimensione="sm" />
                <span className="te-row-name"><b>{row.ticker}</b>
                  <span className="num">{format(row.quantita)} · {format(row.prezzo_medio)} {row.valuta} · {w.openingKnownAt(row.as_of.replace('T', ' '))}</span></span>
                <span className="te-muted num">#{row.id}</span>
              </button>
            </li>)}</ul>}
          </div>
        </Card>
        {receiptRecord && <Card titolo={w.receipt(receiptRecord.id)} className="te-receipt-card" data-opening-receipt
              azioni={viewed && readback?.ok ? <span className="bbn-pill is-su"><CheckCircle2 size={12} aria-hidden="true" /> {w.readBackOk}</span> : undefined}>
          <div className="te-body">
            <dl className="te-receipt">
              {[...balanceRows(receiptRecord), { k: t('trade.opening_created'), v: receiptRecord.created_at }].map(row =>
                <div key={row.k}><dt>{row.k}</dt><dd className={row.tone === 'amber' ? 'te-warn-t' : undefined}>{row.v}</dd></div>)}
            </dl>
          </div>
        </Card>}
      </div>
    </div>
    {pending && <ConfirmDialog open pagePresentation={true} variant="nuova" title={t('trade.opening_confirm_title')} intro={t('trade.opening_effects')}
      rows={[...balanceRows(pending.preview.opening),
        { k: t('trade.opening_cash_delta'), v: `${format(pending.preview.cash_delta_eur)} EUR` },
        { k: t('trade.opening_cash_after'), v: pending.preview.cash_disponibile_eur == null ? t('trade.opening_na') : `${format(pending.preview.cash_disponibile_eur)} EUR` }]}
      warn={t('trade.opening_frozen', { seconds: pending.preview.expires_in_seconds }) + ' ' + t('trade.opening_coverage')}
      confirmLabel={w.recordBalance} cancelLabel={w.cancel}
      onCancel={() => setPending(null)} onConfirm={() => void commit(pending)} />}
  </div>} />;
}

// ── pezzi di presentazione: senza hook, cosi' l'ordine degli hook del
//    controller non dipende da cosa e' a schermo (test i18n trade-draft) ──────

const TIPI_CERCABILI = ['EQUITY', 'ETF', 'CRYPTOCURRENCY', 'MUTUALFUND'];
const nomeTitolo = (p: Position) =>
  p.nome && p.nome !== p.ticker && p.nome.toLowerCase() !== 'none' ? p.nome : p.ticker.split('.')[0];

type Tono = 'bad' | 'warn' | 'good' | 'info' | 'unknown';
const ICONA_TONO = { bad: AlertOctagon, warn: AlertTriangle, good: CheckCircle2, info: Info, unknown: HelpCircle };

/** Avviso breve: una riga con icona e colore; la spiegazione lunga e la risposta
 *  letterale del backend restano raggiungibili, non tolte. */
function Nota({ tono, children, dettaglio, riassunto, verbatim, avviso, allerta, spinta }: {
  tono: Tono; children: ReactNode; dettaglio?: ReactNode; riassunto?: string; verbatim?: string;
  avviso?: boolean; allerta?: boolean; spinta?: boolean;
}) {
  const Icona = ICONA_TONO[tono];
  return <div className={`te-note is-${tono}` + (spinta ? ' te-push' : '')} role={allerta ? 'alert' : undefined}
              data-avviso={avviso ? '' : undefined}>
    <Icona size={16} aria-hidden="true" />
    <span className="te-note-txt">{children}
      {verbatim && <span className="te-verbatim">{verbatim}</span>}
      {dettaglio && <details><summary>{riassunto ?? parole().moreInfo}</summary><p>{dettaglio}</p></details>}
    </span>
  </div>;
}

/** Riquadro prima → dopo dell'anteprima. */
function Tessera({ k, v, sotto, vuota, attesa }: { k: string; v: ReactNode; sotto: ReactNode; vuota: boolean; attesa: string }) {
  return <div className={'te-tile' + (vuota ? ' is-ghost' : '')}>
    <span className="te-tile-k">{k}</span>
    <span className="te-tile-v num">{vuota ? '—' : v}</span>
    <span className="te-tile-s">{vuota ? attesa : sotto}</span>
  </div>;
}

/** Le taglie degli ordini passati in ordine crescente, con l'ordine in corso in evidenza. */
function Istogramma({ valori, mio, mediana, etichetta }: {
  valori: number[]; mio: number | null; mediana: number | null; etichetta: string;
}) {
  const barre = valori.filter(v => isFinite(v) && v > 0).map(v => ({ v, mio: false }));
  if (mio != null && isFinite(mio) && mio > 0) barre.push({ v: mio, mio: true });
  if (!barre.length) return null;
  barre.sort((a, b) => a.v - b.v);
  const tetto = Math.max(...barre.map(b => b.v));
  const vicinoMediana = mediana == null ? -1 : barre.reduce((best, b, i) =>
    !b.mio && (best < 0 || Math.abs(b.v - mediana) < Math.abs(barre[best].v - mediana)) ? i : best, -1);
  return <div className="te-hist" role="img" aria-label={etichetta}>
    {barre.slice(-40).map((b, i) => <span key={i}
      className={b.mio ? 'is-me' : i === vicinoMediana ? 'is-med' : undefined}
      style={{ height: `${Math.max(8, (b.v / tetto) * 100)}%` }} />)}
  </div>;
}

/** Flussi cumulati a gradini, per data valuta, dalla riga più vecchia visibile. */
function GraficoFlussi({ movimenti, flussi, etichetta, oggi }: {
  movimenti: MovimentoCassa[]; flussi: Map<number, number | null>;
  etichetta: (n: number, last: string) => string; oggi: string;
}) {
  const punti = movimenti.slice().reverse()
    .map(m => ({ d: m.date, v: flussi.get(m.id) }))
    .filter((p): p is { d: string; v: number } => typeof p.v === 'number' && isFinite(p.v) && !!p.d);
  if (!punti.length) return null;
  const W = 800, H = 170, top = 14, bottom = 136;
  const t0 = Date.parse(punti[0].d), t1 = Math.max(Date.now(), Date.parse(punti[punti.length - 1].d) + 86400000);
  const span = Math.max(1, t1 - t0);
  const vmin = Math.min(0, ...punti.map(p => p.v)), vmax = Math.max(1, ...punti.map(p => p.v));
  const x = (d: string) => ((Date.parse(d) - t0) / span) * W;
  const y = (v: number) => bottom - ((v - vmin) / (vmax - vmin)) * (bottom - top);
  let path = `M0 ${y(punti[0].v).toFixed(1)}`;
  punti.slice(1).forEach(p => { path += ` H${x(p.d).toFixed(1)} V${y(p.v).toFixed(1)}`; });
  path += ` H${W}`;
  const ultimo = punti[punti.length - 1];
  const tacche = [vmax, (vmax + vmin) / 2, vmin];
  return <svg className="te-flows-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
              aria-label={etichetta(punti.length, eur(ultimo.v, 0))}>
    <defs><linearGradient id="te-flows-fill" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stopColor="var(--bbn-good)" stopOpacity=".26" /><stop offset="1" stopColor="var(--bbn-good)" stopOpacity="0" />
    </linearGradient></defs>
    {tacche.map((v, i) => <line key={i} x1="0" x2={W} y1={y(v)} y2={y(v)} className="te-flows-grid" />)}
    <path d={`${path} V${y(vmin)} H0 Z`} fill="url(#te-flows-fill)" />
    <path d={path} className="te-flows-line" vectorEffect="non-scaling-stroke" />
    <text x="0" y={H - 4} className="te-flows-t">{gg(punti[0].d)}</text>
    <text x={W} y={H - 4} className="te-flows-t" textAnchor="end">{oggi}</text>
    <text x={W} y={y(vmax) - 4} className="te-flows-t" textAnchor="end">{eur(vmax, 0)}</text>
  </svg>;
}
