import { t as tr } from '../i18n/t.js';
// ============================================================================
// F7 TRADE ENTRY — "LA CASSA" · le derivazioni, FUORI dai componenti
// (Opus 5, 27/07. Spec: docs/superpowers/specs/2026-07-27-f7-trade-entry-la-cassa-design.md)
//
// Modello: app/src/lib/movimenti.ts di F14. Funzioni PURE: niente React,
// niente fetch, niente stato. E' il posto dove i difetti si leggono senza
// montare nulla.
//
// QUATTRO REGOLE CHE QUESTO FILE FA RISPETTARE:
//  1. NIENTE FALLBACK SILENZIOSI (PM 14/07). Un cambio che manca non diventa
//     1.0: diventa `null`, e chi rende DEVE dichiararlo. Un proxy ammesso
//     viaggia con la sua etichetta (v. `valutazione`).
//  2. NON SI SOMMANO DUE VALUTE. Un carico si ricostruisce sommando
//     `quantita*prezzo`: se l'ordine e' in una valuta diversa da quella della
//     posizione, quella somma NON e' un prezzo.
//  3. ZERO NON E' UN DATO ASSENTE — e vale nei due sensi: una cassa a zero e'
//     una MISURA, non un buco; un buco non si rende come zero.
//  4. NIENTE SIMULAZIONI SU UN ORDINE CHE NON C'E'. Con i campi vuoti
//     `Number('')` fa 0: senza un guard la pagina calcolava "non copre" a
//     modulo bianco. Si simula solo se l'ordine e' scritto per intero.
//
// La review avversariale del 27/07 (10 agenti) ha smontato la prima stesura su
// tutti e quattro i punti: le correzioni sono annotate una per una.
// ============================================================================
import type { Position, PortfolioSnapshot } from '@/lib/api';
import { portfolioValues } from '@/lib/portfolio-values';
import { linguaCorrente, type Lingua } from '../i18n/lingua.js';
import { analizzaNumero, scriviNumero } from '../i18n/numeri.js';
import { traduci } from '../i18n/t.js';

export type Verbo = 'BUY' | 'ADD' | 'TRIM' | 'SELL' | 'DIVIDEND';

/** I verbi che tolgono cassa e aggiungono titoli. */
export const ENTRA = (a: Verbo): boolean => a === 'BUY' || a === 'ADD';
/** I verbi che aggiungono cassa e tolgono titoli. */
export const ESCE = (a: Verbo): boolean => a === 'TRIM' || a === 'SELL';

// ── IL CAMBIO ───────────────────────────────────────────────────────────────
// Due fonti, e la fonte VIAGGIA col numero: in pagina si scrive da dove viene.
//
// ⚠ ORDINE DI AUTOREVOLEZZA ROVESCIATO DALLA REVIEW. `GET /fx` arrotonda a 6
// decimali (bellomberg_api.py:716); il campo `fx_to_eur` del payload del
// portafoglio e' lo STESSO get_fx_to_eur a precisione piena, ed e' quello che
// il backend usa davvero quando scala la cassa. Sui pence il tasso piccolo
// perde cifre significative con l'arrotondamento e altera la cassa simulata.
// Quindi: prima il payload a precisione piena, poi /fx.

export type FonteCambio = 'nativa' | 'portafoglio' | 'fx' | 'assente';

export interface Cambio {
  valuta: string;
  /** null = non disponibile. MAI 1.0 di ripiego. */
  tasso: number | null;
  fonte: FonteCambio;
  /** true quando il tasso viene da /fx, che lo arrotonda a 6 decimali */
  arrotondato: boolean;
}

export function cambioPer(
  valuta: string,
  rates: Record<string, number> | null,
  posizioni: Position[],
): Cambio {
  const v = (valuta || '').toUpperCase();
  if (v === 'EUR') return { valuta: v, tasso: 1, fonte: 'nativa', arrotondato: false };

  // precisione piena, e la stessa che il backend usa per scalare la cassa
  const p = posizioni.find(
    x => (x.valuta || '').toUpperCase() === v
      && typeof x.fx_to_eur === 'number' && isFinite(x.fx_to_eur) && x.fx_to_eur > 0,
  );
  if (p) {
    return { valuta: v, tasso: p.fx_to_eur as number, fonte: 'portafoglio', arrotondato: false };
  }

  const daFx = rates ? rates[v] : undefined;
  if (typeof daFx === 'number' && isFinite(daFx) && daFx > 0)
    return { valuta: v, tasso: daFx, fonte: 'fx', arrotondato: true };

  return { valuta: v, tasso: null, fonte: 'assente', arrotondato: false };
}

// ── LA CONVERSIONE ──────────────────────────────────────────────────────────

export interface Ordine {
  ticker: string;
  action: Verbo;
  quantita: number;
  prezzo: number;
  valuta: string;
}

export interface Conversione {
  /** quantita x prezzo, nella valuta scritta. */
  locale: number;
  /** null quando il cambio manca: la catena si ferma qui e la pagina lo scrive. */
  eur: number | null;
  cambio: Cambio;
}

export function converti(o: Ordine, cambio: Cambio): Conversione {
  const locale = o.quantita * o.prezzo;
  const eur = (cambio.tasso == null || !isFinite(locale)) ? null : locale * cambio.tasso;
  return { locale, eur, cambio };
}

// ── LA SIMULAZIONE ──────────────────────────────────────────────────────────

/** perche' un numero "dopo" non e' definito */
export type Muto =
  | null
  | 'ordine-incompleto'      // i campi non sono ancora scritti
  | 'valuta-discorde'        // sommerebbe due valute: non sarebbe un prezzo
  | 'carico-assente'         // la posizione non ha un prezzo medio in DB
  | 'posizione-chiusa'       // dopo l'ordine non resta nessun titolo
  | 'book-assente'           // il portafoglio non e' caricato: non so cosa possiedi
  | 'nav-assente'            // cassa non verificata o NAV non disponibile
  | 'non-posseduto'          // vendere/incassare su un ticker che non e' in book
  | 'prezzo-ignoto'          // manca il prezzo di mercato per valutare il residuo
  | 'cambio-assente';        // senza euro non si tocca ne' cassa ne' peso

/** con che prezzo e' stato valutato cio' che entra o esce dal book */
export type Valutazione = 'mercato' | 'eseguito-proxy' | 'ignota';

export interface Simulazione {
  /** false quando l'ordine non e' scritto per intero: NIENTE si simula */
  valido: boolean;

  cassaPrima: number | null;
  cassaDopo: number | null;
  /** null = non verificabile · true/false solo per BUY e ADD */
  copre: boolean | null;
  mancano: number | null;
  /** la cassa e' un numero MISURATO anche quando vale 0 o e' negativa */
  cassaNegativa: boolean;

  qtaPrima: number | null;
  qtaDopo: number | null;
  qtaMuta: Muto;
  nuovaPosizione: boolean;

  caricoPrima: number | null;
  caricoDopo: number | null;
  caricoMuto: Muto;
  caricoInvariato: boolean;

  pesoPrima: number | null;
  pesoDopo: number | null;
  pesoMuto: Muto;

  navPrima: number | null;
  navDopo: number | null;
  navMuto: Muto;
  /** il NAV si muove anche fuori dal dividendo, se l'eseguito non e' il mercato */
  navDelta: number | null;

  /** con che prezzo e' valutato cio' che entra/esce: va DICHIARATO in pagina */
  valutazione: Valutazione;
}

const VUOTA: Simulazione = {
  valido: false,
  cassaPrima: null, cassaDopo: null, copre: null, mancano: null, cassaNegativa: false,
  qtaPrima: null, qtaDopo: null, qtaMuta: 'ordine-incompleto', nuovaPosizione: false,
  caricoPrima: null, caricoDopo: null, caricoMuto: 'ordine-incompleto', caricoInvariato: false,
  pesoPrima: null, pesoDopo: null, pesoMuto: 'ordine-incompleto',
  navPrima: null, navDopo: null, navMuto: 'ordine-incompleto', navDelta: null,
  valutazione: 'ignota',
};

export function simula(
  o: Ordine,
  conv: Conversione,
  pos: Position | undefined,
  snap: PortfolioSnapshot | null,
  /** il portafoglio non e' caricato (errore di fetch): `pos` assente NON prova nulla */
  bookAssente = false,
): Simulazione {
  const { cash: cassaPrima, nav: navPrima, invested: mvPrima } = portfolioValues(snap);

  // ⚠ REGOLA 4: senza un ordine scritto non si simula NIENTE. Prima, con i
  // campi vuoti, `eur` valeva 0 e su cassa negativa la pagina accendeva
  // "Non copre: mancano X" a modulo bianco.
  const scritto = !!o.ticker && isFinite(o.quantita) && o.quantita > 0
    && isFinite(o.prezzo) && o.prezzo > 0;
  if (!scritto) {
    return { ...VUOTA, cassaPrima, navPrima, cassaNegativa: cassaPrima != null && cassaPrima < 0 };
  }

  const eur = conv.eur;
  const fx = conv.cambio.tasso;
  const entra = ENTRA(o.action);
  const esce = ESCE(o.action);
  const dividendo = o.action === 'DIVIDEND';

  // ── cassa
  let cassaDopo: number | null = null;
  if (cassaPrima != null && eur != null) {
    cassaDopo = entra ? cassaPrima - eur : cassaPrima + eur;
  }
  const copre = !entra ? null
    : (cassaPrima != null && eur != null) ? eur <= cassaPrima : null;
  const mancano = (copre === false && cassaPrima != null && eur != null)
    ? eur - cassaPrima : null;

  // ── titoli. Senza book non si afferma "0": si dichiara.
  const nuovaPosizione = !pos && !bookAssente;
  let qtaPrima: number | null = null;
  let qtaDopo: number | null = null;
  let qtaMuta: Muto = null;
  if (bookAssente) {
    qtaMuta = 'book-assente';
  } else if (!pos && !entra) {
    qtaMuta = 'non-posseduto';                 // vendere cio' che non risulta posseduto
  } else {
    qtaPrima = pos ? pos.quantita : 0;
    qtaDopo = dividendo ? qtaPrima
      : entra ? qtaPrima + o.quantita
        : qtaPrima - o.quantita;               // niente clamp: un -7.929 URLA, uno 0 no
  }

  // ── carico
  const caricoPrima = pos ? pos.prezzo_medio : null;
  const valutaPos = pos ? (pos.valuta || '').toUpperCase() : null;
  const valutaOrd = (o.valuta || '').toUpperCase();
  const caricoInvariato = esce || dividendo;

  let caricoDopo: number | null = null;
  let caricoMuto: Muto = null;
  if (qtaMuta) {
    caricoMuto = qtaMuta;
  } else if (esce && qtaDopo != null && qtaDopo <= 0) {
    caricoMuto = 'posizione-chiusa';           // un carico di zero titoli non e' "invariato"
  } else if (caricoInvariato) {
    caricoDopo = caricoPrima;
  } else if (nuovaPosizione) {
    caricoDopo = o.prezzo;                     // la prima riga fa il carico
  } else if (valutaPos && valutaOrd && valutaPos !== valutaOrd) {
    caricoMuto = 'valuta-discorde';            // REGOLA 2
  } else if (caricoPrima == null) {
    caricoMuto = 'carico-assente';             // la posizione non ha prezzo medio in DB
  } else if (qtaDopo && qtaDopo > 0 && qtaPrima != null) {
    caricoDopo = (qtaPrima * caricoPrima + o.quantita * o.prezzo) / qtaDopo;
  }

  // ── quanto vale, a MERCATO, cio' che entra o esce dal book.
  // ⚠ LA CORREZIONE PIU' IMPORTANTE DELLA REVIEW. Prima i titoli si muovevano
  // di `conv.eur`, cioe' al prezzo ESEGUITO, mentre `valore_mercato` e'
  // valutato al prezzo LIVE: dopo una vendita totale il peso usciva NEGATIVO
  // ("possiedi ancora un pezzo di cio' che hai chiuso") e "NAV invariato" era
  // falso di q*(live-eseguito)*fx.
  const live = pos && typeof pos.prezzo_live === 'number' && isFinite(pos.prezzo_live)
    ? pos.prezzo_live : null;
  // ⚠ `prezzo_live` e' nella valuta della POSIZIONE, non in quella dell'ordine:
  // va convertito col cambio della posizione. Usare `fx` (il cambio scelto nel
  // modulo) confonde le unita': un prezzo in pence verrebbe trattato come euro,
  // gonfiando il valore di mercato e il NAV simulato. Il numero puo' sembrare
  // plausibile: la verifica deve confrontare le unita', non solo l'intervallo.
  const fxPos = pos && typeof pos.fx_to_eur === 'number' && isFinite(pos.fx_to_eur)
    && pos.fx_to_eur > 0 ? pos.fx_to_eur : null;
  let valutazione: Valutazione = 'ignota';
  let mercatoEur: number | null = null;
  if (live != null && fxPos != null) {
    mercatoEur = o.quantita * live * fxPos; valutazione = 'mercato';
  } else if (nuovaPosizione && fx != null) {
    // una posizione nuova non ha un live: valutarla all'eseguito e' un proxy
    // LECITO, e viaggia etichettato (regola 1).
    mercatoEur = o.quantita * o.prezzo * fx; valutazione = 'eseguito-proxy';
  }

  // ── peso
  const valorePrima = pos ? (pos.valore_mercato ?? null) : (nuovaPosizione ? 0 : null);
  let pesoPrima: number | null = null;
  let pesoDopo: number | null = null;
  let pesoMuto: Muto = null;
  if (qtaMuta) pesoMuto = qtaMuta;
  else if (mvPrima == null || mvPrima <= 0 || valorePrima == null) pesoMuto = 'book-assente';
  else if (mercatoEur == null) pesoMuto = fx == null ? 'cambio-assente' : 'prezzo-ignoto';
  else {
    pesoPrima = (valorePrima / mvPrima) * 100;
    const d = dividendo ? 0 : (entra ? mercatoEur : -mercatoEur);
    const mvDopo = mvPrima + d;
    const valDopo = Math.max(0, valorePrima + d);   // una posizione chiusa pesa 0, non -0,3%
    pesoDopo = mvDopo > 0 ? (valDopo / mvDopo) * 100 : null;
    if (pesoDopo == null) pesoMuto = 'prezzo-ignoto';
  }

  // ── NAV: cassa +/- eseguito, titoli +/- mercato. Coincidono solo se
  // l'eseguito E' il mercato — e quasi mai lo e', perche' qui si registra un
  // ordine gia' passato al broker.
  let navDelta: number | null = null;
  let navMuto: Muto = null;
  if (eur == null) navMuto = 'cambio-assente';
  else if (dividendo) navDelta = eur;
  else if (mercatoEur == null) navMuto = qtaMuta || 'prezzo-ignoto';
  else navDelta = entra ? (mercatoEur - eur) : (eur - mercatoEur);
  const navDopo = (navPrima == null || navDelta == null) ? null : navPrima + navDelta;
  if (navPrima == null) navMuto = navMuto || 'nav-assente';

  return {
    valido: true,
    cassaPrima, cassaDopo, copre, mancano,
    cassaNegativa: cassaPrima != null && cassaPrima < 0,
    qtaPrima, qtaDopo, qtaMuta, nuovaPosizione,
    caricoPrima, caricoDopo, caricoMuto, caricoInvariato,
    pesoPrima, pesoDopo, pesoMuto,
    navPrima, navDopo, navMuto, navDelta,
    valutazione,
  };
}

/** la frase da mettere accanto a un numero che non c'e'. Mai un trattino nudo. */
export function perche(m: Muto, valute?: string[]): string {
  switch (m) {
    case 'ordine-incompleto': return tr('trade.order_incomplete');
    case 'valuta-discorde':
      return tr('trade.would_add_currencies', {a: valute && valute.length === 2 ? valute.join(tr('trade.and_fragment')) : tr('trade.two_currencies')});
    case 'carico-assente': return tr('trade.cost_missing');
    case 'posizione-chiusa': return tr('trade.position_closed');
    case 'book-assente': return tr('trade.portfolio_unloaded');
    case 'nav-assente': return tr('trade.nav_unavailable');
    case 'non-posseduto': return tr('trade.holding_not_active');
    case 'prezzo-ignoto': return tr('trade.residual_market_missing');
    case 'cambio-assente': return tr('trade.euro_fx_missing');
    default: return '';
  }
}

// ── LE TAGLIE STORICHE ──────────────────────────────────────────────────────

export interface TradeRiga {
  ticker: string;
  action: string;
  data: string;
  quantita: number;
  prezzo: number;
  valuta: string;
}

export interface Taglia {
  ticker: string;
  action: string;
  data: string;
  valuta: string;
  locale: number;
  eur: number | null;
}

export interface Taglie {
  taglie: Taglia[];
  /** quelle misurabili, ordinate per CONTROVALORE crescente — cioe' nell'ordine
   *  in cui compaiono sul binario. La navigazione a frecce segue la posizione,
   *  non la data: su un righello ←/→ promettono uno spostamento spaziale. */
  misurate: Taglia[];
  mediana: number | null;
  massimo: number | null;
  /** valute per cui nessuna fonte ha dato un cambio: si DICHIARANO */
  scoperte: string[];
  senzaCambio: number;
}

export function taglieStoriche(
  trades: TradeRiga[],
  rates: Record<string, number> | null,
  posizioni: Position[],
): Taglie {
  const cache: Record<string, Cambio> = {};
  const scoperte: string[] = [];
  const taglie: Taglia[] = trades.map(t => {
    const v = (t.valuta || '').toUpperCase() || '?';
    if (!cache[v]) cache[v] = cambioPer(v, rates, posizioni);
    const c = cache[v];
    if (c.tasso == null && scoperte.indexOf(v) < 0) scoperte.push(v);
    const locale = Math.abs((t.quantita || 0) * (t.prezzo || 0));
    return {
      ticker: t.ticker, action: t.action, data: t.data, valuta: v,
      locale, eur: c.tasso == null ? null : locale * c.tasso,
    };
  });

  const misurate = taglie.filter(t => t.eur != null)
    .sort((a, b) => (a.eur as number) - (b.eur as number));
  const val = misurate.map(t => t.eur as number);          // gia' crescente
  // mediana vera: su n pari e' la media dei due centrali, non il maggiore
  const mediana = val.length
    ? (val.length % 2 ? val[(val.length - 1) / 2]
      : (val[val.length / 2 - 1] + val[val.length / 2]) / 2)
    : null;
  return {
    taglie,
    misurate,
    mediana,
    massimo: val.length ? val[val.length - 1] : null,
    scoperte,
    senzaCambio: taglie.length - misurate.length,
  };
}

// ── L'ORDINE DEL BOOK ───────────────────────────────────────────────────────
// Il backend manda le posizioni ordinate per `quantita*prezzo_medio` in valuta
// LOCALE (memory_db.py:881): le quotate in pence salgono x100. Si corregge qui,
// e lo SCARTO si rende visibile invece di nasconderlo.

export interface RigaBook {
  pos: Position;
  arrivo: number;
  posto: number;
  scarto: number;
  /** null quando `valore_mercato` non e' un numero: n.d., non 0% */
  peso: number | null;
}

export function ordinaPerControvalore(positions: Position[]): {
  righe: RigaBook[];
  fuoriPosto: number;
  peggiore: RigaBook | null;
  /** righe senza controvalore: escluse dalla classifica e DICHIARATE */
  senzaValore: number;
} {
  const vm = (p: Position): number | null =>
    typeof p.valore_mercato === 'number' && isFinite(p.valore_mercato) ? p.valore_mercato : null;
  const mv = positions.reduce((s, p) => s + (vm(p) ?? 0), 0);

  const conValore = positions.filter(p => vm(p) != null);
  const senza = positions.filter(p => vm(p) == null);
  const perEur = conValore.slice().sort((a, b) => (vm(b) as number) - (vm(a) as number));

  const arrivo = new Map<Position, number>();
  positions.forEach((p, i) => arrivo.set(p, i + 1));

  const righe: RigaBook[] = perEur.concat(senza).map((p, i) => ({
    pos: p,
    arrivo: arrivo.get(p) ?? i + 1,
    posto: i + 1,
    scarto: (i + 1) - (arrivo.get(p) ?? i + 1),
    peso: vm(p) != null && mv > 0 ? ((vm(p) as number) / mv) * 100 : null,
  }));

  const fuoriPosto = righe.filter(r => r.scarto !== 0).length;
  const peggiore = righe.reduce<RigaBook | null>(
    (best, r) => (!best || Math.abs(r.scarto) > Math.abs(best.scarto) ? r : best), null);
  return { righe, fuoriPosto, peggiore, senzaValore: senza.length };
}

// ── L'ESITO DELLA SCRITTURA ─────────────────────────────────────────────────
// ⚠ IL DIFETTO ALTA. Il backend, se non riesce ad aggiornare la cassa, risponde
// `cash_disponibile_eur: null` PIU' un `cash_note` (bellomberg_api.py:1654-1682).
// Prima il client leggeva solo il primo campo: il trade scritto e la cassa
// ferma, senza che nessuno lo dicesse.
//
// QUATTRO stati, non tre — la review ne ha trovati due in piu' della prima
// stesura:
//  · un backend puo' rispondere cassa VALORIZZATA **e** nota (la scrittura su
//    disco fallisce dopo aver calcolato il nuovo saldo): la nota non si butta;
//  · una richiesta che non torna (timeout, rete caduta) NON prova che il trade
//    non sia stato scritto — e dirlo spinge al doppio invio su una pagina
//    senza annullo.

export interface RispostaTrade {
  ok?: boolean;
  trade_id?: number | string | null;
  cash_disponibile_eur?: number | null;
  cash_note?: string | null;
  /** GUARDIA PREZZI (backend 01/08, changelog (61)): scostamento fra ±30% e ×3
   *  dall'ultimo snapshot — la scrittura passa, ma la nota viaggia. `null`
   *  quando pulito. Senza questa lettura il ramo avviso della specifica del PM
   *  era muto (voce ponte NUOVO 01/08). */
  guardia_note?: string | null;
}

export type StatoCassa = 'aggiornata' | 'aggiornata-con-nota' | 'non-aggiornata' | 'muta';

export interface Esito {
  tradeId: string;
  /** false quando la risposta non porta ne' ok ne' trade_id: non si afferma nulla */
  riconosciuta: boolean;
  stato: StatoCassa;
  cassa: number | null;
  /** la nota del backend, VERBATIM */
  nota: string | null;
  /** la nota della GUARDIA PREZZI, VERBATIM — ortogonale allo stato cassa:
   *  arriva quando il prezzo è lontano dal riferimento ma la scrittura passa */
  guardia: string | null;
}

export function esitoScrittura(r: RispostaTrade | null | undefined): Esito {
  const id = r && r.trade_id != null ? String(r.trade_id) : null;
  const riconosciuta = !!(r && (id !== null || r.ok === true));
  const cassa = r && typeof r.cash_disponibile_eur === 'number'
    && isFinite(r.cash_disponibile_eur) ? r.cash_disponibile_eur : null;
  const nota = r && typeof r.cash_note === 'string' && r.cash_note.trim()
    ? r.cash_note.trim() : null;
  const guardia = r && typeof r.guardia_note === 'string' && r.guardia_note.trim()
    ? r.guardia_note.trim() : null;

  let stato: StatoCassa;
  if (cassa != null) stato = nota ? 'aggiornata-con-nota' : 'aggiornata';
  else if (nota) stato = 'non-aggiornata';
  else stato = 'muta';

  return { tradeId: id ?? '?', riconosciuta, stato, cassa, nota, guardia };
}

/** Un 4xx dichiara il rifiuto; un 5xx può arrivare anche dopo il commit. */
export function scritturaRifiutata(err: unknown): boolean {
  const e = err as { response?: { status?: number } } | null | undefined;
  return !!(e && e.response && typeof e.response.status === 'number'
    && e.response.status >= 400 && e.response.status < 500);
}

// ── IL CANALE CASSA (F7 «il libretto», F14 lo storico) ──────────────────────
// GEMELLO di esitoScrittura, non una sua generalizzazione. Quella funzione
// legge `trade_id`, porta `guardia_note` ed e' provata per differenza su
// 30.990 input ostili (§9-unquadragies-septies): rimetterla in gioco per un
// campo che si chiama `movement_id` costerebbe piu' di quanto vale. Lo stato
// e la sua grammatica — StatoCassa — sono invece gli STESSI, apposta: al PM
// «cassa non aggiornata» deve leggersi uguale che venga da un trade o da un
// versamento.

export interface RispostaMovimento {
  ok?: boolean;
  movement_id?: number | string | null;
  tipo?: string;
  importo_eur?: number;
  cash_disponibile_eur?: number | null;
  cash_note?: string | null;
}

export interface EsitoMovimento {
  movimentoId: string;
  /** false quando la risposta non porta ne' ok ne' movement_id: non si
   *  afferma nulla, ne' che sia scritto ne' che non lo sia */
  riconosciuta: boolean;
  stato: StatoCassa;
  cassa: number | null;
  /** la nota del backend, VERBATIM */
  nota: string | null;
}

export function esitoMovimento(r: RispostaMovimento | null | undefined): EsitoMovimento {
  const id = r && r.movement_id != null ? String(r.movement_id) : null;
  const riconosciuta = !!(r && (id !== null || r.ok === true));
  const cassa = r && typeof r.cash_disponibile_eur === 'number'
    && isFinite(r.cash_disponibile_eur) ? r.cash_disponibile_eur : null;
  const nota = r && typeof r.cash_note === 'string' && r.cash_note.trim()
    ? r.cash_note.trim() : null;

  let stato: StatoCassa;
  if (cassa != null) stato = nota ? 'aggiornata-con-nota' : 'aggiornata';
  else if (nota) stato = 'non-aggiornata';
  else stato = 'muta';

  return { movimentoId: id ?? '?', riconosciuta, stato, cassa, nota };
}

/** Quale delle due guardie scavalcabili ha parlato. `ignota` NON e' un errore:
 *  e' il caso onesto in cui il backend rifiuta con un prefisso che questa
 *  versione del frontend non conosce — allora si mostra il motivo verbatim e
 *  ci si astiene dal riassumerlo. */
export type GuardiaCassa = 'duplicato' | 'soglia' | 'ignota';

export interface RifiutoCassa {
  /** il `detail` del backend, VERBATIM e SEMPRE una stringa */
  motivo: string;
  /** vero SOLO se il backend dice lui stesso come si passa (`conferma=true`).
   *  E' questo — non il prefisso — a decidere se il bottone di conferma
   *  compare: un rifiuto che non spiega come passare non e' scavalcabile. */
  rimediabile: boolean;
  quale: GuardiaCassa;
  /** lo status HTTP, per non affermare piu' di quanto si sappia */
  status: number;
  /** ⚠ VERO SOLO dove il backend GARANTISCE che non c'e' stata scrittura.
   *  Non basta che il server abbia risposto: garantiscono zero scritture il
   *  401 (nemmeno entra nell'endpoint) e il 422 (tutte le guardie di
   *  `log_cash_movement` stanno PRIMA dell'INSERT, memory_db.py:930-973).
   *  NON garantisce niente un 500 successivo all'INSERT — l'endpoint non e'
   *  avvolto in `_err500` e un'eccezione fra la scrittura e il `return`
   *  (es. `_safe_print` che cattura solo OSError, bellomberg_api.py:53)
   *  uscirebbe come 500 a riga gia' committata. E nemmeno un 503, che sulla
   *  rotta ha tre origini di cui una POST-commit (`sqlite3.Error` catturato a
   *  bellomberg_api.py:1795, che copre anche il `close()` di `_conn`).
   *  Perche' conta: dire «non e' stato scritto» su una riga scritta manda il
   *  PM a riprovare, il secondo tentativo prende GUARDIA DUPLICATO, e il
   *  bottone «Confermo» — che quella guardia la spegne — fa entrare il
   *  doppio movimento. Due confutatori l'hanno trovato per strade diverse. */
  nonScritto: boolean;
}

/** Legge un rifiuto di `POST /cash/movement`. Ritorna `null` quando il server
 *  NON ha risposto (timeout, rete caduta): li' non si sa se la scrittura sia
 *  avvenuta, e affermare il contrario spinge al doppio invio. */
export function leggiRifiuto(err: unknown): RifiutoCassa | null {
  if (!scritturaRifiutata(err)) return null;
  const e = err as { response?: { status?: number; data?: { detail?: unknown; code?: unknown } } };
  const status = typeof e?.response?.status === 'number' ? e.response.status : 0;
  const d = e?.response?.data?.detail;
  // ⚠ il `detail` di un 422 di pydantic e' un ARRAY di oggetti, non una
  // stringa (succede se `importo_eur` non arriva numerico). Reso com'e'
  // farebbe esplodere React su "Objects are not valid as a React child";
  // reso come stringa vuota nasconderebbe il motivo. Si serializza.
  const motivo = typeof d === 'string' ? d
    : d == null ? ''
      : (() => { try { return JSON.stringify(d); } catch { return String(d); } })();
  const code = e?.response?.data?.code;
  const coded = e?.response?.data != null && Object.prototype.hasOwnProperty.call(e.response.data, 'code');
  const quale: GuardiaCassa = coded
    ? code === 'cash_duplicate' ? 'duplicato' : code === 'cash_threshold' ? 'soglia' : 'ignota'
    : /^GUARDIA DUPLICATO\b/.test(motivo) ? 'duplicato'
      : /^GUARDIA IMPORTO\b/.test(motivo) ? 'soglia' : 'ignota';
  // Codes are language-independent. Legacy prose applies only when no code was sent.
  const rimediabile = coded ? status === 422 && quale !== 'ignota' : /conferma\s*=\s*true/i.test(motivo);
  return { motivo, rimediabile, quale, status, nonScritto: status === 422 || status === 401 };
}

/** La data valuta del movimento. Vuota = «oggi», e lo decide il BACKEND: qui
 *  non si spedisce una data e basta.
 *  ⚠ Tre controlli, non uno:
 *  1. la FORMA — YYYY-MM-DD e nient'altro (un `…T23:59:59` il backend lo
 *     normalizzerebbe zitto, e un orario in un campo che dice «data» e' un
 *     malinteso che non si vede);
 *  2. il CALENDARIO — `2026-02-31` ha la forma giusta e non e' un giorno. Si
 *     ricostruisce e si confrontano le tre componenti: `new Date` su una
 *     stringa fuori calendario non e' affidabile allo stesso modo ovunque;
 *  3. il DOMINIO — `2062-08-20` passa i primi due e finisce a registro 36
 *     anni avanti, in un giorno che il TWR non sa trattare. L'importo e'
 *     protetto contro esattamente questa classe di refuso (`leggiNumero`
 *     rifiuta l'ambiguo), la data non lo era. `oggi` va passato dal
 *     chiamante: questo modulo non legge l'orologio (resta provabile).
 *     La finestra a +30 giorni lascia passare un bonifico con valuta futura
 *     e ferma una trasposizione di cifre. */
export const DATA_MIN = '2000-01-01';
export const GIORNI_AVANTI_MAX = 30;

export function leggiDataValuta(s: string, oggi?: string): LetturaData | null {
  const t = (s || '').trim();
  if (!t) return null;                        // vuoto: legittimo, decide il backend
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t);
  if (!m) return { ok: false, motivo: tr('trade.date_iso_required') };
  const [a, me, g] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const d = new Date(Date.UTC(a, me - 1, g));
  if (d.getUTCFullYear() !== a || d.getUTCMonth() !== me - 1 || d.getUTCDate() !== g) {
    return { ok: false, motivo: tr('trade.date_invalid_calendar', {a: t}) };
  }
  if (t < DATA_MIN) {
    return { ok: false, motivo: tr('trade.date_before_min', {a: t, b: DATA_MIN}) };
  }
  if (oggi) {
    const lim = new Date(Date.UTC(
      Number(oggi.slice(0, 4)), Number(oggi.slice(5, 7)) - 1,
      Number(oggi.slice(8, 10)) + GIORNI_AVANTI_MAX));
    const limIso = lim.toISOString().slice(0, 10);
    if (t > limIso) {
      return { ok: false,
        motivo: tr('trade.date_future_limit', {a: t, b: GIORNI_AVANTI_MAX, c: oggi}) };
    }
  }
  return { ok: true, iso: t };
}

export type LetturaData =
  | { ok: true; iso: string }
  | { ok: false; motivo: string };

// ── AVVISI ──────────────────────────────────────────────────────────────────

export interface Discordanza {
  valutaBook: string;
  valutaScelta: string;
  /** vero solo per la coppia che differisce di 100: la nota su GBX/GBP non si
   *  incolla a un mismatch USD/EUR, dove sposterebbe l'attenzione su un
   *  fattore inesistente */
  fattoreCento: boolean;
}

export function valutaDiscorde(o: Ordine, pos: Position | undefined): Discordanza | null {
  if (!pos) return null;
  const a = (pos.valuta || '').toUpperCase();
  const b = (o.valuta || '').toUpperCase();
  if (!a || !b || a === b) return null;
  // su DIVIDEND la valuta e' EUR per costruzione (incasso in euro per azione):
  // segnalarlo su ogni posizione non-EUR sarebbe l'avviso che si impara a saltare
  if (o.action === 'DIVIDEND') return null;
  const cento = (a === 'GBX' && b === 'GBP') || (a === 'GBP' && b === 'GBX');
  return { valutaBook: a, valutaScelta: b, fattoreCento: cento };
}

/** Quante volte questo ordine vale la taglia mediana. null se non calcolabile. */
export function controMediana(eur: number | null, mediana: number | null): number | null {
  if (eur == null || mediana == null || mediana <= 0) return null;
  return eur / mediana;
}

// ── NUMERI SCRITTI A MANO ───────────────────────────────────────────────────
// ⚠ MISURATO SULL'APP VIVA, non dedotto: il browser dell'app gira in `en-GB`,
// e su un `<input type="number">` il separatore decimale sbagliato non viene
// RIFIUTATO, viene CANCELLATO dal carattere digitato. Battendo `158,50` il
// campo vale `15850`, `validity.badInput` e' **false**, e il POST parte con un
// numero CENTO VOLTE piu' grande. Su 0,4 diventa 4. La pagina non ha annullo.
// Quindi: input di testo, normalizzazione esplicita, e l'ambiguo si RIFIUTA.

export type LetturaNumero =
  | { ok: true; valore: number }
  | { ok: false; motivo: string };

export function leggiNumero(s: string, lingua: Lingua = linguaCorrente(), linguaMessaggio: Lingua = lingua): LetturaNumero | null {
  const r = analizzaNumero(s, lingua, linguaMessaggio);
  if (r == null || !r.ok) return r;
  if (r.valore <= 0) return { ok: false, motivo: traduci(linguaMessaggio, 'numeri.maggiore_zero') };
  return r;
}

/** Per i campi che portano un SEGNO (gli Outcome di F10): una decisione può
 *  chiudere in perdita e lo zero è un pareggio, non un errore — il dominio
 *  «maggiore di zero» vale per quantità e prezzi, non qui. Normalizzazione e
 *  rifiuto dell'ambiguo restano quelli di leggiNumero. */
export function leggiNumeroConSegno(s: string, lingua: Lingua = linguaCorrente(), linguaMessaggio: Lingua = lingua): LetturaNumero | null {
  const t = (s || '').trim();
  if (!t) return null;                                   // campo vuoto: non e' un errore
  const negativo = t[0] === '-';
  const corpo = negativo || t[0] === '+' ? t.slice(1) : t;
  const r = analizzaNumero(corpo, lingua, linguaMessaggio);
  if (r == null) return { ok: false, motivo: traduci(linguaMessaggio, 'numeri.manca_dopo_segno') };
  if (!r.ok) return r;
  return { ok: true, valore: negativo ? -r.valore : r.valore };
}

/**
 * Il prezzo da precompilare cliccando una riga del book.
 * ⚠ `prezzo_live` può portare rumore float32 di yfinance. Copiarlo senza
 * formattazione nel campo trasferisce la coda numerica nel prezzo salvato.
 * Si limita la rappresentazione a quattro decimali, senza presentare il
 * rumore binario come precisione della quotazione.
 * Il decimale segue la lingua del modulo (virgola IT, punto EN): una
 * quotazione a tre decimali deve essere riletta senza ambiguità. La prova
 * verifica il ciclo scrittura/lettura in entrambe le lingue.
 */
export function prezzoDaBook(p: Position, lingua: Lingua = linguaCorrente()): string {
  const v = p.prezzo_live;
  if (typeof v !== 'number' || !isFinite(v) || v <= 0) return '';
  return scriviNumero(v, lingua, 4);
}
