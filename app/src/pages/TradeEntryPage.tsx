// F7 TRADE ENTRY "LA CASSA" — impianto scelto dal PM 27/07 sui PNG di
// mockup_f7_trade (opzione A, contro B "il precedente" e C "la forma del libro").
// Spec: docs/superpowers/specs/2026-07-27-f7-trade-entry-la-cassa-design.md
//
// LA DOMANDA A CUI RISPONDE LA PAGINA E' UNA SOLA: posso permettermelo, e cosa
// divento dopo? Prima leggeva `snap.positions` e buttava tutto il resto — 189
// valori resi su 478, e fra i buttati c'erano i 26.247 € di cassa che stanno
// nello stesso payload.
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
import { Bellomberg } from '@/lib/api';
import type { MovimentoCassa, PortfolioSnapshot, Position } from '@/lib/api';
import ConfirmDialog, { ConfirmRow } from '@/components/ConfirmDialog';
import {
  ENTRA, cambioPer, converti, simula, taglieStoriche, ordinaPerControvalore,
  esitoScrittura, scritturaRifiutata, valutaDiscorde, controMediana, leggiNumero,
  prezzoDaBook, perche, esitoMovimento, leggiRifiuto, leggiDataValuta,
} from '@/lib/cassa';
import type {
  Conversione, Discordanza, Esito, EsitoMovimento, Ordine, RifiutoCassa,
  Simulazione, TradeRiga, Verbo,
} from '@/lib/cassa';
import { RefreshCw, Save, AlertOctagon, CheckCircle2, AlertTriangle, HelpCircle } from 'lucide-react';
import './trade-cassa.css';

const ACTIONS: { v: Verbo; label: string }[] = [
  { v: 'BUY', label: 'BUY — nuova posizione o aumenta un\'esistente' },
  { v: 'ADD', label: 'ADD — aumenta una posizione esistente' },
  { v: 'TRIM', label: 'TRIM — riduce parzialmente' },
  { v: 'SELL', label: 'SELL — chiude o riduce' },
  { v: 'DIVIDEND', label: 'DIVIDEND — incasso in contanti, in euro per azione' },
];
const CURRENCIES = ['EUR', 'USD', 'GBP', 'GBX', 'CHF', 'JPY', 'HKD'];
const LIMITE_TRADES = 200;
const ULTIMI_IN_LISTA = 12;
// Il backend clampa a 1..1000. Serve come COSTANTE e non come letterale nella
// chiamata perche' va confrontata con le righe rese: se ne tornano esattamente
// LIMITE, quella che vedi e' una finestra e non lo storico — e va detto.
// (Stessa cura di MovementsPage:35, dove era una ALTA latente.)
const LIMITE_MOVIMENTI = 200;

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
  v == null || !isFinite(v) ? 'n.d.' : v.toLocaleString('it-IT', opz(dec, dec));
const eur = (v: number | null | undefined, dec = 2): string =>
  v == null || !isFinite(v) ? 'n.d.' : `${num(v, dec)} €`;
// quantita' frazionarie (cripto) e prezzi a 4 decimali (dividendi): mai
// arrotondare, si confermerebbe un numero diverso da quello inviato.
const exact = (v: number | null | undefined, dec = 8): string =>
  v == null || !isFinite(v) ? 'n.d.' : v.toLocaleString('it-IT', opz(0, dec));
const segno = (v: number | null | undefined, dec = 2): string =>
  v == null || !isFinite(v) ? 'n.d.' : (v > 0 ? '+' : '') + num(v, dec);
const gg = (iso: string): string =>
  (iso || '').length >= 10 ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}` : 'n.d.';
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

type TradeBody = {
  ticker: string; action: string; quantita: number; prezzo: number;
  valuta: string; note?: string; pm_rationale?: string;
};
/** ⚠ Si congela il corpo E i numeri derivati: prima «In euro» e «Cassa dopo»
 *  del dialog seguivano lo stato VIVO mentre le altre righe erano congelate,
 *  cioe' due numeri sulla stessa schermata con garanzie diverse. */
type Pending = {
  body: TradeBody; conv: Conversione; sim: Simulazione; discorde: Discordanza | null;
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
  const [snap, setSnap] = useState<PortfolioSnapshot | null>(null);
  const [posErr, setPosErr] = useState<string | null>(null);
  const [trades, setTrades] = useState<TradeRiga[]>([]);
  const [tradesErr, setTradesErr] = useState<string | null>(null);
  const [rates, setRates] = useState<Record<string, number> | null>(null);
  const [ratesErr, setRatesErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [ticker, setTicker] = useState('');
  const [action, setAction] = useState<Verbo>('BUY');
  const [qty, setQty] = useState('');
  const [price, setPrice] = useState('');
  const [valuta, setValuta] = useState('EUR');
  const [note, setNote] = useState('');
  const [rationale, setRationale] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [avviso, setAvviso] = useState<string | null>(null);
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
  const [mvImporto, setMvImporto] = useState('');
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
  const [mvAvviso, setMvAvviso] = useState<string | null>(null);
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
    const [p, t, f, m] = await Promise.allSettled([
      Bellomberg.portfolio(), Bellomberg.trades(LIMITE_TRADES), Bellomberg.fx(),
      Bellomberg.cashMovements(LIMITE_MOVIMENTI),
    ]);
    const testo = (x: unknown) => {
      const e = x as { response?: { data?: { detail?: string } }; message?: string };
      return e?.response?.data?.detail || e?.message || String(x);
    };

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
        setMovErr('risposta di forma inattesa: `movements` non è una lista');
      }
    } else { setMovimenti([]); setMovErr(testo(m.reason)); }
    setMovLetto(true);
    setLoading(false);
  }, []);

  useEffect(() => { carica(); }, [carica]);

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
  const letturaQty = useMemo(() => leggiNumero(qty), [qty]);
  const letturaPrice = useMemo(() => leggiNumero(price), [price]);
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
    setPrice(action === 'DIVIDEND' ? '' : prezzoDaBook(p));
    setQty('');
    setAvviso(null); setEsito(null); setErroreScrittura(null); setEsitoIgnoto(null);
  };

  // ── validazione: identica a prima (bugfix #164) ──────────────────────────
  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    setAvviso(null); setEsito(null); setErroreScrittura(null); setEsitoIgnoto(null);
    if (letturaQty && !letturaQty.ok) { setAvviso(`Quantità: ${letturaQty.motivo}`); return; }
    if (letturaPrice && !letturaPrice.ok) { setAvviso(`Prezzo: ${letturaPrice.motivo}`); return; }
    if (!tickerUp || !qty || !price) { setAvviso('Compila ticker, quantità, prezzo'); return; }
    if (!ordineValido) { setAvviso('Quantità e prezzo devono essere positivi'); return; }
    if (action !== 'BUY') {
      if (bookAssente) {
        setAvviso(`Posizioni non caricate${posErr ? ` (${posErr})` : ''}: impossibile validare `
          + `${action}. Usa AGGIORNA e riprova.`);
        return;
      }
      if (!pos) {
        setAvviso(`Ticker ${tickerUp} non in portfolio: ${action} rifiutato. `
          + 'Usa BUY per aprire una nuova posizione.');
        return;
      }
      if ((action === 'SELL' || action === 'TRIM') && qtyN > pos.quantita) {
        setAvviso(`Quantità ${exact(qtyN)} superiore a quella posseduta `
          + `(${exact(pos.quantita)} ${tickerUp})`);
        return;
      }
    }
    setPending({
      body: {
        ticker: tickerUp, action, quantita: qtyN, prezzo: priceN, valuta,
        note: note || undefined, pm_rationale: rationale || undefined,
      },
      conv, sim, discorde,
    });
  };

  const commit = async (p: Pending) => {
    setSubmitting(true);
    try {
      const r = await Bellomberg.logTrade(p.body);
      setEsito(esitoScrittura(r));
      setErroreScrittura(null); setEsitoIgnoto(null);
      await carica();
      if (p.body.action === 'BUY' || p.body.action === 'SELL') setTicker('');
      setQty(''); setNote(''); setRationale('');
    } catch (err) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string };
      const testo = e?.response?.data?.detail || e?.message || String(err);
      setEsito(null);
      // ⚠ una rejection prova il RIFIUTO solo se il server ha RISPOSTO. Su
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
      { k: 'Azione', v: b.action, tone: (b.action === 'SELL' || b.action === 'TRIM') ? 'crimson' : 'cyan' },
      { k: 'Ticker', v: b.ticker },
      { k: div ? 'Azioni' : 'Quantità', v: exact(b.quantita) },
      { k: div ? 'Dividendo/azione' : 'Prezzo', v: `${exact(b.prezzo)} ${b.valuta}` },
      { k: div ? 'Incasso' : 'Controvalore', v: `${num(b.quantita * b.prezzo)} ${b.valuta}`, tone: 'amber' },
    ];
    rows.push(p.conv.eur != null
      ? { k: 'In euro', v: eur(p.conv.eur), tone: 'amber' }
      : { k: 'In euro', v: `non calcolabile: cambio ${b.valuta}→EUR non disponibile`, tone: 'crimson' });
    rows.push(p.sim.cassaDopo != null
      ? { k: 'Cassa dopo', v: eur(p.sim.cassaDopo), tone: p.sim.copre === false ? 'crimson' : undefined }
      : { k: 'Cassa dopo', v: `n.d. — ${perche(p.sim.navMuto || 'book-assente')}` });
    // ⚠ i due avvisi che contano entravano nel form e NON nel dialog: l'ultima
    // schermata prima di una scrittura irreversibile era muta sull'errore che
    // costa di piu'.
    if (p.discorde) {
      rows.push({
        k: 'Valuta',
        v: `${b.ticker} in book è quotata in ${p.discorde.valutaBook}, stai scrivendo `
          + `${p.discorde.valutaScelta}` + (p.discorde.fattoreCento ? ' — differiscono per 100' : ''),
        tone: 'crimson',
      });
    }
    if (p.sim.caricoMuto === 'valuta-discorde') {
      rows.push({ k: 'Prezzo di carico dopo', v: 'non definito (sommerebbe due valute)', tone: 'crimson' });
    }
    if (b.pm_rationale) rows.push({ k: 'Motivo', v: b.pm_rationale });
    if (b.note) rows.push({ k: 'Nota', v: b.note });
    return rows;
  };

  // ── frecce sui verbi: roving tabindex, e l'indice parte da chi ha il fuoco ─
  // ══ IL LIBRETTO DELLA CASSA ═══════════════════════════════════════════
  const letturaImporto = useMemo(() => leggiNumero(mvImporto), [mvImporto]);
  const letturaData = useMemo(() => leggiDataValuta(mvData, mvOggi), [mvData, mvOggi]);
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
    if (!letturaImporto) { setMvAvviso('Scrivi l\'importo del movimento.'); return; }
    if (!letturaImporto.ok) { setMvAvviso(`Importo: ${letturaImporto.motivo}`); return; }
    if (letturaData && !letturaData.ok) {
      setMvAvviso(`Data valuta: ${letturaData.motivo}`); return;
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
            <h1>// il tagliando</h1>
            <span className="side">scrive nel portafoglio ufficiale</span>
          </div>
          <div className="pb" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

              <div className="verbi" role="radiogroup" aria-label="Tipo di operazione"
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
                    <input id="f7-tk" value={ticker} placeholder="Ticker esatto del broker"
                           autoComplete="off"
                           onChange={e => setTicker(e.target.value.toUpperCase())} />
                    <span className="suf">
                      {bookAssente ? '' : pos ? 'IN BOOK' : tickerUp ? 'NUOVO' : ''}
                    </span>
                  </div>
                  <div className={'aiuto' + (bookAssente && tickerUp ? ' male' : '')}>
                    {bookAssente
                      ? (tickerUp ? 'book non caricato: non posso dire se ce l\'hai già' : ' ')
                      : pos
                        ? `${exact(pos.quantita)} titoli a carico ${num(pos.prezzo_medio)} ${pos.valuta}`
                        : tickerUp ? 'non è fra le posizioni attive' : ' '}
                  </div>
                </div>

                {/* ⚠ type="text" e non "number": su type=number il browser CANCELLA
                    il separatore decimale non della sua locale — `158,50` diventa
                    `15850`, senza badInput. Misurato sull'app viva. */}
                <div className="campo">
                  <label htmlFor="f7-qt">{action === 'DIVIDEND' ? 'Azioni' : 'Quantità'}</label>
                  <div className={'box' + (letturaQty && !letturaQty.ok ? ' rotto' : '')}>
                    <input id="f7-qt" className="num" type="text" inputMode="decimal"
                           autoComplete="off" value={qty}
                           placeholder={action === 'DIVIDEND' ? 'es. 1900' : 'es. 10'}
                           aria-invalid={!!(letturaQty && !letturaQty.ok)}
                           onChange={e => setQty(e.target.value)} />
                    <span className="suf">TITOLI</span>
                  </div>
                  {letturaQty && !letturaQty.ok && (
                    <div className="aiuto male">{letturaQty.motivo}</div>
                  )}
                </div>

                <div className="campo">
                  <label htmlFor="f7-pz">
                    {action === 'DIVIDEND' ? 'EUR per azione' : 'Prezzo'}
                  </label>
                  <div className={'box' + (letturaPrice && !letturaPrice.ok ? ' rotto' : '')}>
                    <input id="f7-pz" className="num" type="text" inputMode="decimal"
                           autoComplete="off" value={price}
                           placeholder={action === 'DIVIDEND' ? 'es. 0,87' : 'es. 158,50'}
                           aria-invalid={!!(letturaPrice && !letturaPrice.ok)}
                           onChange={e => setPrice(e.target.value)} />
                    <span className="suf">{action === 'DIVIDEND' ? 'EUR' : valuta}</span>
                  </div>
                  {letturaPrice && !letturaPrice.ok ? (
                    <div className="aiuto male">{letturaPrice.motivo}</div>
                  ) : pos && pos.prezzo_live != null && action !== 'DIVIDEND' ? (
                    <div className="aiuto">
                      live {num(pos.prezzo_live)} {pos.valuta}
                      {ordineValido && ` · ${segno((priceN / pos.prezzo_live - 1) * 100)}% dal live`}
                    </div>
                  ) : <div className="aiuto">&nbsp;</div>}
                </div>

                <div className="campo largo">
                  <label htmlFor="f7-vl">Valuta</label>
                  <div className="box">
                    <select id="f7-vl" value={valuta} disabled={action === 'DIVIDEND'}
                            onChange={e => setValuta(e.target.value)}>
                      {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <span className="suf">
                      {action === 'DIVIDEND' ? 'EUR PER I DIVIDENDI'
                        : cambio.fonte === 'assente' ? 'CAMBIO ASSENTE'
                          : `1 ${valuta} = ${num(cambio.tasso, 6)} EUR`}
                    </span>
                  </div>
                </div>
              </div>

              {/* ── LA CATENA DEL CAMBIO ── */}
              <div className="catena">
                <div className="oggi">
                  {sim.valido
                    ? <>La conferma, prima, mostrava solo questo →{' '}
                        <b>{num(conv.locale)} {valuta}</b></>
                    : 'Scrivi quantità e prezzo: qui compare quanto costa davvero'}
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
                          × <b>cambio {valuta}→EUR non disponibile</b>: il controvalore in
                          euro non è calcolabile qui
                        </span>
                      )}
                    </div>
                    <span className="src">
                      {cambio.fonte === 'nativa' && 'L\'ordine è già in euro.'}
                      {cambio.fonte === 'portafoglio' && <>Cambio dal campo <b>fx_to_eur</b> del
                        payload del portafoglio, a precisione piena — è lo stesso che il backend
                        usa per scalare la cassa. Può essere <b>ricalcolato</b> al momento della
                        scrittura: il saldo che torna può differire di centesimi.</>}
                      {cambio.fonte === 'fx' && <>Cambio da <b>GET /fx</b>, che lo arrotonda a
                        sei decimali: su valute piccole il backend userà un valore leggermente
                        diverso quando scala la cassa.</>}
                      {cambio.fonte === 'assente' && <>Nessuna fonte ha un cambio {valuta}→EUR:
                        né il payload del portafoglio né GET /fx
                        {ratesErr ? ` (${ratesErr})` : ''}. <b>Nessun cambio inventato.</b></>}
                    </span>
                  </>
                )}
              </div>

              <div className="campo">
                <label htmlFor="f7-rz">Perché lo stai facendo</label>
                <textarea id="f7-rz" rows={2} value={rationale}
                          placeholder="es. medio ancora: sotto 3.850 il divario col NAV torna oltre il 30%"
                          onChange={e => setRationale(e.target.value)} />
              </div>
              <div className="campo">
                <label htmlFor="f7-nt">Nota</label>
                <input id="f7-nt" className="testo" value={note} placeholder="testo libero"
                       onChange={e => setNote(e.target.value)} />
              </div>

              {/* ── AVVISI ── */}
              {discorde && (
                <div className="avv giallo"><span className="ic">▲</span>
                  <span><b>Valuta diversa da quella in book.</b> {tickerUp} è quotata
                    in {discorde.valutaBook}, hai scelto {discorde.valutaScelta}
                    {discorde.fattoreCento && <> — <b>differiscono per 100</b></>}.
                    Non blocca: controlla il prezzo.</span></div>
              )}
              {sim.caricoMuto && sim.caricoMuto !== 'ordine-incompleto' && (
                <div className="avv viola"><span className="ic">⊘</span>
                  <span><b>Il prezzo di carico dopo non è definito</b>
                    {' — '}{perche(sim.caricoMuto,
                      discorde ? [discorde.valutaBook, discorde.valutaScelta] : undefined)}.
                    {sim.caricoMuto === 'valuta-discorde' && <> La riga resta dichiarata,
                      non stimata. <b>Attenzione</b>: il backend, quando scrive, la media
                      lo stesso — senza controllare la valuta.</>}</span></div>
              )}
              {sfora && (
                <div className="avv rosso"><span className="ic">▲</span>
                  <span><b>Non copre: mancano {eur(sim.mancano)}.</b> Registri comunque —
                    in Trade Entry si scrive un ordine <b>già eseguito</b> al broker.</span></div>
              )}
              {!sfora && sim.valido && sim.cassaDopo != null && (
                <div className="avv calmo"><span className="ic">≡</span>
                  <span>
                    {entra ? <>Copre: restano <b>{eur(sim.cassaDopo)}</b></>
                      : <>La cassa <b>sale</b> a <b>{eur(sim.cassaDopo)}</b></>}
                    {volte != null && <> · questo ordine vale <b>{num(volte, 1)}×</b> la
                      tua taglia mediana</>}.</span></div>
              )}
              {sim.valido && entra && sim.copre === null && (
                <div className="avv viola"><span className="ic">⊘</span>
                  <span><b>Capienza non verificabile</b> — {cassa == null
                    ? 'la cassa non è nel payload del portafoglio'
                    : 'il controvalore in euro non è calcolabile'}.</span></div>
              )}
              {avviso && (
                <div className="avv rosso"><span className="ic"><AlertOctagon size={12} /></span>
                  <span>{avviso}</span></div>
              )}

              <button type="submit" disabled={submitting}
                      className={'spara' + (sfora ? ' sfora' : '')}>
                {submitting ? <RefreshCw size={11} className="animate-spin" /> : <Save size={11} />}
                {submitting ? 'SCRITTURA…'
                  : `REGISTRA ${action}${conv.eur != null && sim.valido ? ' · ' + eur(conv.eur) : ''}`}
              </button>
            </form>

            {/* ── L'ESITO: annunciato, e raggiungibile dal fuoco ── */}
            <div ref={esitoRef} tabIndex={-1} role="status" aria-live="polite"
                 style={{ outline: 'none' }}>
              {erroreScrittura && (
                <div className="avv rosso"><span className="ic"><AlertOctagon size={12} /></span>
                  <span><b>Il trade NON è stato scritto</b> — il backend ha risposto e ha
                    rifiutato.<span className="verbatim">{erroreScrittura}</span></span></div>
              )}
              {esitoIgnoto && (
                <div className="avv viola"><span className="ic"><HelpCircle size={12} /></span>
                  <span><b>Esito sconosciuto.</b> La richiesta è partita e la risposta non è
                    arrivata: il trade <b>potrebbe essere stato scritto</b>. Controlla i
                    movimenti <b>prima</b> di riprovare, o registreresti due volte lo
                    stesso ordine.<span className="verbatim">{esitoIgnoto}</span></span></div>
              )}
              {esito && !esito.riconosciuta && (
                <div className="avv viola"><span className="ic"><HelpCircle size={12} /></span>
                  <span><b>Risposta non riconosciuta</b>: non porta né <code>ok</code> né
                    <code> trade_id</code>. Non posso dire se il trade è stato scritto —
                    controlla i movimenti.</span></div>
              )}
              {esito && esito.riconosciuta && esito.stato === 'aggiornata' && (
                <div className="avv verde"><span className="ic"><CheckCircle2 size={12} /></span>
                  <span>Trade <b>#{esito.tradeId}</b> registrato · cassa
                    disponibile <b>{eur(esito.cassa)}</b>.</span></div>
              )}
              {esito && esito.riconosciuta && esito.stato === 'aggiornata-con-nota' && (
                <div className="avv giallo"><span className="ic"><AlertTriangle size={12} /></span>
                  <span>Trade <b>#{esito.tradeId}</b> registrato · cassa
                    <b> {eur(esito.cassa)}</b>, <b>ma il backend ha allegato una nota</b>:
                    <span className="verbatim">« {esito.nota} »</span></span></div>
              )}
              {esito && esito.riconosciuta && esito.stato === 'non-aggiornata' && (
                <div className="avv giallo"><span className="ic"><AlertTriangle size={12} /></span>
                  <span>Trade <b>#{esito.tradeId}</b> registrato, <b>ma la cassa NON è stata
                    aggiornata</b>. Il backend dice:
                    <span className="verbatim">« {esito.nota} »</span></span></div>
              )}
              {esito && esito.riconosciuta && esito.stato === 'muta' && (
                <div className="avv giallo"><span className="ic"><AlertTriangle size={12} /></span>
                  <span>Trade <b>#{esito.tradeId}</b> registrato, <b>ma la cassa non risulta
                    aggiornata</b> e il backend non ha detto perché. Controllala a mano prima
                    del prossimo ordine.</span></div>
              )}
              {/* GUARDIA PREZZI: ortogonale allo stato cassa — la scrittura è
                  passata, ma il prezzo è lontano dall'ultimo riferimento (fra
                  ±30% e ×3). Senza questo blocco il ramo avviso della specifica
                  del PM era muto (ponte NUOVO 01/08, changelog (61)). */}
              {esito && esito.riconosciuta && esito.guardia && (
                <div className="avv giallo"><span className="ic"><AlertTriangle size={12} /></span>
                  <span><b>GUARDIA PREZZI</b> — il trade è scritto, ma il prezzo è lontano
                    dal riferimento. Il backend dice:
                    <span className="verbatim">« {esito.guardia} »</span></span></div>
              )}
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
            <h2>// il libretto della cassa</h2>
            {/* ⚠ «N movimenti a registro» era falso in due stati: prima della
                prima lettura (N=0 su un registro mai letto) e quando N è il
                TETTO chiesto, non il totale — che questa pagina non ha. */}
            <span className="side">
              {!movLetto ? 'registro in lettura…'
                : movErr ? 'registro non letto'
                  : finestraPiena
                    ? `almeno ${movimenti.length} movimenti · cassa ${cassa != null ? eur(cassa) : 'n.d.'}`
                    : `${movimenti.length} ${movimenti.length === 1 ? 'movimento reso' : 'movimenti resi'}`
                      + ` · cassa ${cassa != null ? eur(cassa) : 'n.d.'}`}
            </span>
          </div>
          <div className="pb lb">
            <form onSubmit={inviaMovimento}>
              <div className="verbi cassa" role="radiogroup"
                   aria-label="Versamento o prelievo" onKeyDown={mvKeyDown}>
                {([['DEPOSIT', 'VERSA'], ['WITHDRAWAL', 'PRELEVA']] as const).map(([t, l]) => (
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
                  <label htmlFor="f7-mv-imp">Importo</label>
                  <div className={'box' + (letturaImporto && !letturaImporto.ok ? ' rotto' : '')}>
                    <input id="f7-mv-imp" ref={mvImportoRef} className="num" type="text"
                           inputMode="decimal"
                           autoComplete="off" value={mvImporto} placeholder="es. 1.234,56"
                           aria-invalid={!!(letturaImporto && !letturaImporto.ok)}
                           onChange={e => { setMvImporto(e.target.value); scordaRifiuto(); }} />
                    <span className="suf">EUR</span>
                  </div>
                  <div className={'aiuto'
                    + ((letturaImporto && !letturaImporto.ok) || prelievoScoperto ? ' male' : '')}>
                    {letturaImporto && !letturaImporto.ok ? letturaImporto.motivo
                      : prelievoScoperto
                        ? `il prelievo supera la cassa (${eur(cassa)}): il backend lo RIFIUTA`
                        : previsioneCredibile
                          ? `se passa, la cassa va da ${eur(cassa)} a ${eur(cassaDopoMovimento)}`
                          : cassa == null ? 'cassa non disponibile: la capienza non è verificabile'
                            : ' '}
                  </div>
                </div>

                <div className="campo">
                  <label htmlFor="f7-mv-dt">Data valuta</label>
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
                      : 'decide in che giorno il flusso entra nel TWR'}
                  </div>
                </div>

                <div className="campo">
                  <label htmlFor="f7-mv-nt">Causale</label>
                  <div className="box">
                    <input id="f7-mv-nt" type="text" autoComplete="off" value={mvNota}
                           placeholder="bonifico dal conto corrente"
                           onChange={e => { setMvNota(e.target.value); scordaRifiuto(); }} />
                  </div>
                  {/* «verbatim» era falso: la causale passa da .trim() e una
                      fatta di soli spazi non viene spedita affatto. */}
                  <div className="aiuto">finisce in <code>note</code>, senza gli spazi ai bordi</div>
                </div>
              </div>

              {mvAvviso && (
                <div className="avv rosso"><span className="ic">✕</span>
                  <span>{mvAvviso}</span></div>
              )}

              {/* L'ANTICIPO DELLA GUARDIA: la riga gemella esiste già. Non
                  blocca — la guardia vera è del backend — ma toglie la
                  sorpresa, che è il motivo per cui questo impianto esiste. */}
              {gemella && !mvRifiuto && (
                <div className="avv giallo"><span className="ic">⚠</span>
                  <span><b>Questa riga esiste già</b> (id={gemella.id}): stessa data,
                    stesso tipo, stesso importo — è evidenziata nel registro qui sotto.
                    Il backend la rifiuterà a meno di confermare.</span></div>
              )}

              {/* `disabled` sul solo invio in volo: dal momento che ogni
                  modifica ai campi butta il rifiuto, REGISTRA e CONFERMO non
                  possono più essere entrambi vivi sullo stesso corpo. */}
              <button type="submit" className="spara"
                      disabled={mvInvio || !!mvRifiuto}>
                <Save size={13} />
                {mvInvio ? 'INVIO…'
                  : mvTipo === 'DEPOSIT' ? 'REGISTRA IL VERSAMENTO' : 'REGISTRA IL PRELIEVO'}
              </button>
            </form>

            {/* ── L'ESITO ────────────────────────────────────────────────
                ⚠ La live region è l'annunciatore INVISIBILE qui sotto, non il
                blocco visibile: un `aria-live` che entra nell'albero INSIEME
                al testo non viene annunciato (e con `:empty{display:none}` non
                ci entrava affatto). Così, in più, i BOTTONI non stanno dentro
                una region che alcuni lettori rileggono a ogni mutazione. */}
            <div className="lb-annuncio" role="status" aria-live="polite">
              {mvRifiuto ? `Movimento rifiutato. ${mvRifiuto.rifiuto.motivo}`
                : mvIgnoto ? 'Esito ignoto: il server non ha risposto.'
                  : mvEsito ? (mvEsito.riconosciuta
                    ? `Movimento ${mvEsito.movimentoId} a registro.`
                    : 'Risposta non riconosciuta.') : ''}
            </div>
            <div className="lb-esito">
              {mvRifiuto && (
                <>
                  <div className="avv giallo">
                    <span className="ic"><AlertTriangle size={12} /></span>
                    <span>
                      <b>{mvRifiuto.rifiuto.quale === 'duplicato'
                        ? 'Già a registro: stessa data, stesso tipo, stesso importo.'
                        : mvRifiuto.rifiuto.quale === 'soglia'
                        /* la cifra NON si ricopia: `CASH_SOGLIA_CONFERMA_EUR`
                           è policy modificabile (memory_db.py:906) e vive in un
                           posto solo. Il numero vero lo porta il verbatim. */
                          ? 'Sopra la soglia che chiede conferma.'
                          : 'Movimento rifiutato.'}</b>{' '}
                      {/* ⚠ «NON è stato scritto» solo dove il backend lo
                          GARANTISCE (422/401). Su un 500 dopo l'INSERT o su un
                          503 post-commit affermarlo manda il PM a riprovare,
                          il secondo giro prende GUARDIA DUPLICATO, e il
                          bottone qui sotto — che quella guardia la spegne — fa
                          entrare il doppio movimento. */}
                      {mvRifiuto.rifiuto.nonScritto
                        ? <>Il movimento <b>NON</b> è stato scritto.</>
                        : <>Il backend ha risposto <b>HTTP {mvRifiuto.rifiuto.status}</b>:{' '}
                          <b>non posso dire</b> se la riga sia stata scritta — controlla
                          il registro qui sotto prima di riprovare.</>}
                      {' '}Il backend dice:
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
                          {mvInvio ? 'INVIO…' : 'CONFERMO, È VOLUTO'}
                        </button>
                        <button type="button" className="lb-ann" ref={mvAnnullaRef}
                                onClick={() => {
                                  setMvRifiuto(null);
                                  mvImportoRef.current?.focus();
                                }} disabled={mvInvio}>
                          ANNULLA
                        </button>
                      </div>
                      {/* La chiave è UNA per DUE guardie (memory_db.py:958 e
                          :964): chi conferma deve sapere che spegne anche
                          l'altra. Dirlo è la resa decisa dal PM il 20/08. */}
                      <div className="gate">
                        confermando riparte lo <b>stesso</b> movimento con{' '}
                        <code>conferma=true</code>, che spegne <b>entrambe</b> le guardie
                        scavalcabili — il duplicato <b>e</b> quella sull'importo.
                        È una chiave sola.
                      </div>
                    </>
                  )}
                </>
              )}

              {mvIgnoto && (
                <div className="avv viola"><span className="ic"><HelpCircle size={12} /></span>
                  <span><b>Esito ignoto</b>: il server non ha risposto, quindi{' '}
                    <b>non so</b> se il movimento sia stato scritto. <b>Sto rileggendo</b>{' '}
                    il registro qui sotto: se la riga compare, è passato.
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
                      <b>Movimento {mvEsito.movimentoId} a registro.</b>{' '}
                      {/* i quattro stati hanno quattro rami: prima
                          `aggiornata-con-nota` cadeva in quello che afferma
                          l'OPPOSTO («la cassa non è stata aggiornata») */}
                      {mvEsito.stato === 'aggiornata'
                        ? <>Cassa aggiornata: <b>{eur(mvEsito.cassa)}</b>.</>
                        : mvEsito.stato === 'aggiornata-con-nota'
                          ? <>Cassa aggiornata a <b>{eur(mvEsito.cassa)}</b>, ma con una
                            nota del backend.</>
                          : mvEsito.stato === 'muta'
                            ? <>La risposta <b>non porta la cassa</b>: il registro è scritto,
                              ma il valore nuovo non è dichiarato.</>
                            : <>La cassa <b>non</b> è stata aggiornata: registro e cassa
                              operativa <b>divergono</b>.</>}
                      {mvEsito.nota && (
                        <span className="verbatim">« {mvEsito.nota} »</span>
                      )}
                    </span>
                  </div>
                ) : (
                  <div className="avv viola"><span className="ic"><HelpCircle size={12} /></span>
                    <span><b>Risposta non riconosciuta</b>: non porta né <code>ok</code> né{' '}
                      <code>movement_id</code>. Non affermo che sia scritto né che non lo
                      sia — controlla il registro qui sotto.</span></div>
                )
              )}
            </div>

            {/* ── LE RIGHE GIÀ SCRITTE ── */}
            <div className="lbm">
              {!movLetto ? (
                <div className="vuoto">Lettura del registro in corso…</div>
              ) : movErr ? (
                <div className="vuoto">Registro non letto: <b>{movErr}</b>. Il modulo
                  qui sopra resta usabile, ma senza le righe non posso dirti se una
                  terna è già a registro.</div>
              ) : movimenti.length === 0 ? (
                <div className="vuoto">Nessun movimento di cassa a registro.</div>
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
                  const esatto = val == null ? 'importo n.d.'
                    : `${m.date} · ${m.type === 'DEPOSIT' ? 'versamento' : 'prelievo'}`
                      + ` ${verso}${num(Math.abs(val))} €`;
                  return (
                    <div className={'uo' + (dupe ? ' qui' : '')} key={m.id}
                         ref={dupe ? mvGemellaRef : undefined}
                         aria-label={esatto + (m.note ? ` — ${m.note}` : '')}
                         title={esatto + (m.note ? `\n${m.note}` : '')}>
                      <span className="dt num">{gg(m.date)}</span>
                      <span className="tk">
                        {m.type === 'DEPOSIT' ? 'VERSAMENTO' : 'PRELIEVO'}</span>
                      <span className="ba">
                        {movimentoMax > 0 && val != null && (
                          <i className={dupe ? 'q' : undefined}
                             style={{ width: pct(Math.abs(val) / movimentoMax) }} />
                        )}
                      </span>
                      <span className="ev num">
                        {val == null ? <span className="nd">importo n.d.</span>
                          : <>{verso}{eur(Math.abs(val), 0)}</>}
                      </span>
                      <span className="ev num fl">
                        {cum != null ? `flussi ${eur(cum, 0)}` : 'flussi n.d.'}
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
                le righe rese sono <b>{movimenti.length}</b>, cioè esattamente il tetto
                chiesto: se il registro ne ha di più vecchie <b>non le vedo</b>, e la
                colonna flussi parte da metà storia.
              </div>
            )}
            {/* La colonna dice «flussi» e non «saldo» perché il saldo cassa NON
                è ricostruibile da qui: anche i trade la muovono. Sulla rosa
                quella colonna dava una cifra incompatibile col versamento. */}
            <div className="gate">
              <b>flussi</b> = cumulato dei versamenti e prelievi <b>resi qui sotto</b>.
              Non è il saldo di cassa: anche i trade la muovono, e in questo registro
              non ci sono.
            </div>
          </div>
        </div>

        {/* ── PRIMA → DOPO ── */}
        <div className="rq">
          <div className="ph c">
            <h2>// prima → dopo</h2>
            <span className="side">
              {sim.valutazione === 'eseguito-proxy' ? 'valutato al prezzo che hai scritto'
                : 'simulazione sul book vivo'}
            </span>
          </div>
          <div className="pb">
            {!sim.valido ? (
              <div className="t-no" style={{ fontSize: 12 }}>
                La simulazione compare quando ticker, quantità e prezzo sono scritti.
              </div>
            ) : (
              <>
                <div className="pd">
                  <Riga k="Cassa" a={eur(sim.cassaPrima)} b={eur(sim.cassaDopo)}
                        muto={sim.cassaDopo == null}
                        dl={sim.cassaDopo != null && sim.cassaPrima != null
                          ? segno(sim.cassaDopo - sim.cassaPrima) + ' €' : 'n.d.'}
                        verso={entra ? 1 : -1} grosso sfora={sfora} />
                  <Riga k={`Titoli ${tickerUp}`}
                        a={sim.qtaPrima != null ? exact(sim.qtaPrima) : 'n.d.'}
                        b={sim.qtaMuta ? '—' : exact(sim.qtaDopo)}
                        muto={!!sim.qtaMuta}
                        dl={sim.qtaMuta ? perche(sim.qtaMuta)
                          : (sim.qtaDopo != null && sim.qtaPrima != null
                            ? (sim.qtaDopo - sim.qtaPrima > 0 ? '+' : '') + exact(sim.qtaDopo - sim.qtaPrima)
                            : 'n.d.')}
                        verso={0} />
                  <Riga k="Prezzo di carico"
                        a={sim.caricoPrima != null ? `${num(sim.caricoPrima)} ${pos?.valuta || ''}` : 'n.d.'}
                        b={sim.caricoMuto ? '—' : `${num(sim.caricoDopo)} ${pos?.valuta || valuta}`}
                        muto={!!sim.caricoMuto}
                        dl={sim.caricoMuto
                          ? perche(sim.caricoMuto,
                            discorde ? [discorde.valutaBook, discorde.valutaScelta] : undefined)
                          : sim.caricoInvariato ? 'invariato'
                            : (sim.caricoDopo != null && sim.caricoPrima != null
                              ? segno(sim.caricoDopo - sim.caricoPrima) : 'nuovo carico')}
                        verso={sim.caricoDopo != null && sim.caricoPrima != null
                          ? Math.sign(sim.caricoDopo - sim.caricoPrima) : 0} />
                  <Riga k="Peso in titoli"
                        a={sim.pesoPrima != null ? num(sim.pesoPrima) + '%' : 'n.d.'}
                        b={sim.pesoMuto ? '—' : num(sim.pesoDopo) + '%'}
                        muto={!!sim.pesoMuto}
                        dl={sim.pesoMuto ? perche(sim.pesoMuto)
                          : (sim.pesoDopo != null && sim.pesoPrima != null
                            ? segno(sim.pesoDopo - sim.pesoPrima) + ' pt' : 'n.d.')}
                        verso={0} />
                  <Riga k="NAV" a={eur(sim.navPrima, 0)}
                        b={sim.navMuto ? '—' : eur(sim.navDopo, 0)}
                        muto={!!sim.navMuto}
                        dl={sim.navMuto ? perche(sim.navMuto)
                          : (sim.navDelta != null
                            ? (Math.abs(sim.navDelta) < 0.005 ? 'invariato' : segno(sim.navDelta) + ' €')
                            : 'n.d.')}
                        verso={sim.navDelta != null ? -Math.sign(sim.navDelta) : 0} />
                </div>
                <div className="avv calmo" style={{ marginTop: 8 }}>
                  <span className="ic">≡</span>
                  <span>
                    È una <b>simulazione</b>. La cassa si muove al prezzo che hai <b>scritto</b>;
                    i titoli sono valutati{' '}
                    {sim.valutazione === 'mercato' ? <>al <b>prezzo di mercato</b></>
                      : sim.valutazione === 'eseguito-proxy'
                        ? <>al <b>prezzo che hai scritto</b>, perché per un nome nuovo non c'è
                          ancora un prezzo di mercato</>
                        : <>— manca il prezzo per valutarli</>}.
                    {' '}Il NAV cambia della differenza fra i due, non resta fermo per
                    definizione. Il carico dopo è <b>la nostra aritmetica</b>: di norma coincide
                    con quella del backend, ma non è promesso.
                  </span>
                </div>
              </>
            )}
          </div>
        </div>

        {/* ── GLI ULTIMI ORDINI ── */}
        <div className="rq cresce">
          <div className="ph">
            <h2>// gli ultimi ordini che hai scritto</h2>
            <span className="side">stessa scala del binario</span>
          </div>
          <div className="pb scorre nopad">
            {tradesErr ? (
              <div className="vuoto">Storico non disponibile: <b>{tradesErr}</b>. Il binario
                resta valido, ma senza le taglie di confronto.</div>
            ) : ultimi.length === 0 ? (
              <div className="vuoto">Nessun movimento in archivio.</div>
            ) : (
              <>
                {sim.valido && (
                  <div className="uo qui">
                    <span className="dt">ORA</span>
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
                        : <span className="nd">cambio {t.valuta} n.d.</span>}
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
            <h2>// la cassa</h2>
            <span className="side">
              {bookAssente ? 'portafoglio non caricato'
                : cassa != null ? `${eur(cassa)} · dal campo cash_disponibile_eur`
                  : 'cash_disponibile_eur non è nel payload'}
            </span>
            <button type="button" className="mini" onClick={carica} aria-busy={loading}>
              <RefreshCw size={10} className={loading ? 'animate-spin' : ''} /> AGGIORNA
            </button>
          </div>
          <div className="pb">
            {posErr ? (
              <div className="vuoto">
                <span><b>Portafoglio non disponibile</b> — {posErr}.<br />
                  Il binario non si disegna: senza la cassa la capienza non è verificabile.
                  Non è detto che il book sia vuoto.</span>
              </div>
            ) : cassa == null ? (
              <div className="vuoto">
                <span><b>La cassa non è nel payload</b> (<code>cash_disponibile_eur</code>{' '}
                  assente o non numerico): la capienza <b>non è verificabile</b> e il binario
                  non si disegna. Un binario vuoto si leggerebbe come «zero».</span>
              </div>
            ) : cassa <= 0 ? (
              <div className="vuoto">
                <span><b>{cassa === 0 ? 'La cassa è a zero' : `Sei in scoperto di ${eur(-cassa)}`}</b>
                  {' '}— è una <b>misura</b>, non un dato mancante: il binario non ha una
                  larghezza da disegnare. Ogni acquisto qui sotto risulterà scoperto.</span>
              </div>
            ) : scala != null ? (
              <>
                <div className={'binario' + (sfora ? ' sfora' : '') + (pezzo && !entra ? ' entrata' : '')}>
                  {pezzo && (
                    <>
                      <div className="morso"
                           style={{ left: pct(pezzo.da), width: pct(pezzo.largo) }} />
                      <div className="etm">
                        {sfora ? 'NON COPRE' : entra ? 'QUESTO ORDINE ESCE' : 'QUESTO ORDINE ENTRA'}
                        <b className="num">{entra ? '−' : '+'}{eur(delta)}</b>
                      </div>
                    </>
                  )}
                  <div className="resto">
                    <span className="num">
                      {sfora && sim.mancano != null
                        ? <>mancano {eur(sim.mancano)}</>
                        : <>{eur(pezzo ? sim.cassaDopo : cassa)}{' '}
                          <span className="t-no" style={{ fontSize: 10 }}>
                            {pezzo ? (entra ? 'RESTANO' : 'IN CASSA DOPO') : 'IN CASSA'}</span></>}
                    </span>
                  </div>
                </div>

                <div className="tacche" ref={tacchePista} onKeyDown={taccheKeyDown}
                     role="group" aria-label="Le taglie dei tuoi ordini già scritti">
                  {taglie.misurate.map((t, i) => (
                    <button key={`${t.data}-${t.ticker}-${i}`} type="button" className="tc"
                            tabIndex={i === taccaSel ? 0 : -1}
                            style={{ left: pct((t.eur as number) / scala) }}
                            aria-label={`${t.ticker} ${t.action} del ${gg(t.data)}, `
                              + `${num(t.locale)} ${t.valuta}, pari a ${eur(t.eur)}`}
                            onMouseEnter={() => setTaccaHover(i)}
                            onMouseLeave={() => setTaccaHover(null)}
                            onFocus={() => { setTaccaFuoco(i); setTaccaSel(i); }}
                            onBlur={() => setTaccaFuoco(null)} />
                  ))}
                  {taglie.mediana != null && (
                    <>
                      <span className="rif" style={{ left: pct(taglie.mediana / scala) }} />
                      <span className="lb" style={{ left: pct(taglie.mediana / scala) }}>
                        MEDIANA {num(taglie.mediana, 0)} €
                      </span>
                    </>
                  )}
                  {taglie.massimo != null && taglie.massimo <= scala && (
                    <>
                      <span className="rif" style={{ left: pct(taglie.massimo / scala) }} />
                      <span className="lb" style={{ left: pct(taglie.massimo / scala) }}>
                        IL TUO MASSIMO {num(taglie.massimo, 0)} €
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
                        <div className="kv"><span>quando</span>
                          <span className="num">{t.data.slice(0, 10)}</span></div>
                        <div className="kv"><span>controvalore</span>
                          <span className="num">{num(t.locale)} {t.valuta}</span></div>
                        <div className="kv"><span>in euro</span>
                          <span className="num">{eur(t.eur)}</span></div>
                      </div>
                    );
                  })()}
                </div>

                <div className="leg">
                  <i><span className={'sw mo' + (pezzo && !entra ? ' in' : '')} />
                    {entra ? 'quanto esce' : 'quanto entra'}
                    {delta != null && <> — <b>{num((delta / scala) * 100, 1)}%</b> della scala</>}</i>
                  <i><span className="sw tc" />i tuoi <b>{taglie.misurate.length}</b> ordini già
                    scritti, convertiti in EUR coi cambi di oggi</i>
                  <i><span className="sw md" />mediana e massimo</i>
                  <i>la larghezza intera è {entra || !pezzo ? 'la cassa di adesso'
                    : 'la cassa DOPO l\'ordine'}<span className="t-et">: {eur(scala)}</span></i>
                  {fuoriScala > 0 && (
                    <i className="gate"><b>{fuoriScala}</b> ordini più grandi della scala:
                      disegnati a fondo corsa</i>
                  )}
                  {taglie.senzaCambio > 0 && (
                    <i className="gate"><b>{taglie.senzaCambio}</b> ordini senza tacca: nessun
                      cambio per {taglie.scoperte.join(', ')}</i>
                  )}
                  {tradesErr && <i className="gate">storico non disponibile: nessuna tacca</i>}
                </div>
              </>
            ) : null}
          </div>
        </div>

        {/* ── IL BOOK ── */}
        <div className="rq cresce">
          <div className="ph">
            <h2>// il book</h2>
            <span className="side">
              {bookAssente ? 'non disponibile'
                : `ordinato per controvalore — ${book.fuoriPosto}/${book.righe.length} righe `
                  + 'arrivano in un altro ordine'}
              {book.senzaValore > 0 && ` · ${book.senzaValore} senza controvalore, in coda`}
            </span>
          </div>
          <div className="pb scorre nopad">
            <table>
              <thead>
                <tr>
                  <th>Ticker</th><th>Val</th><th className="r">Qtà</th>
                  <th className="r">Carico</th><th className="r">Live</th>
                  <th className="r">Controvalore</th><th className="r">Peso</th>
                  <th className="r">P&amp;L</th><th className="r">Scarto</th>
                </tr>
              </thead>
              <tbody>
                {posErr ? (
                  <tr><td colSpan={9} className="d" style={{ textAlign: 'center', padding: 22 }}>
                    POSIZIONI NON DISPONIBILI — {posErr}. Il book non è detto sia vuoto:
                    backend in errore, usa AGGIORNA.
                  </td></tr>
                ) : loading && book.righe.length === 0 ? (
                  <tr><td colSpan={9} style={{ textAlign: 'center', padding: 22 }}>
                    caricamento posizioni…</td></tr>
                ) : book.righe.length === 0 ? (
                  <tr><td colSpan={9} style={{ textAlign: 'center', padding: 22 }}>
                    Nessuna posizione. Usa BUY per aprire la prima.</td></tr>
                ) : book.righe.map(r => (
                  <tr key={r.pos.ticker}
                      className={r.pos.ticker.toUpperCase() === tickerUp ? 'mira' : undefined}>
                    <td className="d">
                      {/* un comando VERO: prima era la riga a rispondere a INVIO e
                          SPAZIO, e uno spazio battuto per scorrere sovrascriveva
                          in silenzio il modulo che stavi compilando */}
                      <button type="button" className="usa"
                              aria-label={`Usa ${r.pos.ticker} nel tagliando`}
                              onClick={() => selectPosition(r.pos)}>{r.pos.ticker}</button>
                    </td>
                    <td className="num">{r.pos.valuta}</td>
                    <td className="r num">{exact(r.pos.quantita)}</td>
                    <td className="r num">{num(r.pos.prezzo_medio)}</td>
                    <td className="r num">{r.pos.prezzo_live != null ? num(r.pos.prezzo_live) : 'n.d.'}</td>
                    <td className="r num d">{eur(r.pos.valore_mercato, 0)}</td>
                    <td className="r num">{r.peso != null ? num(r.peso) + '%' : 'n.d.'}</td>
                    <td className="r num">
                      {r.pos.pl_pct == null ? 'n.d.' : (
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
          tone={pending.sim.copre === false || pending.discorde ? 'crimson' : 'amber'}
          title="Registrare il trade nel portafoglio ufficiale?"
          intro="Il movimento entra nel portafoglio e fa ricalcolare posizioni e P/L."
          rows={pendingRows(pending)}
          warn={(pending.sim.copre === false
            ? `NON COPRE: mancano ${eur(pending.sim.mancano)} rispetto alla cassa disponibile. ` : '')
            + 'La scrittura tocca DUE archivi: il movimento e le posizioni in '
            + 'data/consigliere.db, e la cassa in portfolio.json. L\'app non ha una funzione '
            + 'di annullo: una correzione va fatta a mano su ENTRAMBI.'}
          confirmLabel={`REGISTRA ${pending.body.action}`}
          onConfirm={() => { const p = pending; setPending(null); commit(p); }}
          onCancel={() => setPending(null)}
        />
      )}
    </div>
  );
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
