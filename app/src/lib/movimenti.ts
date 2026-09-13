import { t as tr } from '../i18n/t.js';
import { linguaCorrente, localeDi } from '../i18n/lingua.js';
// ============================================================
// F14 MOVIMENTI — le derivazioni, e SOLO quelle (Opus 5, 27/07)
//
// Funzioni pure su un array di Trade: niente React, niente fetch,
// niente stato. Stanno qui e non dentro i componenti per due motivi
// misurati: le tre viste condividono lo stesso ordinamento (se lo
// rifacessero per conto loro divergerebbero), e questi sono i punti
// dove la pagina di oggi sbaglia — averli in un posto solo li rende
// leggibili senza montare React.
//
// ⚠️ I conteggi citati nei commenti sotto ("69 righe", "27 ticker") sono le
// misure del 27/07 e restano come DATA di quella misura, non come stato di oggi:
// rimisurato il 21/08 sul DB vero sono **75 trade su 29 ticker**. Un numero in
// un commento invecchia; quello che non deve invecchiare e' il MOTIVO per cui
// e' stato scritto, ed e' quello che i commenti spiegano.
//
// REGOLA CHE ATTRAVERSA TUTTO IL FILE: il portafoglio ha tre valute
// (misurato 27/07: EUR 45 · USD 14 · GBX 10). Un controvalore TOTALE
// non esiste senza un cambio, e il cambio qui non si fa. Ogni
// confronto di grandezza avviene DENTRO una valuta sola.
// ============================================================

import type { MovimentoCassa } from '@/lib/api';

export interface Trade {
  ticker: string;
  action: string;
  quantita: number;
  prezzo: number;
  valuta: string;
  data: string;                      // ISO, col timestamp: 69 righe su 69 ce l'hanno
  note?: string | null;
  pm_rationale?: string | null;
  linked_decision_id?: number | null;
  link_origin?: 'explicit' | 'none' | 'unknown' | null;
  ora_convenzionale?: number | boolean | null;
  // Campi ora consegnati dal registro; restano opzionali per dichiarare
  // payload legacy o incompleti senza inventare ID, tempi o realizzati.
  id?: number | null;
  created_at?: string | null;
  realized_eur?: number | null;
  realized_local?: number | null;
}

export type Azione = 'BUY' | 'ADD' | 'TRIM' | 'DIVIDEND' | string;

/** Un ingresso alza la posizione, un'uscita la abbassa. Il dividendo non fa ne' l'uno ne' l'altro. */
export const entra = (a: Azione) => a === 'BUY' || a === 'ADD';
export const esce = (a: Azione) => a === 'TRIM' || a === 'SELL';

/**
 * Il segno di un realizzato, con lo ZERO come terzo caso.
 * Prima `>= 0` dipingeva di verde un'uscita chiusa esattamente in pari e il
 * riepilogo la contava fra le perdite: due letture opposte dello stesso
 * numero esatto (review 27/07). Zero non e' un guadagno: e' zero.
 */
export const segnoPL = (v: number): 'su' | 'giu' | 'pari' =>
  (v > 0 ? 'su' : v < 0 ? 'giu' : 'pari');

// ── tempo ───────────────────────────────────────────────────────
/** `2026-06-04T13:58:09` -> `04/06/26`. Torna '—' su tutto cio' che non e' una data.
 *
 * ⚠️ Il controllo era `iso.length >= 10`, cioe' contava i CARATTERI: con
 * `'non-una-data'` usciva in cella **`da/na/n-`**, che ha la forma di una data
 * e non lo e'. E dal 21/08 la stessa riga finisce sotto un separatore che
 * dice «DATA NON LEGGIBILE»: due letture opposte dello stesso dato nella
 * stessa schermata. Ora il criterio e' lo STESSO di `chiaveMese`. */
const GIORNO_ISO = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])/;
export const ggmmaa = (iso?: string | null) =>
  (iso && GIORNO_ISO.test(iso)
    ? linguaCorrente() === 'en'
      ? `${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(2, 4)}`
      : `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(2, 4)}` : '—');

/**
 * L'ora, che oggi la pagina BUTTA con `.slice(0,10)` pur avendola su
 * 69 righe su 69. `null` se il backend ha mandato la sola data: e'
 * un'assenza vera e va resa come tale, non come `00:00:00`.
 */
export const oraDi = (iso?: string | null) =>
  iso && iso.length > 10 ? iso.slice(11, 19) : null;

/** L'ora che l'importatore scrive quando l'estratto del broker non ne porta una. */
export const ORA_SEGNAPOSTO = '12:00:00';

/**
 * Quante righe portano l'ora SEGNAPOSTO.
 *
 * ⚠️ Rendere `12:00:00` come se fosse un orario misurato e' una precisione
 * inventata. Verificato il 27/07 sul DB: **35 righe su 69** — rimisurato il
 * 21/08: **36 su 75**, il libro e' cresciuto — hanno esattamente
 * quell'ora, e sono le stesse 35 dell'estratto del broker — perche'
 * `import_user_trades.py:121` fa `dt.replace(hour=12, minute=0, second=0)`
 * quando l'estratto porta solo la data (review 27/07, MEDIA).
 *
 * Dal 12/09 il payload distingue la convenzione con ora_convenzionale.
 * Il conteggio usa solo il flag esplicito; una riga legacy NULL resta ignota.
 */
export function contaOreSegnaposto(trades: Trade[]): number {
  return trades.filter(t => t.ora_convenzionale === 1 || t.ora_convenzionale === true).length;
}

/** Un mezzogiorno misurato resta tale; per le righe legacy non si deduce la provenienza. */
export function oraTrade(t: Trade): string {
  const ora = oraDi(t.data);
  if (!ora) return tr('movements.timeUnknown');
  if (t.ora_convenzionale === 1 || t.ora_convenzionale === true) return tr('movements.conventionalTime', {a: ora});
  if (t.ora_convenzionale == null) return tr('movements.timeOriginUnknown', {a: ora});
  return ora;
}

export function legameMovimento(t: Trade): string {
  if (t.link_origin === 'none') return tr('movements.manual');
  if (t.link_origin === 'explicit' && t.linked_decision_id != null) return tr('movements.explicitDecision', {a: t.linked_decision_id});
  if (t.linked_decision_id != null) return tr('movements.unknownDecisionOrigin', {a: t.linked_decision_id});
  return tr('movements.linkUnknown');
}

const nomeMese = (month: number, breve = false) => new Intl.DateTimeFormat(localeDi(linguaCorrente()),
  { month: breve ? 'short' : 'long' }).format(new Date(2000, month, 1)).toLocaleUpperCase(localeDi(linguaCorrente()));

// ── testo ───────────────────────────────────────────────────────
/**
 * I DUE testi di una riga, separati.
 *
 * Oggi `MovementsPage.tsx:83` fa `pm_rationale || note`: dove esistono
 * entrambi ne mostra UNO. Misurato il 27/07: una riga di vendita sintetica
 * ha `note` da 306 caratteri E `pm_rationale` da 52, e la pagina butta
 * i 306. Qui tornano tutti e due e decide la vista come renderli.
 */
export function testiDi(t: Trade): { rationale: string; nota: string; vuoto: boolean } {
  const rationale = (t.pm_rationale || '').trim();
  const nota = (t.note || '').trim();
  return { rationale, nota, vuoto: !rationale && !nota };
}

/** Controvalore nella valuta NATIVA del trade. `null` se manca un fattore: mai 0 al posto di n.d. */
export function controvalore(t: Trade): number | null {
  if (t.quantita == null || t.prezzo == null) return null;
  if (!isFinite(t.quantita) || !isFinite(t.prezzo)) return null;
  return Math.abs(t.quantita * t.prezzo);
}

// ── ordinamento ─────────────────────────────────────────────────
/**
 * Le righe in ordine di DATA VERA, dalla piu' recente.
 *
 * Il backend le manda con `ORDER BY id DESC` (`memory_db.py:1989`),
 * cioe' per ordine di INSERIMENTO. Misurato il 27/07: 14 coppie
 * adiacenti invertite e 34 righe su 69 in posizione diversa da quella
 * cronologica — su un import di broker le due cose non coincidono.
 * Si corregge qui perche' il `data` arriva completo su 69 righe su 69.
 * Pareggio sciolto con `id` quando c'e' (oggi non arriva: vedi cancello).
 */
export function perDataDesc(trades: Trade[]): Trade[] {
  return trades.slice().sort((a, b) => {
    const d = (b.data || '').localeCompare(a.data || '');
    if (d !== 0) return d;
    return (b.id ?? 0) - (a.id ?? 0);
  });
}

// ── il registro unico: titoli e cassa sulla stessa linea del tempo ──
/**
 * Una riga del registro: o una mossa sui titoli, o un flusso di cassa.
 *
 * ⚠️ UNIONE DISCRIMINATA, non un `MovimentoCassa` travestito da `Trade`.
 * Un versamento non ha ticker, quantita', prezzo ne' realizzato: infilarlo
 * dentro un Trade lo farebbe entrare — in silenzio — in `costruisciCorsie`
 * (una corsia senza titolo), in `contaRealizzato` (un flusso contato fra le
 * uscite) e in `statoCancello` (che deciderebbe la FORMA del payload dei
 * trade guardando una riga che dai trade non viene). Tutte le derivazioni
 * sui titoli restano su `Trade[]` e la cassa non le vede mai.
 */
export type RigaRegistro =
  | { specie: 'titolo'; quando: string; t: Trade }
  | { specie: 'cassa'; quando: string; m: MovimentoCassa };

const idDiRiga = (r: RigaRegistro): number =>
  (r.specie === 'cassa' ? r.m.id : r.t.id) ?? 0;

/** Una data su cui si puo' ORDINARE. Stesso criterio di `arcoDi`: se `Date.parse`
 *  non la legge, non e' una data — e non deve decidere una posizione. */
const dataLeggibile = (s: string) => !!s && isFinite(Date.parse(s));

/** La chiave di mese di una riga, `''` se la data non e' un mese vero.
 *  ⚠️ Esiste come funzione ESPORTATA perche' `raggruppaPerMese` e il componente
 *  che rende i separatori DEVONO usare lo stesso criterio: quando divergevano,
 *  una riga con data illeggibile veniva RESA sotto un separatore che non la
 *  contava, e quel separatore diceva un numero piu' piccolo di quante righe
 *  aveva sotto. Misurato: 4 righe rese, somma dei mesi 2. */
export const chiaveMese = (quando: string): string => {
  const k = (quando || '').slice(0, 7);
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(k) ? k : '';
};

/** L'etichetta del gruppo «data non leggibile».
 *  ⚠️ Prima quel gruppo non esisteva e la data spazzatura passava il filtro:
 *  con `data: 'non-una-data'` la chiave diventava `'non-una'`, che e' truthy, e
 *  l'etichetta usciva letteralmente **«undefined non-»** — cioe' `undefined` a
 *  schermo, in una pagina che dichiara ogni buco. Riprodotto in node. */
export const MESE_IGNOTO = 'DATA NON LEGGIBILE'; // Identificatore storico di compatibilità.

export type VersoCassa = 'dentro' | 'fuori' | 'ignoto';
/** Il verso di un movimento. `ignoto` NON e' un caso di scuola travestito da
 *  prudenza: `else = prelievo` faceva diventare un tipo sconosciuto un'uscita
 *  col segno meno, sottratta dal netto, in silenzio (regola 14/07). Oggi lo
 *  schema lo impedisce (`CHECK(type IN ('DEPOSIT','WITHDRAWAL'))`,
 *  `memory_db.py:334`) ma il payload non e' lo schema. */
export const versoDi = (tipo: string): VersoCassa =>
  (tipo === 'DEPOSIT' ? 'dentro' : tipo === 'WITHDRAWAL' ? 'fuori' : 'ignoto');

/**
 * Titoli e cassa fusi, piu' recenti prima.
 *
 * ⚠️ I due `quando` NON hanno la stessa precisione, ed e' il motivo per cui
 * il pareggio si scioglie in modo dichiarato invece che per caso: un trade
 * porta l'ISO col timestamp (`2026-08-12T17:48:53`), un movimento di cassa
 * porta il SOLO giorno — il backend normalizza alla data valuta prima
 * dell'INSERT (`memory_db.py:1087`), quindi un'ora non ce l'ha proprio.
 * Per confronto di stringhe `'2026-08-12' < '2026-08-12T00:00:00'`, quindi a
 * parita' di giorno la riga di cassa cade SOTTO i trade di quel giorno. Non e'
 * un caso da sciogliere meglio: il movimento non SA a che ora e' avvenuto, e
 * dargli una posizione dentro la giornata sarebbe una precisione inventata.
 *
 * ⚠️ Una data ILLEGGIBILE non ordina: va in fondo, non in cima. Ordinando sulla
 * stringa grezza, `'non-una-data'` batteva `'2026-08-13...'` (la 'n' viene dopo
 * il '2') e una riga di cui non sappiamo QUANDO sia avvenuta si piazzava in
 * testa a un registro la cui intestazione dichiara «PER DATA VERA». Stesso
 * criterio di `arcoDi`, che una data illeggibile la butta fuori dall'arco.
 *
 * ⚠️ LIMITE DICHIARATO, non curato qui: a parita' di istante ESATTO fra due
 * trade lo spareggio cade su `id`, che `GET /trades` oggi non consegna
 * (`?? 0` per entrambi) — quindi vince l'ordine del payload, cioe'
 * `ORDER BY id DESC` (`memory_db.py:1989`). Sul libro vero sono 9 gruppi di
 * pari-istante per 30 righe, 11 delle quali sullo stesso `2026-02-01T12:00:00`
 * dell'import iniziale. E' il comportamento che c'era gia' in `perDataDesc`,
 * non una regressione: cambiarlo riordinerebbe 30 righe sotto gli occhi del PM,
 * e la decisione e' sua.
 */
export function fondiRegistro(trades: Trade[], cassa: MovimentoCassa[]): RigaRegistro[] {
  const out: RigaRegistro[] = [];
  for (const t of trades) out.push({ specie: 'titolo', quando: t.data || '', t });
  for (const m of cassa) out.push({ specie: 'cassa', quando: m.date || '', m });
  const ord = (r: RigaRegistro) => (dataLeggibile(r.quando) ? r.quando : '');
  return out.sort((a, b) => {
    const d = ord(b).localeCompare(ord(a));
    if (d !== 0) return d;
    // stesso istante esatto e specie diverse: prima il titolo, che porta
    // l'ora vera. Fra pari specie decide l'id (sulla cassa c'e' sempre,
    // sui trade oggi no: e' una delle 4 colonne al cancello).
    if (a.specie !== b.specie) return a.specie === 'titolo' ? -1 : 1;
    return idDiRiga(b) - idDiRiga(a);
  });
}

/** Il ticker di una riga, `null` sulla cassa: un flusso non ha un titolo. */
export const tickerDiRiga = (r: RigaRegistro): string | null =>
  (r.specie === 'titolo' ? r.t.ticker : null);

/**
 * I testi di una riga qualunque. Sulla cassa la causale e' una parola del PM
 * come le altre, e va nel posto della NOTA: non e' un `pm_rationale`, che su
 * un trade e' il motivo della mossa.
 */
export function testiDiRiga(r: RigaRegistro): { rationale: string; nota: string; vuoto: boolean } {
  if (r.specie === 'titolo') return testiDi(r.t);
  const nota = (r.m.note || '').trim();
  return { rationale: '', nota, vuoto: !nota };
}

export interface Flussi {
  versato: number;
  prelevato: number;
  netto: number;
  /** righe che NON entrano nei tre numeri sopra — importo illeggibile oppure
   *  tipo sconosciuto. Non contate come zero e non dedotte: dichiarate. */
  ignoti: number;
}

/**
 * Versato, prelevato e netto dei flussi ESTERNI.
 *
 * ⚠️ Il netto NON e' il saldo di cassa e non lo diventa mai da qui: anche i
 * trade muovono la cassa, e in `GET /cash/movements` i trade non ci sono.
 * E' il numero che sulla rosa del 20/08, quando lo si chiamava «saldo», dava una
 * cifra incompatibile col versamento appena registrato (MASTER
 * §9-unquadragies-quinquiesdecies).
 * Un importo illeggibile non entra come 0: si conta a parte (regola 14/07).
 */
export function contaFlussi(cassa: MovimentoCassa[]): Flussi {
  let versato = 0, prelevato = 0, ignoti = 0;
  for (const m of cassa) {
    const v = m.amount_eur;
    if (typeof v !== 'number' || !isFinite(v)) { ignoti++; continue; }
    const verso = versoDi(m.type);
    // ⚠️ `else = prelievo` era un fallback zitto: un tipo sconosciuto diventava
    // un'uscita col segno meno e usciva dal netto senza che nulla lo dicesse.
    if (verso === 'dentro') versato += Math.abs(v);
    else if (verso === 'fuori') prelevato += Math.abs(v);
    else ignoti++;
  }
  return { versato, prelevato, netto: versato - prelevato, ignoti };
}

// ── mesi ────────────────────────────────────────────────────────
export interface Mese {
  chiave: string;                    // '2026-06'
  etichetta: string;                 // 'GIUGNO 2026'
  n: number;
  nTicker: number;
  perAzione: Record<string, number>;
  /** quante delle `n` righe del mese sono flussi di cassa */
  nCassa: number;
}

/**
 * La cadenza, CONTATA e non sommata: quante mosse per mese, su quanti
 * titoli, divise per verbo. Non si sommano controvalori perche' le
 * valute sono tre — un totale mensile sarebbe un numero inventato.
 *
 * ⚠️ Conta le righe RESE, cassa compresa: un separatore che dice
 * «6 MOVIMENTI» sopra quattro trade e due flussi mentirebbe su cio' che ha
 * sotto. Il conteggio dei TITOLI resta sui soli trade — un flusso non porta
 * un titolo e non puo' gonfiare quel numero.
 *
 * ⚠️ NESSUNA riga resta fuori. Prima le righe con data non leggibile venivano
 * saltate (`if (!k) continue`) ma il componente le RENDEVA lo stesso, sotto il
 * separatore precedente: quel separatore dichiarava meno righe di quante ne
 * avesse sotto. Ora finiscono in un gruppo loro, `chiave: ''`, etichettato
 * `DATA NON LEGGIBILE` e messo in fondo — dove `fondiRegistro` le ordina.
 */
export function raggruppaPerMese(righe: RigaRegistro[]): Mese[] {
  const m = new Map<string, {
    n: number; tk: Set<string>; az: Record<string, number>; cassa: number;
  }>();
  for (const r of righe) {
    const k = chiaveMese(r.quando);
    let g = m.get(k);
    if (!g) { g = { n: 0, tk: new Set(), az: {}, cassa: 0 }; m.set(k, g); }
    g.n++;
    if (r.specie === 'titolo') {
      g.tk.add(r.t.ticker);
      g.az[r.t.action] = (g.az[r.t.action] || 0) + 1;
    } else {
      g.cassa++;
    }
  }
  return [...m.entries()]
    // il gruppo senza data va in FONDO, non in cima: '' perderebbe ogni
    // confronto decrescente e finirebbe ultimo — ma lo si scrive, non lo si
    // eredita da un caso limite di `localeCompare`
    .sort((a, b) => (a[0] === '' ? 1 : b[0] === '' ? -1 : b[0].localeCompare(a[0])))
    .map(([chiave, g]) => ({
      chiave,
      etichetta: chiave === ''
        ? tr('movements.unknownDate')
        : `${nomeMese(Number(chiave.slice(5, 7)) - 1)} ${chiave.slice(0, 4)}`,
      n: g.n,
      nTicker: g.tk.size,
      perAzione: g.az,
      nCassa: g.cassa,
    }));
}

// ── arco temporale ──────────────────────────────────────────────
export interface Arco { da: string; a: string; giorni: number; }

/**
 * L'arco si costruisce SOLO sulle date interpretabili.
 *
 * Prima si sceglievano gli estremi per confronto di STRINGHE e si faceva
 * `Date.parse` senza guardare: una sola riga con `data` vuota o non ISO
 * vinceva come minimo, l'arco diventava `NaN`, e a valle `frazione`
 * tornava 0 per OGNI riga — cioe' 69 segni accatastati sul bordo sinistro
 * come se fossero avvenuti nello stesso istante, senza che niente lo
 * dicesse. Uno zero muto travestito da grafico (review 27/07, BASSA).
 * Ora una data illeggibile e' semplicemente fuori dall'arco, e se non ne
 * resta nessuna l'arco e' `null` — che il chiamante rende come "n.d.".
 */
export function arcoDi(trades: Trade[]): Arco | null {
  const validi = trades.filter(t => t.data && isFinite(Date.parse(t.data)));
  if (!validi.length) return null;
  let da = validi[0].data, a = validi[0].data;
  for (const t of validi) {
    if (t.data < da) da = t.data;
    if (t.data > a) a = t.data;
  }
  const ms = Date.parse(a) - Date.parse(da);
  if (!isFinite(ms)) return null;
  return { da, a, giorni: Math.max(1, Math.round(ms / 86400000)) };
}

/**
 * Dove cade una data dentro l'arco, 0..1. Fuori arco resta agli estremi, non
 * esce dal grafico. Una data ILLEGGIBILE tornava `NaN`, che finisce dentro
 * uno `style` senza far esplodere niente: il segno semplicemente spariva o si
 * piazzava a caso. Ora torna 0 e il chiamante lo tratta come "inizio arco"
 * (review 27/07, BASSA).
 */
export function frazione(iso: string, arco: Arco): number {
  const t0 = Date.parse(arco.da), t1 = Date.parse(arco.a), t = Date.parse(iso);
  if (!isFinite(t0) || !isFinite(t1) || !isFinite(t) || t1 === t0) return 0;
  const f = (t - t0) / (t1 - t0);
  return Math.min(1, Math.max(0, f));
}

/** Le tacche dei mesi dentro l'arco, per le guide verticali delle SCIE. */
export function mesiDellArco(arco: Arco): { nome: string; f: number }[] {
  const out: { nome: string; f: number }[] = [];
  const d = new Date(arco.da);
  d.setDate(1); d.setHours(0, 0, 0, 0);
  const fine = new Date(arco.a);
  let giri = 0;
  while (d <= fine && giri++ < 240) {                 // paracadute: mai un while infinito in un render
    const iso = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
    out.push({ nome: nomeMese(d.getMonth(), true), f: frazione(iso, arco) });
    d.setMonth(d.getMonth() + 1);
  }
  return out;
}

// ── corsie ──────────────────────────────────────────────────────
export interface Mossa {
  trade: Trade;
  f: number;                         // posizione nell'arco, 0..1
  /** peso relativo alle mosse della corsia NELLA STESSA VALUTA, 0..1 */
  rel: number;
  ctrl: number | null;               // controvalore in valuta nativa
}
export interface Corsia {
  ticker: string;
  /** la valuta della prima mossa: resta per compatibilita' di resa */
  valuta: string;
  /** TUTTE le valute presenti nella corsia, in ordine di comparsa. Piu' di una = corsia mista */
  valute: string[];
  n: number;
  primo: string;
  ultimo: string;
  mosse: Mossa[];
}

/**
 * Una corsia per ticker, ordinate per PRIMO movimento: cosi' il book si
 * legge mentre si costruisce, da sinistra a destra.
 *
 * `rel` e' il peso rispetto alla mossa piu' grossa della corsia **nella
 * stessa valuta**.
 *
 * ⚠️ La prima versione divideva per il massimo dell'INTERA corsia, dando per
 * scontato che una corsia avesse una valuta sola. **E' falso sui dati veri**:
 * `MSTR` ha 5 righe su DUE valute (3 in USD, 2 in EUR — verificato in DB, ed
 * e' l'unico caso su 27 ticker), quindi i due segni in euro venivano misurati
 * contro un massimo in dollari. Cioe' esattamente il confronto fra valute che
 * l'intestazione della pagina dichiara di NON fare (review 27/07, ALTA).
 * Ora ogni valuta ha il suo metro dentro la corsia, e la corsia dichiara
 * tutte le valute che contiene.
 */
export function costruisciCorsie(trades: Trade[], arco: Arco): Corsia[] {
  const per = new Map<string, Trade[]>();
  for (const t of trades) {
    const l = per.get(t.ticker);
    if (l) l.push(t); else per.set(t.ticker, [t]);
  }
  const out: Corsia[] = [];
  for (const [ticker, righe] of per) {
    const cron = righe.slice().sort((a, b) => (a.data || '').localeCompare(b.data || ''));
    // un metro PER VALUTA, non uno per corsia
    const maxPer = new Map<string, number>();
    const valute: string[] = [];
    for (const t of cron) {
      const v = t.valuta || '?';
      if (!valute.includes(v)) valute.push(v);
      const c = controvalore(t);
      if (c != null && c > 0) maxPer.set(v, Math.max(maxPer.get(v) ?? 0, c));
    }
    out.push({
      ticker,
      valuta: cron[0]?.valuta || '?',
      valute,
      n: cron.length,
      primo: cron[0]?.data || '',
      ultimo: cron[cron.length - 1]?.data || '',
      mosse: cron.map(t => {
        const ctrl = controvalore(t);
        const max = maxPer.get(t.valuta || '?') ?? 0;
        return {
          trade: t,
          f: frazione(t.data, arco),
          // niente controvalore o metro a zero -> peso neutro, che la vista
          // rende come "n.d." e non come 0%: mai una divisione per zero che
          // sputa NaN dentro uno style
          rel: ctrl != null && max > 0 ? Math.min(1, ctrl / max) : 0,
          ctrl,
        };
      }),
    });
  }
  // Ordine DETERMINISTICO, e il pareggio non e' un dettaglio: 19 righe
  // portano lo stesso `primo` (2026-02-01T12:00:00, l'import iniziale).
  // Senza spareggio quelle corsie prendono l'ordine in cui arriva il
  // payload — cioe' `ORDER BY id DESC`, che abbiamo appena CHIESTO al
  // backend di cambiare: il grafico si riordinerebbe da solo il giorno in
  // cui accettano. A parita' di data viene prima la corsia piu' battuta,
  // poi il ticker in ordine alfabetico.
  return out.sort((a, b) =>
    (a.primo || '').localeCompare(b.primo || '')
    || b.n - a.n
    || a.ticker.localeCompare(b.ticker));
}

// ── il cancello ─────────────────────────────────────────────────
export type StatoCancello = 'aperto' | 'chiuso';

/**
 * `aperto` = il backend consegna la colonna (anche valorizzata a null su
 * una riga: e' un'assenza VERA, non un campo mancante). `chiuso` = la
 * chiave non arriva proprio, ed e' lo stato di oggi.
 *
 * Si guarda la presenza della CHIAVE e non il valore, perche' JSON
 * omette le chiavi assenti: e' l'unico modo di distinguere "non me lo
 * mandi" da "non c'e' un realizzato su questa riga".
 */
export function statoCancello(trades: Trade[]): StatoCancello {
  return trades.some(t => Object.prototype.hasOwnProperty.call(t, 'realized_eur'))
    ? 'aperto' : 'chiuso';
}

export interface Realizzato {
  /** `ok` = c'e' almeno una riga con un numero · `vuoto` = la colonna arriva ma nessuna riga la valorizza */
  stato: 'ok' | 'vuoto';
  n: number;                         // righe con un realizzato
  somma: number;
  vinte: number;                     // > 0
  perse: number;                     // < 0
  /** esattamente 0: non e' ne' un guadagno ne' una perdita, e contarla fra le
      perdite (com'era) era una classificazione sbagliata su un dato esatto */
  pari: number;
  quotaMaggiore: number;             // quanto pesa il guadagno piu' grosso, 0..1
  tickerMaggiore: string;
}

/**
 * Il conto delle uscite. Torna `null` col cancello chiuso: senza il dato
 * NON si stima, si dichiara il buco (regola 14/07).
 *
 * `quotaMaggiore` esiste per un motivo preciso, misurato il 27/07:
 * una singola uscita valeva gran parte del realizzato. Una somma senza quel numero
 * accanto racconta una storia che i dati non raccontano.
 */
export function contaRealizzato(trades: Trade[]): Realizzato | null {
  if (statoCancello(trades) === 'chiuso') return null;
  const con = trades.filter(t => typeof t.realized_eur === 'number' && isFinite(t.realized_eur));
  // ⚠️ Colonna consegnata ma nessuna riga valorizzata: e' lo stato normale di
  // una migrazione in due tempi (SELECT allargata, backfill non ancora girato).
  // Prima tornava l'oggetto zero e la pagina stampava "+0,00 €" IN VERDE, cioe'
  // affermava che le nove uscite avevano chiuso in pari. E' il default zitto
  // vietato dalla regola 14/07 (review 27/07, ALTA): ora e' uno stato a se',
  // che il chiamante rende come buco dichiarato e non come numero.
  if (!con.length)
    return { stato: 'vuoto', n: 0, somma: 0, vinte: 0, perse: 0, pari: 0,
             quotaMaggiore: 0, tickerMaggiore: '' };
  const somma = con.reduce((s, t) => s + (t.realized_eur as number), 0);
  const positivi = con.filter(t => (t.realized_eur as number) > 0);
  const totalePositivo = positivi.reduce((s, t) => s + (t.realized_eur as number), 0);
  // Il "maggiore" e' il maggiore GUADAGNO, non il maggiore valore assoluto:
  // il rapporto sta su `totalePositivo`, quindi prendere una perdita come
  // numeratore darebbe una quota di segno opposto e sopra l'1 (con guadagni
  // per 6.000 e una perdita da -10.000 usciva "167% dei guadagni concentrato
  // su X", su un titolo che aveva perso). Review 27/07, BASSA.
  let top = positivi[0];
  for (const t of positivi)
    if ((t.realized_eur as number) > (top.realized_eur as number)) top = t;
  return {
    stato: 'ok',
    n: con.length,
    somma,
    vinte: positivi.length,
    perse: con.filter(t => (t.realized_eur as number) < 0).length,
    pari: con.filter(t => t.realized_eur === 0).length,
    // quota sul totale dei GUADAGNI, non sulla somma netta: dividere per un
    // netto che le perdite hanno gia' eroso gonfierebbe la percentuale
    quotaMaggiore: top && totalePositivo > 0
      ? Math.min(1, (top.realized_eur as number) / totalePositivo) : 0,
    tickerMaggiore: top ? top.ticker : '',
  };
}
