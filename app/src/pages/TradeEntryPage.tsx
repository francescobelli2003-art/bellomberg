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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Bellomberg } from '@/lib/api';
import type { Decision, MovimentoCassa, PortfolioSnapshot, Position } from '@/lib/api';
import { portfolioValues } from '@/lib/portfolio-values';
import { congelaAnteprima, dataTrade, decisioneCompatibile, legameTrade, FrontendTradeError } from '@/lib/trade-entry';
import type { TradeRequest, TradePreview, TradeResult } from '@/lib/trade-entry';
import { preparaPosizioneIniziale, congelaPosizioneIniziale, leggiRicevutaPosizione, leggiPosizioniIniziali } from '@/lib/position-opening';
import type { OpeningDraft, OpeningRecord, OpeningResult } from '@/lib/position-opening';
import { useLingua, useT } from '@/i18n/provider';
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
import { RefreshCw, Save, AlertOctagon, CheckCircle2, AlertTriangle, HelpCircle } from 'lucide-react';
import './trade-cassa.css';

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

export default function TradeEntryPage() {
  const t = useT();
  const [params] = useSearchParams();
  const initialOpening = params.get('mode') === 'opening';
  const [mode, setMode] = useState<'trade' | 'opening'>(initialOpening ? 'opening' : 'trade');
  const [visited, setVisited] = useState({ trade: !initialOpening, opening: initialOpening });
  const selectedStyle = { background: 'rgba(41,211,242,.10)', color: 'var(--cy)', boxShadow: 'inset 0 -2px 0 0 var(--cy)' };
  const choose = (value: 'trade' | 'opening') => {
    setVisited(v => ({ ...v, [value]: true }));
    setMode(value);
  };
  return <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', gap: 9 }}>
    <div className="f7c" style={{ height: 'auto' }}>
      <div className="verbi" role="group" aria-label={t('trade.mode')} style={{ width: 320 }}>
        <button type="button" id="f7-mode-trade" aria-pressed={mode === 'trade'} style={mode === 'trade' ? selectedStyle : undefined} onClick={() => choose('trade')}>{t('trade.operation')}</button>
        <button type="button" id="f7-mode-opening" aria-pressed={mode === 'opening'} style={mode === 'opening' ? selectedStyle : undefined} onClick={() => choose('opening')}>{t('trade.opening')}</button>
      </div>
    </div>
    <div style={{ flex: 1, minHeight: 0, display: mode === 'trade' ? 'block' : 'none' }} aria-hidden={mode !== 'trade'}>
      {visited.trade && <TradeOperationEntry />}
    </div>
    <div style={{ flex: 1, minHeight: 0, display: mode === 'opening' ? 'block' : 'none' }} aria-hidden={mode !== 'opening'}>
      {visited.opening && <PositionOpeningEntry />}
    </div>
  </div>;
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

function TradeOperationEntry() {
  const tr = useT(), language = useLingua();
  const [searchParams] = useSearchParams();
  const decisionFromRoute = searchParams.get('decision');
  const routeConsumed = useRef<string | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [decisionsErr, setDecisionsErr] = useState<string | null>(null);
  const [selectedDecision, setSelectedDecision] = useState(decisionFromRoute || 'none');
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
  const mvGemellaRef = useRef<HTMLDivElement>(null);

  const [taccaHover, setTaccaHover] = useState<number | null>(null);
  const [taccaFuoco, setTaccaFuoco] = useState<number | null>(null);
  const [taccaSel, setTaccaSel] = useState(0);
  const tacchePista = useRef<HTMLDivElement>(null);
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
    } else {
      setDecisions([]);
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

  const fuoriScala = useMemo(
    () => (scala == null ? 0 : taglie.misurate.filter(t => (t.eur as number) > scala).length),
    [taglie, scala]);
  const ultimi = useMemo(
    () => taglie.taglie.slice()
      .sort((a, b) => (a.data < b.data ? 1 : a.data > b.data ? -1 : 0))
      .slice(0, ULTIMI_IN_LISTA),
    [taglie]);

  // roving tabindex: l'indice deve restare dentro la lista, altrimenti nessuna
  // tacca ha tabIndex=0 e il gruppo esce dall'ordine di tabulazione
  useEffect(() => {
    if (taccaSel > taglie.misurate.length - 1) setTaccaSel(Math.max(0, taglie.misurate.length - 1));
  }, [taglie.misurate.length, taccaSel]);
  const taccaMostrata = taccaHover ?? taccaFuoco;

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
      const body: TradeRequest = {
        ticker: tickerUp, action, quantita: qtyN, prezzo: priceN, valuta,
        note: note || undefined, pm_rationale: rationale || undefined,
        data: dataTrade(tradeDay, tradeTime, oggiISO()),
        ...legameTrade(selectedDecision, decisions, tickerUp, action),
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

  const taccheKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const n = taglie.misurate.length;
    if (!n) return;
    let next = taccaSel;
    if (e.key === 'ArrowRight') next = Math.min(n - 1, taccaSel + 1);
    else if (e.key === 'ArrowLeft') next = Math.max(0, taccaSel - 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = n - 1;
    else return;
    e.preventDefault();
    setTaccaSel(next);
    (tacchePista.current?.querySelectorAll('button.tc')[next] as HTMLButtonElement | undefined)
      ?.focus();
  };

  return (
    <div className="f7c">

      {/* ══ COLONNA SINISTRA ══════════════════════════════════════════ */}
      <div className="colonna sx">

        <div className="rq">
          <span className="sq tl" /><span className="sq br" />
          <div className="ph a">
            <h1>{tr('trade.ticket_title')}</h1>
            <span className="side">{tr('trade.ticket_subtitle')}</span>
          </div>
          <div className="pb" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

              <div className="verbi" role="radiogroup" aria-label={tr('trade.trade_type')}
                   onKeyDown={verbiKeyDown}>
                {ACTIONS.map(a => (
                  <button key={a.v} type="button" role="radio" aria-checked={a.v === action}
                          aria-label={a.label} title={a.label}
                          tabIndex={a.v === action ? 0 : -1}
                          onClick={() => cambiaVerbo(a.v)}>{a.v}</button>
                ))}
              </div>

              <div className="campi">
                <div className="campo largo">
                  <label htmlFor="f7-tk">Ticker</label>
                  <div className="box">
                    <input id="f7-tk" value={ticker} placeholder={tr('trade.exact_ticker')}
                           autoComplete="off"
                           onChange={e => setTicker(e.target.value.toUpperCase())} />
                    <span className="suf">
                      {bookAssente ? '' : pos ? 'IN BOOK' : tickerUp ? tr('trade.new') : ''}
                    </span>
                  </div>
                  <div className={'aiuto' + (bookAssente && tickerUp ? ' male' : '')}>
                    {bookAssente
                      ? (tickerUp ? tr('trade.book_not_loaded') : ' ')
                      : pos
                        ? tr('trade.holding_cost', {a: exact(pos.quantita), b: num(pos.prezzo_medio), c: pos.valuta})
                        : tickerUp ? tr('trade.not_active') : ' '}
                  </div>
                </div>

                {/* ⚠ type="text" e non "number": su type=number il browser CANCELLA
                    il separatore decimale non della sua locale — `158,50` diventa
                    `15850`, senza badInput. Misurato sull'app viva. */}
                <div className="campo">
                  <label htmlFor="f7-qt">{action === 'DIVIDEND' ? tr('trade.shares') : tr('trade.quantity')}</label>
                  <div className={'box' + (letturaQty && !letturaQty.ok ? ' rotto' : '')}>
                    <input id="f7-qt" className="num" type="text" inputMode="decimal"
                           title={tr('numeri.grafia_richiesta', { esempio: qtyLanguage === 'it' ? '1.234,56' : '1,234.56' })}
                           autoComplete="off" value={qty}
                           placeholder={action === 'DIVIDEND' ? tr('trade.example_shares') : tr('trade.example_qty')}
                           aria-invalid={!!(letturaQty && !letturaQty.ok)}
                           onChange={e => setQty(e.target.value)} />
                    <span className="suf">{tr('trade.shares_upper')}</span>
                  </div>
                  {letturaQty && !letturaQty.ok && (
                    <div className="aiuto male">{letturaQty.motivo}</div>
                  )}
                </div>

                <div className="campo">
                  <label htmlFor="f7-pz">
                    {action === 'DIVIDEND' ? tr('trade.euro_per_share') : tr('trade.price')}
                  </label>
                  <div className={'box' + (letturaPrice && !letturaPrice.ok ? ' rotto' : '')}>
                    <input id="f7-pz" className="num" type="text" inputMode="decimal"
                           title={tr('numeri.grafia_richiesta', { esempio: priceLanguage === 'it' ? '1.234,56' : '1,234.56' })}
                           autoComplete="off" value={price}
                           placeholder={action === 'DIVIDEND' ? tr('trade.example_dividend') : tr('trade.example_price')}
                           aria-invalid={!!(letturaPrice && !letturaPrice.ok)}
                           onChange={e => setPrice(e.target.value)} />
                    <span className="suf">{action === 'DIVIDEND' ? 'EUR' : valuta}</span>
                  </div>
                  {letturaPrice && !letturaPrice.ok ? (
                    <div className="aiuto male">{letturaPrice.motivo}</div>
                  ) : pos && pos.prezzo_live != null && action !== 'DIVIDEND' ? (
                    <div className="aiuto">
                      live {num(pos.prezzo_live)} {pos.valuta}
                      {ordineValido && tr('trade.distance_live', {a: segno((priceN / pos.prezzo_live - 1) * 100)})}
                    </div>
                  ) : <div className="aiuto">&nbsp;</div>}
                </div>

                <div className="campo largo">
                  <label htmlFor="f7-vl">{tr('trade.currency')}</label>
                  <div className="box">
                    <select id="f7-vl" value={valuta} disabled={action === 'DIVIDEND'}
                            onChange={e => setValuta(e.target.value)}>
                      {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <span className="suf">
                      {action === 'DIVIDEND' ? tr('trade.dividends_euro')
                        : cambio.fonte === 'assente' ? tr('trade.fx_absent_upper')
                          : `1 ${valuta} = ${num(cambio.tasso, 6)} EUR`}
                    </span>
                  </div>
                </div>
              </div>

              {/* ── LA CATENA DEL CAMBIO ── */}
              <div className="campi">
                <div className="campo">
                  <label htmlFor="f7-data">{tr('trade.optional_date')}</label>
                  <input id="f7-data" className="testo" type="date" min="2000-01-01" max={oggiISO()}
                    value={tradeDay} onChange={e => setTradeDay(e.target.value)} />
                  <div className="aiuto">{tr('trade.date_help')}</div>
                </div>
                <div className="campo">
                  <label htmlFor="f7-ora">{tr('trade.optional_time')}</label>
                  <input id="f7-ora" className="testo" type="time" step="1"
                    value={tradeTime} onChange={e => setTradeTime(e.target.value)} />
                </div>
              </div>
              <div className="campo">
                <label htmlFor="f7-decisione">{tr('trade.decision_link')}</label>
                <div className="box">
                  <select id="f7-decisione" value={selectedDecision}
                    onChange={e => setSelectedDecision(e.target.value)}>
                    <option value="none">{tr('trade.manual_no_decision')}</option>
                    <option value="unknown">{tr('trade.unknown_link')}</option>
                    {!['none', 'unknown'].includes(selectedDecision)
                      && !decisions.some(d => String(d.id) === selectedDecision && decisioneCompatibile(d, tickerUp, action))
                      && <option value={selectedDecision}>#{selectedDecision} {tr('trade.incompatible_option')}</option>}
                    {decisions.filter(d => decisioneCompatibile(d, tickerUp, action)).map(d => (
                      <option key={d.id} value={String(d.id)}>#{d.id} · {d.action} {d.ticker} · {d.timestamp.slice(0, 10)} · {d.status}
                        {d.esecuzione ? tr('trade.executed_amount', {a: eur(d.esecuzione.eur), b: d.esecuzione.inferito ? tr('trade.inferred') : ''}) : ''}</option>
                    ))}
                  </select>
                </div>
                <div className={'aiuto' + (decisionsErr ? ' male' : '')}>
                  {decisionsErr ? tr('trade.decisions_unavailable', {a: decisionsErr})
                    : tr('trade.decision_help')}
                </div>
              </div>
              {tradeDay && <div className="avv giallo"><span>{tr('trade.dated_trade_help')}</span></div>}
              <div className="catena">
                <div className="oggi">
                  {sim.valido
                    ? <>{tr('trade.estimate_current')}{' '}
                        <b>{num(conv.locale)} {valuta}</b></>
                    : tr('trade.write_to_estimate')}
                </div>
                {sim.valido && (
                  <>
                    <div className="anelli">
                      <span className="an num">{exact(qtyN)}</span>
                      <span className="op">×</span>
                      <span className="an num">{num(priceN)} {valuta}</span>
                      <span className="op">=</span>
                      <span className="an num">{num(conv.locale)} {valuta}</span>
                      {conv.eur != null ? (
                        <>
                          <span className="op">×</span>
                          <span className="fx num">{num(cambio.tasso, 6)}</span>
                          <span className="op">=</span>
                          <span className="out num">{eur(conv.eur)}</span>
                        </>
                      ) : (
                        <span className="rotta">
                          × <b>{tr('trade.fx_word')} {valuta}{tr('trade.eur_unavailable')}</b>{tr('trade.eur_not_calculable_here')}
                        </span>
                      )}
                    </div>
                    <span className="src">
                      {cambio.fonte === 'nativa' && tr('trade.order_in_euros')}
                      {cambio.fonte === 'portafoglio' && <>{tr('trade.portfolio_fx_help')}</>}
                      {cambio.fonte === 'fx' && <>{tr('trade.service_fx_help')}</>}
                      {cambio.fonte === 'assente' && <>{tr('trade.no_fx_source')} {valuta}{tr('trade.no_fx_payload')}
                        {ratesErr ? ` (${ratesErr})` : ''}. <b>{tr('trade.no_invented_fx')}</b></>}
                    </span>
                  </>
                )}
              </div>

              <div className="campo">
                <label htmlFor="f7-rz">{tr('trade.why_trade')}</label>
                <textarea id="f7-rz" rows={2} value={rationale}
                          placeholder={tr('trade.rationale_example')}
                          onChange={e => setRationale(e.target.value)} />
              </div>
              <div className="campo">
                <label htmlFor="f7-nt">{tr('trade.note')}</label>
                <input id="f7-nt" className="testo" value={note} placeholder={tr('trade.free_text')}
                       onChange={e => setNote(e.target.value)} />
              </div>

              {/* ── AVVISI ── */}
              {discorde && (
                <div className="avv giallo"><span className="ic">▲</span>
                  <span><b>{tr('trade.currency_differs')}</b> {tickerUp} {tr('trade.quoted_in')} {discorde.valutaBook}{tr('trade.you_chose')} {discorde.valutaScelta}
                    {discorde.fattoreCento && <> — <b>{tr('trade.factor_hundred')}</b></>}{tr('trade.check_price')}</span></div>
              )}
              {sim.caricoMuto && sim.caricoMuto !== 'ordine-incompleto' && (
                <div className="avv viola"><span className="ic">⊘</span>
                  <span><b>{tr('trade.cost_after_unknown')}</b>
                    {' — '}{perche(sim.caricoMuto,
                      discorde ? [discorde.valutaBook, discorde.valutaScelta] : undefined)}.
                    {sim.caricoMuto === 'valuta-discorde' && <> {tr('trade.no_mixed_currency_average')}</>}</span></div>
              )}
              {sfora && (
                <div className="avv rosso"><span className="ic">▲</span>
                  <span><b>{tr('trade.not_covered_shortfall')} {eur(sim.mancano)}.</b> {tr('trade.already_executed_intro')} <b>{tr('trade.already_executed')}</b> {tr('trade.at_broker')}</span></div>
              )}
              {!sfora && sim.valido && sim.cassaDopo != null && (
                <div className="avv calmo"><span className="ic">≡</span>
                  <span>
                    {entra ? <>{tr('trade.covered_remaining')} <b>{eur(sim.cassaDopo)}</b></>
                      : <>{tr('trade.the_cash')} <b>{tr('trade.rises')}</b> {tr('trade.to')} <b>{eur(sim.cassaDopo)}</b></>}
                    {volte != null && <> {tr('trade.order_multiple')} <b>{num(volte, 1)}×</b> {tr('trade.median_size')}</>}.</span></div>
              )}
              {sim.valido && entra && sim.copre === null && (
                <div className="avv viola"><span className="ic">⊘</span>
                  <span><b>{tr('trade.capacity_unknown')}</b> — {cassa == null
                    ? tr('trade.cash_missing_payload')
                    : tr('trade.euro_value_unknown')}.</span></div>
              )}
              {avviso && (
                <div className="avv rosso"><span className="ic"><AlertOctagon size={12} /></span>
                  <span>{noticeText(avviso, tr)}</span></div>
              )}

              <button type="submit" disabled={submitting || preparing}
                      className={'spara' + (sfora ? ' sfora' : '')}>
                {submitting ? <RefreshCw size={11} className="animate-spin" /> : <Save size={11} />}
                {submitting ? tr('trade.writing') : preparing ? tr('trade.checking')
                  : tr('trade.check_confirm', {a: action})}
              </button>
            </form>

            {/* ── L'ESITO: annunciato, e raggiungibile dal fuoco ── */}
            <div ref={esitoRef} tabIndex={-1} role="status" aria-live="polite"
                 style={{ outline: 'none' }}>
              {erroreScrittura && (
                <div className="avv rosso"><span className="ic"><AlertOctagon size={12} /></span>
                  <span><b>{tr('trade.trade_not_written')}</b> {tr('trade.backend_rejected')}<span className="verbatim">{erroreScrittura}</span></span></div>
              )}
              {esitoIgnoto && (
                <div className="avv viola"><span className="ic"><HelpCircle size={12} /></span>
                  <span><b>{tr('trade.unknown_outcome')}</b> {tr('trade.request_sent_no_confirmation')} <b>{tr('trade.may_be_written')}</b>{tr('trade.check_movements')} <b>{tr('trade.before')}</b> {tr('trade.retry_duplicate_warning')}<span className="verbatim">{esitoIgnoto}</span></span></div>
              )}
              {esito && !esito.riconosciuta && (
                <div className="avv viola"><span className="ic"><HelpCircle size={12} /></span>
                  <span><b>{tr('trade.unrecognized_response')}</b>{tr('trade.has_neither')} <code>ok</code> {tr('trade.nor')}
                    <code> trade_id</code>{tr('trade.cannot_assert_trade')}</span></div>
              )}
              {esito && esito.riconosciuta && esito.stato === 'aggiornata' && (
                <div className="avv verde"><span className="ic"><CheckCircle2 size={12} /></span>
                  <span>Trade <b>#{esito.tradeId}</b> {tr('trade.recorded_available_cash')} <b>{eur(esito.cassa)}</b>.</span></div>
              )}
              {esito && esito.riconosciuta && esito.stato === 'aggiornata-con-nota' && (
                <div className="avv giallo"><span className="ic"><AlertTriangle size={12} /></span>
                  <span>Trade <b>#{esito.tradeId}</b> {tr('trade.recorded_cash')}
                    <b> {eur(esito.cassa)}</b>, <b>{tr('trade.backend_note_attached')}</b>:
                    <span className="verbatim">« {esito.nota} »</span></span></div>
              )}
              {esito && esito.riconosciuta && esito.stato === 'non-aggiornata' && (
                <div className="avv giallo"><span className="ic"><AlertTriangle size={12} /></span>
                  <span>Trade <b>#{esito.tradeId}</b> {tr('trade.recorded_comma')} <b>{tr('trade.cash_not_updated')}</b>{tr('trade.backend_says_dot')}
                    <span className="verbatim">« {esito.nota} »</span></span></div>
              )}
              {esito && esito.riconosciuta && esito.stato === 'muta' && (
                <div className="avv giallo"><span className="ic"><AlertTriangle size={12} /></span>
                  <span>Trade <b>#{esito.tradeId}</b> {tr('trade.recorded_comma')} <b>{tr('trade.cash_update_not_shown')}</b> {tr('trade.cash_missing_reason')}</span></div>
              )}
              {/* GUARDIA PREZZI: ortogonale allo stato cassa — la scrittura è
                  passata, ma il prezzo è lontano dall'ultimo riferimento (fra
                  ±30% e ×3). Senza questo blocco il ramo avviso della specifica
                  del PM era muto (ponte NUOVO 01/08, changelog (61)). */}
              {esito && esito.riconosciuta && esito.guardia && (
                <div className="avv giallo"><span className="ic"><AlertTriangle size={12} /></span>
                  <span><b>{tr('trade.price_guard')}</b> {tr('trade.price_guard_explanation')}
                    <span className="verbatim">« {esito.guardia} »</span></span></div>
              )}
              {tradeResult?.data && <div className="avv calmo"><span>
                {tr('trade.date_prefix')} <b>{tradeResult.data.replace('T', ' ')}</b>
                {tradeResult.ora_convenzionale && tr('trade.conventional_time')}.
                {' '}{tr('trade.link_prefix')} {tradeResult.link_origin === 'explicit' ? tr('trade.decision_id', {a: tradeResult.decisione?.id ?? tr('trade.na')})
                  : tradeResult.link_origin === 'none' ? tr('trade.manual_link') : tr('trade.not_declared')}.
                {tradeResult.fx && <> {tr('trade.fx_prefix')} {exact(tradeResult.fx.tasso)} · {tr(tradeResult.fx.fonte === 'identity' ? 'trade.already_euros' : tradeResult.fx.fonte === 'storico' ? 'trade.historical' : 'trade.current_not_historical')}.
                  {' '}{tradeResult.fx.nota}</>}
                {tradeResult.ricalcolo && <> {tr('trade.successive_checked')} {tradeResult.ricalcolo.trade_successivi.length}.
                  {' '}{tradeResult.ricalcolo.note?.join(' ')}</>}
              </span></div>}
              {tradeResult?.performance_note && <div className="avv giallo"><span>
                {tr('trade.trade_recorded')} {tradeResult.performance_note}
              </span></div>}
            </div>
          </div>
        </div>

        {/* ── IL LIBRETTO DELLA CASSA (F37) ──────────────────────────────
            Impianto C, scelto dal PM 20/08 sulla rosa IN SITU
            (mockup_f7_cassa/F7cassa_C_dettaglio.png, contro A "il sesto
            verbo" e B "il gesto sul binario"). La domanda a cui risponde:
            versare è scrivere una riga di registro — e le righe già scritte
            stanno SOTTO il modulo, così la terna duplicata si vede mentre la
            si digita invece di scoprirla dal 422 a POST già partita. */}
        <div className="rq lb-rq">
          <span className="sq tl" /><span className="sq br" />
          <div className="ph a">
            <h2>{tr('trade.cashbook_title')}</h2>
            {/* ⚠ «N movimenti a registro» era falso in due stati: prima della
                prima lettura (N=0 su un registro mai letto) e quando N è il
                TETTO chiesto, non il totale — che questa pagina non ha. */}
            <span className="side">
              {!movLetto ? tr('trade.register_loading')
                : movErr ? tr('trade.register_unread')
                  : finestraPiena
                    ? tr('trade.at_least_movements', {a: movimenti.length, b: cassa != null ? eur(cassa) : tr('trade.na')})
                    : `${movimenti.length} ${movimenti.length === 1 ? tr('trade.one_movement_shown') : tr('trade.many_movements_shown')}`
                      + tr('trade.cash_fragment', {a: cassa != null ? eur(cassa) : tr('trade.na')})}
            </span>
          </div>
          <div className="pb lb">
            <form onSubmit={inviaMovimento}>
              <div className="verbi cassa" role="radiogroup"
                   aria-label={tr('trade.deposit_or_withdraw')} onKeyDown={mvKeyDown}>
                {([['DEPOSIT', tr('trade.deposit_verb')], ['WITHDRAWAL', tr('trade.withdraw_verb')]] as const).map(([t, l]) => (
                  <button key={t} type="button" role="radio" aria-checked={t === mvTipo}
                          tabIndex={t === mvTipo ? 0 : -1}
                          onClick={() => {
                            setMvTipo(t);
                            setMvRifiuto(null); setMvEsito(null); setMvAvviso(null);
                          }}>{l}</button>
                ))}
              </div>

              <div className="campi">
                {/* ⚠ type="text" e non "number", come tutto il resto di F7:
                    `12.345,67` in en-GB diventerebbe un altro numero. */}
                {/* ⚠ ogni onChange BUTTA il rifiuto pendente: v. `scordaRifiuto` */}
                <div className="campo largo">
                  <label htmlFor="f7-mv-imp">{tr('trade.amount')}</label>
                  <div className={'box' + (letturaImporto && !letturaImporto.ok ? ' rotto' : '')}>
                    <input id="f7-mv-imp" ref={mvImportoRef} className="num" type="text"
                           title={tr('numeri.grafia_richiesta', { esempio: cashLanguage === 'it' ? '1.234,56' : '1,234.56' })}
                           inputMode="decimal"
                           autoComplete="off" value={mvImporto} placeholder={tr('trade.amount_example')}
                           aria-invalid={!!(letturaImporto && !letturaImporto.ok)}
                           onChange={e => { setMvImporto(e.target.value); scordaRifiuto(); }} />
                    <span className="suf">EUR</span>
                  </div>
                  <div className={'aiuto'
                    + ((letturaImporto && !letturaImporto.ok) || prelievoScoperto ? ' male' : '')}>
                    {letturaImporto && !letturaImporto.ok ? letturaImporto.motivo
                      : prelievoScoperto
                        ? tr('trade.withdrawal_refused', {a: eur(cassa)})
                        : previsioneCredibile
                          ? tr('trade.cash_prediction', {a: eur(cassa), b: eur(cassaDopoMovimento)})
                          : cassa == null ? tr('trade.cash_capacity_unavailable')
                            : ' '}
                  </div>
                </div>

                <div className="campo">
                  <label htmlFor="f7-mv-dt">{tr('trade.value_date')}</label>
                  <div className={'box' + (letturaData && !letturaData.ok ? ' rotto' : '')}>
                    <input id="f7-mv-dt" className="num" type="text" inputMode="numeric"
                           autoComplete="off" value={mvData} placeholder={mvOggi}
                           aria-invalid={!!(letturaData && !letturaData.ok)}
                           onChange={e => {
                             mvDataAuto.current = false;
                             setMvData(e.target.value); scordaRifiuto();
                           }} />
                    <span className="suf">ISO</span>
                  </div>
                  <div className={'aiuto' + (letturaData && !letturaData.ok ? ' male' : '')}>
                    {letturaData && !letturaData.ok ? letturaData.motivo
                      : tr('trade.flow_date_help')}
                  </div>
                </div>

                <div className="campo">
                  <label htmlFor="f7-mv-nt">{tr('trade.description')}</label>
                  <div className="box">
                    <input id="f7-mv-nt" type="text" autoComplete="off" value={mvNota}
                           placeholder={tr('trade.bank_transfer_example')}
                           onChange={e => { setMvNota(e.target.value); scordaRifiuto(); }} />
                  </div>
                  {/* «verbatim» era falso: la causale passa da .trim() e una
                      fatta di soli spazi non viene spedita affatto. */}
                  <div className="aiuto">{tr('trade.stored_in')} <code>note</code>{tr('trade.trimmed_edges')}</div>
                </div>
              </div>

              {mvAvviso && (
                <div className="avv rosso"><span className="ic">✕</span>
                  <span>{noticeText(mvAvviso, tr)}</span></div>
              )}

              {/* L'ANTICIPO DELLA GUARDIA: la riga gemella esiste già. Non
                  blocca — la guardia vera è del backend — ma toglie la
                  sorpresa, che è il motivo per cui questo impianto esiste. */}
              {gemella && !mvRifiuto && (
                <div className="avv giallo"><span className="ic">⚠</span>
                  <span><b>{tr('trade.row_exists')}</b> (id={gemella.id}{tr('trade.duplicate_row_explanation')}</span></div>
              )}

              {/* `disabled` sul solo invio in volo: dal momento che ogni
                  modifica ai campi butta il rifiuto, REGISTRA e CONFERMO non
                  possono più essere entrambi vivi sullo stesso corpo. */}
              <button type="submit" className="spara"
                      disabled={mvInvio || !!mvRifiuto}>
                <Save size={13} />
                {mvInvio ? tr('trade.sending')
                  : mvTipo === 'DEPOSIT' ? tr('trade.record_deposit') : tr('trade.record_withdrawal')}
              </button>
            </form>

            {/* ── L'ESITO ────────────────────────────────────────────────
                ⚠ La live region è l'annunciatore INVISIBILE qui sotto, non il
                blocco visibile: un `aria-live` che entra nell'albero INSIEME
                al testo non viene annunciato (e con `:empty{display:none}` non
                ci entrava affatto). Così, in più, i BOTTONI non stanno dentro
                una region che alcuni lettori rileggono a ogni mutazione. */}
            <div className="lb-annuncio" role="status" aria-live="polite">
              {mvRifiuto ? tr('trade.movement_rejected_reason', {a: mvRifiuto.rifiuto.motivo})
                : mvIgnoto ? tr('trade.server_no_response')
                  : mvEsito ? (mvEsito.riconosciuta
                    ? tr('trade.movement_recorded_id', {a: mvEsito.movimentoId})
                    : tr('trade.unrecognized_response_dot')) : ''}
            </div>
            <div className="lb-esito">
              {mvRifiuto && (
                <>
                  <div className="avv giallo">
                    <span className="ic"><AlertTriangle size={12} /></span>
                    <span>
                      <b>{mvRifiuto.rifiuto.quale === 'duplicato'
                        ? tr('trade.duplicate_registered')
                        : mvRifiuto.rifiuto.quale === 'soglia'
                        /* la cifra NON si ricopia: `CASH_SOGLIA_CONFERMA_EUR`
                           è policy modificabile (memory_db.py:906) e vive in un
                           posto solo. Il numero vero lo porta il verbatim. */
                          ? tr('trade.threshold_confirmation')
                          : tr('trade.movement_rejected')}</b>{' '}
                      {/* ⚠ «NON è stato scritto» solo dove il backend lo
                          GARANTISCE (422/401). Su un 500 dopo l'INSERT o su un
                          503 post-commit affermarlo manda il PM a riprovare,
                          il secondo giro prende GUARDIA DUPLICATO, e il
                          bottone qui sotto — che quella guardia la spegne — fa
                          entrare il doppio movimento. */}
                      {mvRifiuto.rifiuto.nonScritto
                        ? <>{tr('trade.the_movement')} <b>{tr('trade.not_upper')}</b> {tr('trade.was_recorded')}</>
                        : <>{tr('trade.backend_responded')} <b>HTTP {mvRifiuto.rifiuto.status}</b>:{' '}
                          <b>{tr('trade.cannot_say')}</b> {tr('trade.check_register_before_retry')}</>}
                      {' '}{tr('trade.backend_says')}
                      <span className="verbatim">« {mvRifiuto.rifiuto.motivo} »</span>
                    </span>
                  </div>
                  {mvRifiuto.rifiuto.rimediabile && (
                    <>
                      {/* ANNULLA prende il fuoco e ha la STESSA larghezza:
                          l'INVIO accidentale non deve scavalcare due guardie. */}
                      <div className="lb-conf">
                        <button type="button" className="spara" onClick={confermaMovimento}
                                disabled={mvInvio}>
                          {mvInvio ? tr('trade.sending') : tr('trade.confirm_intentional')}
                        </button>
                        <button type="button" className="lb-ann" ref={mvAnnullaRef}
                                onClick={() => {
                                  setMvRifiuto(null);
                                  mvImportoRef.current?.focus();
                                }} disabled={mvInvio}>
                          {tr('trade.cancel_caps')}
                        </button>
                      </div>
                      {/* La chiave è UNA per DUE guardie (memory_db.py:958 e
                          :964): chi conferma deve sapere che spegne anche
                          l'altra. Dirlo è la resa decisa dal PM il 20/08. */}
                      <div className="gate">
                        {tr('trade.confirm_resends')} <b>{tr('trade.same')}</b> {tr('trade.movement_with')}{' '}
                        <code>conferma=true</code>{tr('trade.disables')} <b>{tr('trade.both')}</b> {tr('trade.overridable_checks')} <b>{tr('trade.and')}</b> {tr('trade.amount_guard_too')}
                      </div>
                    </>
                  )}
                </>
              )}

              {mvIgnoto && (
                <div className="avv viola"><span className="ic"><HelpCircle size={12} /></span>
                  <span><b>{tr('trade.unknown_outcome_short')}</b>{tr('trade.server_no_reply_so')}{' '}
                    <b>{tr('trade.do_not_know')}</b> {tr('trade.whether_movement_written')} <b>{tr('trade.reading_again')}</b>{' '}
                    {tr('trade.register_check_row')}
                    <span className="verbatim">{mvIgnoto}</span></span></div>
              )}

              {mvEsito && (
                mvEsito.riconosciuta ? (
                  <div className={'avv ' + (mvEsito.stato === 'aggiornata' ? 'verde'
                    : mvEsito.stato === 'muta' ? 'viola' : 'giallo')}>
                    <span className="ic">
                      {mvEsito.stato === 'aggiornata' ? <CheckCircle2 size={12} />
                        : mvEsito.stato === 'muta' ? <HelpCircle size={12} />
                          : <AlertTriangle size={12} />}</span>
                    <span>
                      <b>{tr('trade.movement')} {mvEsito.movimentoId} {tr('trade.recorded_dot')}</b>{' '}
                      {/* i quattro stati hanno quattro rami: prima
                          `aggiornata-con-nota` cadeva in quello che afferma
                          l'OPPOSTO («la cassa non è stata aggiornata») */}
                      {mvEsito.stato === 'aggiornata'
                        ? <>{tr('trade.cash_updated')} <b>{eur(mvEsito.cassa)}</b>.</>
                        : mvEsito.stato === 'aggiornata-con-nota'
                          ? <>{tr('trade.cash_updated_to')} <b>{eur(mvEsito.cassa)}</b>{tr('trade.with_backend_note')}</>
                          : mvEsito.stato === 'muta'
                            ? <>{tr('trade.the_response')} <b>{tr('trade.no_cash_in_response')}</b>{tr('trade.register_written_value_unknown')}</>
                            : <>{tr('trade.the_cash')} <b>{tr('trade.not')}</b> {tr('trade.cash_register_diverge')} <b>{tr('trade.diverge')}</b>.</>}
                      {mvEsito.nota && (
                        <span className="verbatim">« {mvEsito.nota} »</span>
                      )}
                    </span>
                  </div>
                ) : (
                  <div className="avv viola"><span className="ic"><HelpCircle size={12} /></span>
                    <span><b>{tr('trade.unrecognized_response')}</b>{tr('trade.has_neither')} <code>ok</code> {tr('trade.nor')}{' '}
                      <code>movement_id</code>{tr('trade.cannot_assert_movement')}</span></div>
                )
              )}
            </div>

            {/* ── LE RIGHE GIÀ SCRITTE ── */}
            <div className="lbm">
              {!movLetto ? (
                <div className="vuoto">{tr('trade.reading_register')}</div>
              ) : movErr ? (
                <div className="vuoto">{tr('trade.register_unread_prefix')} <b>{movErr}</b>{tr('trade.unread_register_help')}</div>
              ) : movimenti.length === 0 ? (
                <div className="vuoto">{tr('trade.no_cash_movements')}</div>
              ) : (
                movimenti.map(m => {
                  const dupe = gemella != null && m.id === gemella.id;
                  const cum = flussi.get(m.id);
                  const val = typeof m.amount_eur === 'number' && isFinite(m.amount_eur)
                    ? m.amount_eur : null;
                  const verso = m.type === 'DEPOSIT' ? '+' : '−';
                  // ⚠ l'importo di riga è reso a 0 decimali per far stare la
                  // colonna: 1.234,49 € si legge «+1.234 €». Il valore ESATTO
                  // dev'essere raggiungibile da qualche parte, se no la pagina
                  // rende un numero che non sta nel registro — e `gemella`
                  // confronta i centesimi, quindi due righe diverse di un
                  // centesimo si vedono identiche. Qui sta nel title e
                  // nell'aria-label, insieme all'anno che `gg()` non rende.
                  const esatto = val == null ? tr('trade.amount_na')
                    : `${m.date} · ${m.type === 'DEPOSIT' ? tr('trade.deposit_lower') : tr('trade.withdrawal_lower')}`
                      + ` ${verso}${num(Math.abs(val))} €`;
                  return (
                    <div className={'uo' + (dupe ? ' qui' : '')} key={m.id}
                         ref={dupe ? mvGemellaRef : undefined}
                         aria-label={esatto + (m.note ? ` — ${m.note}` : '')}
                         title={esatto + (m.note ? `\n${m.note}` : '')}>
                      <span className="dt num">{gg(m.date)}</span>
                      <span className="tk">
                        {m.type === 'DEPOSIT' ? tr('trade.deposit_upper') : tr('trade.withdrawal_upper')}</span>
                      <span className="ba">
                        {movimentoMax > 0 && val != null && (
                          <i className={dupe ? 'q' : undefined}
                             style={{ width: pct(Math.abs(val) / movimentoMax) }} />
                        )}
                      </span>
                      <span className="ev num">
                        {val == null ? <span className="nd">{tr('trade.amount_na')}</span>
                          : <>{verso}{eur(Math.abs(val), 0)}</>}
                      </span>
                      <span className="ev num fl">
                        {cum != null ? tr('trade.flows_value', {a: eur(cum, 0)}) : tr('trade.flows_na')}
                      </span>
                    </div>
                  );
                })
              )}
            </div>
            {/* ⚠ Le due note stanno FUORI da `.lbm`. Quella della finestra ci
                stava DENTRO: con 200 righe finiva a 6.211px di scroll dentro un
                vano da 149px — una dichiarazione che nessuno avrebbe letto,
                sopra numeri che senza di lei sono falsi. */}
            {movLetto && !movErr && finestraPiena && (
              <div className="gate">
                {tr('trade.rows_shown')} <b>{movimenti.length}</b>{tr('trade.window_at_limit')} <b>{tr('trade.cannot_see_older')}</b>{tr('trade.flows_partial')}
              </div>
            )}
            {/* La colonna dice «flussi» e non «saldo» perché il saldo cassa NON
                è ricostruibile da qui: anche i trade la muovono. Sulla rosa
                quella colonna dava una cifra incompatibile col versamento. */}
            <div className="gate">
              <b>{tr('trade.flows')}</b> {tr('trade.cumulative_deposit_withdrawal')} <b>{tr('trade.shown_below')}</b>{tr('trade.flows_not_cash')}
            </div>
          </div>
        </div>

        {/* ── PRIMA → DOPO ── */}
        <div className="rq">
          <div className="ph c">
            <h2>{tr('trade.before_after')}</h2>
            <span className="side">
              {sim.valutazione === 'eseguito-proxy' ? tr('trade.valued_entered_price')
                : tr('trade.live_book_simulation')}
            </span>
          </div>
          <div className="pb">
            {!sim.valido ? (
              <div className="t-no" style={{ fontSize: 12 }}>
                {tr('trade.simulation_needs_inputs')}
              </div>
            ) : (
              <>
                <div className="pd">
                  <Riga k={tr('trade.cash')} a={eur(sim.cassaPrima)} b={eur(sim.cassaDopo)}
                        muto={sim.cassaDopo == null}
                        dl={sim.cassaDopo != null && sim.cassaPrima != null
                          ? segno(sim.cassaDopo - sim.cassaPrima) + ' €' : tr('trade.na')}
                        verso={entra ? 1 : -1} grosso sfora={sfora} />
                  <Riga k={tr('trade.ticker_shares', {a: tickerUp})}
                        a={sim.qtaPrima != null ? exact(sim.qtaPrima) : tr('trade.na')}
                        b={sim.qtaMuta ? '—' : exact(sim.qtaDopo)}
                        muto={!!sim.qtaMuta}
                        dl={sim.qtaMuta ? perche(sim.qtaMuta)
                          : (sim.qtaDopo != null && sim.qtaPrima != null
                            ? (sim.qtaDopo - sim.qtaPrima > 0 ? '+' : '') + exact(sim.qtaDopo - sim.qtaPrima)
                            : tr('trade.na'))}
                        verso={0} />
                  <Riga k={tr('trade.cost_basis')}
                        a={sim.caricoPrima != null ? `${num(sim.caricoPrima)} ${pos?.valuta || ''}` : tr('trade.na')}
                        b={sim.caricoMuto ? '—' : `${num(sim.caricoDopo)} ${pos?.valuta || valuta}`}
                        muto={!!sim.caricoMuto}
                        dl={sim.caricoMuto
                          ? perche(sim.caricoMuto,
                            discorde ? [discorde.valutaBook, discorde.valutaScelta] : undefined)
                          : sim.caricoInvariato ? tr('trade.unchanged')
                            : (sim.caricoDopo != null && sim.caricoPrima != null
                              ? segno(sim.caricoDopo - sim.caricoPrima) : tr('trade.new_cost'))}
                        verso={sim.caricoDopo != null && sim.caricoPrima != null
                          ? Math.sign(sim.caricoDopo - sim.caricoPrima) : 0} />
                  <Riga k={tr('trade.securities_weight')}
                        a={sim.pesoPrima != null ? num(sim.pesoPrima) + '%' : tr('trade.na')}
                        b={sim.pesoMuto ? '—' : num(sim.pesoDopo) + '%'}
                        muto={!!sim.pesoMuto}
                        dl={sim.pesoMuto ? perche(sim.pesoMuto)
                          : (sim.pesoDopo != null && sim.pesoPrima != null
                            ? segno(sim.pesoDopo - sim.pesoPrima) + ' pt' : tr('trade.na'))}
                        verso={0} />
                  <Riga k="NAV" a={eur(sim.navPrima, 0)}
                        b={sim.navMuto ? '—' : eur(sim.navDopo, 0)}
                        muto={!!sim.navMuto}
                        dl={sim.navMuto ? perche(sim.navMuto)
                          : (sim.navDelta != null
                            ? (Math.abs(sim.navDelta) < 0.005 ? tr('trade.unchanged') : segno(sim.navDelta) + ' €')
                            : tr('trade.na'))}
                        verso={sim.navDelta != null ? -Math.sign(sim.navDelta) : 0} />
                </div>
                <div className="avv calmo" style={{ marginTop: 8 }}>
                  <span className="ic">≡</span>
                  <span>
                    {tr('trade.this_is_a')} <b>{tr('trade.simulation')}</b>{tr('trade.cash_moves_entered')} <b>{tr('trade.entered')}</b>{tr('trade.securities_valued')}{' '}
                    {sim.valutazione === 'mercato' ? <>{tr('trade.at')} <b>{tr('trade.market_price')}</b></>
                      : sim.valutazione === 'eseguito-proxy'
                        ? <>{tr('trade.at')} <b>{tr('trade.entered_price')}</b>{tr('trade.new_name_no_market_price')}</>
                        : <>{tr('trade.no_price_valuation')}</>}.
                    {' '}{tr('trade.nav_difference')} <b>{tr('trade.our_arithmetic')}</b>{tr('trade.backend_usually_matches')}
                  </span>
                </div>
              </>
            )}
          </div>
        </div>

        {/* ── GLI ULTIMI ORDINI ── */}
        <div className="rq cresce">
          <div className="ph">
            <h2>{tr('trade.recent_orders')}</h2>
            <span className="side">{tr('trade.same_scale')}</span>
          </div>
          <div className="pb scorre nopad">
            {tradesErr ? (
              <div className="vuoto">{tr('trade.history_unavailable_prefix')} <b>{tradesErr}</b>{tr('trade.history_comparison_missing')}</div>
            ) : ultimi.length === 0 ? (
              <div className="vuoto">{tr('trade.no_archived_movements')}</div>
            ) : (
              <>
                {sim.valido && (
                  <div className="uo qui">
                    <span className="dt">{tr('trade.now')}</span>
                    <span className="tk">{tickerUp}</span>
                    <span className="ba">
                      {delta != null && scala != null && (
                        <i className="q" style={{ width: pct(delta / scala) }} />
                      )}
                    </span>
                    <span className="ev num">{eur(conv.eur, 0)}</span>
                  </div>
                )}
                {ultimi.map((t, i) => (
                  <div className="uo" key={`${t.data}-${t.ticker}-${i}`}>
                    <span className="dt num">{gg(t.data)}</span>
                    <span className="tk">{t.ticker}</span>
                    <span className="ba" title={`${num(t.locale)} ${t.valuta}`}>
                      {t.eur != null && scala != null && (
                        <i style={{ width: pct(t.eur / scala) }} />
                      )}
                    </span>
                    <span className="ev num">
                      {t.eur != null ? eur(t.eur, 0)
                        : <span className="nd">{tr('trade.fx_word')} {t.valuta} {tr('trade.na')}</span>}
                    </span>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      </div>

      {/* ══ COLONNA DESTRA ════════════════════════════════════════════ */}
      <div className="colonna dx">

        {/* ── IL BINARIO DELLA CASSA: l'oggetto-firma ── */}
        <div className="rq" style={{ flex: '0 0 214px' }}>
          <span className="sq tl" /><span className="sq br" />
          <div className="ph a">
            <h2>{tr('trade.cash_title')}</h2>
            <span className="side">
              {bookAssente ? tr('trade.portfolio_not_loaded')
                : cassa != null ? tr('trade.cash_source_field', {a: eur(cassa)})
                  : tr('trade.cash_field_absent')}
            </span>
            <button type="button" className="mini" onClick={carica} aria-busy={loading}>
              <RefreshCw size={10} className={loading ? 'animate-spin' : ''} /> {tr('trade.refresh')}
            </button>
          </div>
          <div className="pb">
            {posErr ? (
              <div className="vuoto">
                <span><b>{tr('trade.portfolio_unavailable')}</b> — {posErr}.<br />
                  {tr('trade.cashbar_missing_book')}</span>
              </div>
            ) : cassa == null ? (
              <div className="vuoto">
                <span><b>{tr('trade.cash_payload_absent')}</b> — {portfolioValues(snap).note}.{' '}
                  {tr('trade.missing_nonnumeric_capacity')} <b>{tr('trade.cannot_verify')}</b> {tr('trade.empty_bar_looks_zero')}</span>
              </div>
            ) : cassa <= 0 ? (
              <div className="vuoto">
                <span><b>{cassa === 0 ? tr('trade.zero_cash') : tr('trade.overdraft', {a: eur(-cassa)})}</b>
                  {' '}{tr('trade.is_a_measurement')} <b>{tr('trade.measurement')}</b>{tr('trade.zero_bar_explanation')}</span>
              </div>
            ) : scala != null ? (
              <>
                <div className={'binario' + (sfora ? ' sfora' : '') + (pezzo && !entra ? ' entrata' : '')}>
                  {pezzo && (
                    <>
                      <div className="morso"
                           style={{ left: pct(pezzo.da), width: pct(pezzo.largo) }} />
                      <div className="etm">
                        {sfora ? tr('trade.not_covered') : entra ? tr('trade.order_outflow') : tr('trade.order_inflow')}
                        <b className="num">{entra ? '−' : '+'}{eur(delta)}</b>
                      </div>
                    </>
                  )}
                  <div className="resto">
                    <span className="num">
                      {sfora && sim.mancano != null
                        ? <>{tr('trade.shortfall')} {eur(sim.mancano)}</>
                        : <>{eur(pezzo ? sim.cassaDopo : cassa)}{' '}
                          <span className="t-no" style={{ fontSize: 10 }}>
                            {pezzo ? (entra ? tr('trade.remaining_upper') : tr('trade.cash_after_upper')) : tr('trade.cash_upper')}</span></>}
                    </span>
                  </div>
                </div>

                <div className="tacche" ref={tacchePista} onKeyDown={taccheKeyDown}
                     role="group" aria-label={tr('trade.order_sizes_label')}>
                  {taglie.misurate.map((t, i) => (
                    <button key={`${t.data}-${t.ticker}-${i}`} type="button" className="tc"
                            tabIndex={i === taccaSel ? 0 : -1}
                            style={{ left: pct((t.eur as number) / scala) }}
                            aria-label={tr('trade.order_date_fragment', {a: t.ticker, b: t.action, c: gg(t.data)})
                              + tr('trade.equivalent_eur_fragment', {a: num(t.locale), b: t.valuta, c: eur(t.eur)})}
                            onMouseEnter={() => setTaccaHover(i)}
                            onMouseLeave={() => setTaccaHover(null)}
                            onFocus={() => { setTaccaFuoco(i); setTaccaSel(i); }}
                            onBlur={() => setTaccaFuoco(null)} />
                  ))}
                  {taglie.mediana != null && (
                    <>
                      <span className="rif" style={{ left: pct(taglie.mediana / scala) }} />
                      <span className="lb" style={{ left: pct(taglie.mediana / scala) }}>
                        {tr('trade.median_upper')} {num(taglie.mediana, 0)} €
                      </span>
                    </>
                  )}
                  {taglie.massimo != null && taglie.massimo <= scala && (
                    <>
                      <span className="rif" style={{ left: pct(taglie.massimo / scala) }} />
                      <span className="lb" style={{ left: pct(taglie.massimo / scala) }}>
                        {tr('trade.your_maximum')} {num(taglie.massimo, 0)} €
                      </span>
                    </>
                  )}
                  {taccaMostrata != null && taglie.misurate[taccaMostrata] && (() => {
                    const t = taglie.misurate[taccaMostrata];
                    const f = (t.eur as number) / scala;
                    const lato = f < 0.16 ? 'sx' : f > 0.84 ? 'dx' : 'cx';
                    return (
                      <div className={'tip ' + lato} role="tooltip"
                           style={{ left: pct(f) }}>
                        <b>{t.ticker} · {t.action}</b>
                        <div className="kv"><span>{tr('trade.when')}</span>
                          <span className="num">{t.data.slice(0, 10)}</span></div>
                        <div className="kv"><span>{tr('trade.value_lower')}</span>
                          <span className="num">{num(t.locale)} {t.valuta}</span></div>
                        <div className="kv"><span>{tr('trade.in_euros')}</span>
                          <span className="num">{eur(t.eur)}</span></div>
                      </div>
                    );
                  })()}
                </div>

                <div className="leg">
                  <i><span className={'sw mo' + (pezzo && !entra ? ' in' : '')} />
                    {entra ? tr('trade.outflow_size') : tr('trade.inflow_size')}
                    {delta != null && <> — <b>{num((delta / scala) * 100, 1)}%</b> {tr('trade.of_scale')}</>}</i>
                  <i><span className="sw tc" />{tr('trade.your')} <b>{taglie.misurate.length}</b> {tr('trade.historic_orders_current_fx')}</i>
                  <i><span className="sw md" />{tr('trade.median_maximum')}</i>
                  <i>{tr('trade.full_width')} {entra || !pezzo ? tr('trade.cash_now')
                    : tr('trade.cash_after_order')}<span className="t-et">: {eur(scala)}</span></i>
                  {fuoriScala > 0 && (
                    <i className="gate"><b>{fuoriScala}</b> {tr('trade.orders_beyond_scale')}</i>
                  )}
                  {taglie.senzaCambio > 0 && (
                    <i className="gate"><b>{taglie.senzaCambio}</b> {tr('trade.orders_no_marker')} {taglie.scoperte.join(', ')}</i>
                  )}
                  {tradesErr && <i className="gate">{tr('trade.history_no_markers')}</i>}
                </div>
              </>
            ) : null}
          </div>
        </div>

        {/* ── IL BOOK ── */}
        <div className="rq cresce">
          <div className="ph">
            <h2>{tr('trade.book_title')}</h2>
            <span className="side">
              {bookAssente ? tr('trade.unavailable')
                : tr('trade.sorted_value', {a: book.fuoriPosto, b: book.righe.length})
                  + tr('trade.different_order')}
              {book.senzaValore > 0 && tr('trade.missing_value_at_end', {a: book.senzaValore})}
            </span>
          </div>
          <div className="pb scorre nopad">
            <table>
              <thead>
                <tr>
                  <th>Ticker</th><th>{tr('trade.currency_short')}</th><th className="r">{tr('trade.quantity_short')}</th>
                  <th className="r">{tr('trade.cost_short')}</th><th className="r">Live</th>
                  <th className="r">{tr('trade.value')}</th><th className="r">{tr('trade.weight')}</th>
                  <th className="r">P&amp;L</th><th className="r">{tr('trade.rank_change')}</th>
                </tr>
              </thead>
              <tbody>
                {posErr ? (
                  <tr><td colSpan={9} className="d" style={{ textAlign: 'center', padding: 22 }}>
                    {tr('trade.positions_unavailable_upper')} {posErr}{tr('trade.book_error_refresh')}
                  </td></tr>
                ) : loading && book.righe.length === 0 ? (
                  <tr><td colSpan={9} style={{ textAlign: 'center', padding: 22 }}>
                    {tr('trade.positions_loading')}</td></tr>
                ) : book.righe.length === 0 ? (
                  <tr><td colSpan={9} style={{ textAlign: 'center', padding: 22 }}>
                    {tr('trade.no_position_buy')}</td></tr>
                ) : book.righe.map(r => (
                  <tr key={r.pos.ticker}
                      className={r.pos.ticker.toUpperCase() === tickerUp ? 'mira' : undefined}>
                    <td className="d">
                      {/* un comando VERO: prima era la riga a rispondere a INVIO e
                          SPAZIO, e uno spazio battuto per scorrere sovrascriveva
                          in silenzio il modulo che stavi compilando */}
                      <button type="button" className="usa"
                              aria-label={tr('trade.use_ticker', {a: r.pos.ticker})}
                              onClick={() => selectPosition(r.pos)}>{r.pos.ticker}</button>
                    </td>
                    <td className="num">{r.pos.valuta}</td>
                    <td className="r num">{exact(r.pos.quantita)}</td>
                    <td className="r num">{num(r.pos.prezzo_medio)}</td>
                    <td className="r num">{r.pos.prezzo_live != null ? num(r.pos.prezzo_live) : tr('trade.na')}</td>
                    <td className="r num d">{eur(r.pos.valore_mercato, 0)}</td>
                    <td className="r num">{r.peso != null ? num(r.peso) + '%' : tr('trade.na')}</td>
                    <td className="r num">
                      {r.pos.pl_pct == null ? tr('trade.na') : (
                        <span className={r.pos.pl_pct > 0 ? 'pos' : r.pos.pl_pct < 0 ? 'neg' : 'pari'}>
                          {segno(r.pos.pl_pct)}%
                        </span>
                      )}
                    </td>
                    <td className="r num">
                      <span className={r.scarto === 0 ? 'zero' : r.scarto > 0 ? 'su' : 'giu'}>
                        {r.scarto === 0 ? '·' : segno(r.scarto, 0)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {pending && (
        <ConfirmDialog
          open
          tone={pending.preview.cash_disponibile_eur < 0 || pending.discorde ? 'crimson' : 'amber'}
          title={tr('trade.confirm_trade_title')}
          intro={tr('trade.confirm_trade_intro')}
          rows={pendingRows(pending)}
          warn={(pending.preview.cash_disponibile_eur < 0
            ? tr('trade.confirm_shortfall', {a: eur(pending.preview.cash_disponibile_eur)}) : '')
            + tr('trade.preview_expiry', {a: pending.preview.expires_in_seconds})
            + tr('trade.confirm_register_change')
            + tr('trade.confirm_atomic_no_undo')}
          confirmLabel={tr('trade.record_action', {a: pending.body.action})}
          cancelLabel={tr('trade.cancel')}
          onConfirm={() => { const p = pending; setPending(null); commit(p); }}
          onCancel={() => setPending(null)}
        />
      )}
    </div>
  );
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
  return <div className="f7c" data-opening-entry>
    <div className="colonna sx">
      <div className="rq">
        <div className="ph a"><h1>{t('trade.opening_title')}</h1><span className="side">{t('trade.opening_side')}</span></div>
        <div className="pb">
          <p className="gate">{t('trade.opening_intro')}</p>
          <form onSubmit={preview}>
            <fieldset disabled={frozen} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
              <div className="campi">
                <div className="campo"><label htmlFor="f7-op-ticker">{t('trade.opening_ticker')}</label>
                  <input id="f7-op-ticker" className="testo" autoComplete="off" value={draft.ticker} onChange={e => change('ticker', e.target.value)} /></div>
                <div className="campo"><label htmlFor="f7-op-name">{t('trade.opening_name')}</label>
                  <input id="f7-op-name" className="testo" value={draft.nome} onChange={e => change('nome', e.target.value)} /></div>
                <div className="campo"><label htmlFor="f7-op-qty">{t('trade.opening_qty')}</label>
                  <input id="f7-op-qty" className="testo num" type="text" inputMode="decimal" autoComplete="off" value={draft.quantita}
                    title={t('numeri.grafia_richiesta', { esempio: (inputLanguages.quantita ?? language) === 'it' ? '1.234,56' : '1,234.56' })}
                    onChange={e => change('quantita', e.target.value)} /></div>
                <div className="campo"><label htmlFor="f7-op-cost">{t('trade.opening_cost')}</label>
                  <input id="f7-op-cost" className="testo num" type="text" inputMode="decimal" autoComplete="off" value={draft.prezzo_medio}
                    title={t('numeri.grafia_richiesta', { esempio: (inputLanguages.prezzo_medio ?? language) === 'it' ? '1.234,56' : '1,234.56' })}
                    onChange={e => change('prezzo_medio', e.target.value)} /></div>
                <div className="campo"><label htmlFor="f7-op-currency">{t('trade.opening_currency')}</label>
                  <div className="box"><select id="f7-op-currency" value={draft.valuta} onChange={e => change('valuta', e.target.value)}>
                    {CURRENCIES.map(currency => <option key={currency}>{currency}</option>)}
                  </select></div></div>
                <div className="campo"><label htmlFor="f7-op-day">{t('trade.opening_date')}</label>
                  <input id="f7-op-day" className="testo" type="date" min="2000-01-01" max={oggiISO()} value={draft.giorno} onChange={e => change('giorno', e.target.value)} /></div>
                <div className="campo"><label htmlFor="f7-op-precision">{t('trade.opening_precision')}</label>
                  <div className="box"><select id="f7-op-precision" value={draft.precisione} onChange={e => change('precisione', e.target.value as OpeningDraft['precisione'])}>
                    <option value="day">{t('trade.opening_day')}</option><option value="second">{t('trade.opening_second')}</option>
                  </select></div></div>
                {draft.precisione === 'second' && <div className="campo"><label htmlFor="f7-op-time">{t('trade.opening_time')}</label>
                  <input id="f7-op-time" className="testo" type="time" step="1" value={draft.ora} onChange={e => change('ora', e.target.value)} /></div>}
              </div>
              <div className="campo"><label htmlFor="f7-op-source">{t('trade.opening_source')}</label>
                <input id="f7-op-source" className="testo" value={draft.provenienza} placeholder={t('trade.opening_source_example')} onChange={e => change('provenienza', e.target.value)} /></div>
              <div className="campo"><label htmlFor="f7-op-note">{t('trade.opening_note')}</label>
                <textarea id="f7-op-note" rows={2} value={draft.nota} onChange={e => change('nota', e.target.value)} /></div>
              <p className="aiuto">{t('trade.opening_zero')}</p>
            </fieldset>
            {validationAttempted && parsed.error && <div className="avv rosso" role="alert">{parsed.error}</div>}
            <button type="submit" className="spara" disabled={frozen}><Save size={12} />
              {t(busy === 'preview' ? 'trade.opening_checking' : busy === 'write' ? 'trade.opening_writing' : 'trade.opening_preview')}</button>
          </form>
          <div ref={statusRef} tabIndex={-1} role="status" aria-live="polite" style={{ outline: 'none' }}>
            {failure && <div className={'avv ' + (failure.kind === 'uncertain' ? 'viola' : 'rosso')}>
              <span>{failure.kind !== 'preview' && <b>{t(failure.kind === 'uncertain' ? 'trade.opening_uncertain' : 'trade.opening_rejected')}</b>}
                <span className="verbatim">{noticeText(failure.detail, t)}</span></span></div>}
            {receipt && <div className="avv verde"><span>{t('trade.opening_saved')} · #{receipt.opening.id} · {receipt.opening.ticker}</span></div>}
            {receipt?.performance_note && <div className="avv giallo"><span>{receipt.performance_note}</span></div>}
            {locked && <p className="gate">{t('trade.opening_locked')}</p>}
          </div>
          {sent && <button type="button" className="mini" onClick={() => void readTicker(sent.body.ticker)}>{t('trade.opening_readback')} · {sent.body.ticker}</button>}
          {readback && <div className={'avv ' + (readback.ok ? 'verde' : 'rosso')} role="status">
            {t(readback.ok ? 'trade.opening_readback_ok' : 'trade.opening_readback_error')}{readback.detail && ` · ${noticeText(readback.detail, t)}`}</div>}
          {receipt && <button type="button" className="mini" onClick={() => {
            setDraft(emptyOpening()); setInputLanguages({}); setReceipt(null); setSent(null); setViewed(null); setReadback(null); setFailure(null); setValidationAttempted(false);
          }}>{t('trade.opening_next')}</button>}
          <div className="avv calmo"><span>{t('trade.opening_effects')}</span></div>
          <div className="avv giallo"><span>{t('trade.opening_coverage')}</span></div>
        </div>
      </div>
    </div>
    <div className="colonna dx">
      {receiptRecord && <div className="rq" data-opening-receipt>
        <div className="ph c"><h2>{t('trade.opening_receipt')} #{receiptRecord.id}</h2></div>
        <div className="pb"><table><tbody>
          {[...balanceRows(receiptRecord), { k: t('trade.opening_created'), v: receiptRecord.created_at }].map(row =>
            <tr key={row.k}><th>{row.k}</th><td style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{row.v}</td></tr>)}
        </tbody></table><p className="gate">{t('trade.opening_coverage')}</p></div>
      </div>}
      <div className="rq cresce">
        <div className="ph"><h2>{t('trade.opening_register')}</h2>
          <button type="button" className="mini" disabled={reading} onClick={() => void refresh()}>{t('trade.opening_refresh')}</button></div>
        <div className="pb scorre" aria-busy={reading}>
          {reading && <p>{t('trade.opening_loading')}</p>}
          {listError && <div className="avv rosso">{t('trade.opening_read_error')}: {noticeText(listError, t)}</div>}
          {rows?.length === 0 && <p>{t('trade.opening_empty')}</p>}
          {rows?.map(row => <div className="rq" key={row.id}>
            <div className="ph"><span>{row.ticker} · {format(row.quantita)} {row.valuta}</span>
              <button type="button" className="mini" onClick={() => void readTicker(row.ticker)}>{t('trade.opening_view')} #{row.id}</button></div>
            <div className="pb">{t('trade.opening_date')}: {row.as_of.replace('T', ' ')}<br />{row.provenienza}</div>
          </div>)}
        </div>
      </div>
    </div>
    {pending && <ConfirmDialog open title={t('trade.opening_confirm_title')} intro={t('trade.opening_effects')}
      rows={[...balanceRows(pending.preview.opening),
        { k: t('trade.opening_cash_delta'), v: `${format(pending.preview.cash_delta_eur)} EUR` },
        { k: t('trade.opening_cash_after'), v: pending.preview.cash_disponibile_eur == null ? t('trade.opening_na') : `${format(pending.preview.cash_disponibile_eur)} EUR` }]}
      warn={t('trade.opening_frozen', { seconds: pending.preview.expires_in_seconds }) + ' ' + t('trade.opening_coverage')}
      confirmLabel={t('trade.opening_confirm')} cancelLabel={t('trade.cancel')}
      onCancel={() => setPending(null)} onConfirm={() => void commit(pending)} />}
  </div>;
}

// ── la riga di PRIMA → DOPO ─────────────────────────────────────────────────
function Riga(props: {
  k: string; a: string; b: string; dl: string; verso: number;
  grosso?: boolean; sfora?: boolean; muto?: boolean;
}) {
  const cls = props.muto || props.verso === 0 ? '' : props.verso > 0 ? 'su' : 'giu';
  return (
    <div className={'rg' + (props.grosso ? ' grosso' : '') + (props.sfora ? ' sfora' : '')}>
      <span className="k">{props.k}</span>
      <span className="a num">{props.a}</span>
      <span className="fr">→</span>
      <span className={'b num' + (props.muto ? ' muto' : '')}>{props.b}</span>
      <span className={'dl num ' + cls}>{props.dl}</span>
    </div>
  );
}
