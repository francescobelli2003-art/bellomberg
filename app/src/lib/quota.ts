import { t as tr } from '@/i18n/t';
/* ════════════════════════════════════════════════════════════
   IL VALORE QUOTA IN CIMA A F1 — UN SOLO GIUDIZIO SULLA CIMA
   ────────────────────────────────────────────────────────────
   Perché questo modulo esiste (MASTER §9-unquadragies-octodecies,
   changelog (F40); impianto A · IL FONDO, scelto dal PM il 21/08
   sulla rosa in situ `mockup_f1_quota/`).

   Richiesta del PM, testuale: «in cima alla dashboard preferirei
   vedere il nav piuttosto che il valore del patrimonio», chiarita
   subito dopo — «intendo il nav quel 110 in base 100». Cioè non
   «aggiungiamo il valore quota da qualche parte»: il valore quota
   DIVENTA il numero grande, e il patrimonio scende di rango.

   Perché, coi numeri veri (misurati il 21/08 su
   `GET /portfolio/analytics/twr`, rimisurabili; cifre tolte il 02/09
   per la pubblicazione, i rapporti restano): dal 2 febbraio il
   patrimonio si è moltiplicato per più di quattro mentre il valore
   quota ha fatto circa +10%. In mezzo, versamenti. Il caso lampante è
   il 12 agosto: patrimonio su di due cifre percentuali nel giorno,
   quota di pochi centesimi, un bonifico grande in mezzo. Il numero
   grande cresceva di una trentina di volte quanto il PM avesse
   guadagnato.

   ⚠️ NON È SPOSTARE UN NUMERO: È CAMBIARE QUALE NUMERO REGGE LA
   PAGINA. Il patrimonio veniva dallo snapshot NAV, che ha una fonte
   diversa e più robusta; la quota viene dal motore contabile, che
   può tacere. Da qui i tre stati che questo modulo esiste per
   tenere fermi, e che in pagina prima non esistevano:

     1. MOTORE MUTO → la pagina resterebbe SENZA TITOLO. La cura non
        può essere «rimetto il patrimonio» in silenzio (ripiego muto,
        regola PM 14/07): il patrimonio torna in evidenza DICHIARANDO
        di essere un rimpiazzo, col motivo. Vedi `cimaF1`, ramo
        `rimpiazzo`.
     2. LA QUOTA HA UN SUO PASSO, E NON È QUELLO DELLA TESTATA. In
        testata c'è l'orario dello snapshot («16:26 · SQLITE»); sotto
        c'è un numero che per i giorni PASSATI è una chiusura. Perciò
        la quota porta il suo timbro. ⚠️ Ma l'ULTIMO giorno NON è una
        chiusura, e la prima versione di questo modulo lo affermava:
        lo snapshot di oggi è riscritto in UPSERT a ogni giro
        dell'updater (ogni 15 minuti) e la quota si muove con lui —
        misurato il 21/08: quasi tre decimi di quota fra le 16:13 e le
        16:23, stessa data e stesso regime `official`. Il timbro di
        oggi dice «IN CORSO». Vedi `timbroDi`.
     3. IL CONTA-ALLA-ROVESCIA era tarato sugli euro e partiva da
        ZERO. Su un indice base 100, partire da zero mostra per mezzo
        secondo valori tipo «12,50»: quote catastrofiche che non sono
        mai esistite. `cimaF1` dichiara da DOVE si conta, e sulla
        quota si conta dalla BASE.

   ⚠️ CONFINE — cosa questo modulo NON fa. Non calcola niente: il TWR
   lo fa il motore contabile (GIPS, `twr_engine.py`), noi leggiamo
   `twr_index`. L'unica aritmetica qui è la variazione dalla base, ed
   è DERIVATA dal numero che si vede a schermo apposta: se un giorno
   `metrics.twr_total_pct` divergesse dall'indice, la pagina non può
   mostrare due numeri che si contraddicono a due righe di distanza.

   ⚠️ CONFINE — I VERSAMENTI. `flows_eur` NON è il ledger: la
   `methodology` del payload dichiara che nel regime `reconstructed`
   quel termine è `dCB - dRealized` (net-invested-at-cost), non un
   bonifico. Misurato il 21/08: 22 giorni con `flows_eur` non nullo,
   ma il ledger vero (`external_flows`) ne ha DUE. Renderli come
   «versati» direbbe il falso venti volte su ventidue. Perciò la nota
   del flusso legge SOLO `external_flows`.
   ════════════════════════════════════════════════════════════ */

import type { TwrPayload } from '@/lib/api';
import { fmtEUR, fmtNum } from '@/lib/format';

export interface QuotaViva {
  stato: 'viva';
  /** l'ultimo punto di `twr_index` — il numero grande */
  valore: number;
  /** il primo punto: quasi sempre 100, ma non lo diamo per scontato */
  base: number;
  /** ISO, `dates[0]` */
  dal: string;
  /** ISO `YYYY-MM-DD`, `dates[ultimo]` — la data DELLA QUOTA, non quella del NAV */
  al: string;
  /** quanti PUNTI ha la serie. ⚠️ NON sono giorni di calendario: 150 punti
   *  coprono 200 giorni dal 02/02 al 21/08 (misurato). Sono i giorni in cui il
   *  motore ha un valore. */
  punti: number;
  /** il regime dell'ULTIMO punto. `null` = non lo so (`regimes` assente o di
   *  lunghezza diversa): e «non lo so» NON è «ricostruito» — dirlo sarebbe
   *  un'affermazione al posto di un buco dichiarato (regola PM 14/07). */
  regime: 'official' | 'reconstructed' | null;
  /** da quando la serie è ufficiale (`regime_summary.official_since`): serve a
   *  non far credere che tutta la serie lo sia. Misurato: 93 punti su 150 sono
   *  RICOSTRUITI, ufficiali solo dall'11/06. */
  ufficialeDal: string | null;
  /** ciò che il MOTORE dichiara di sé (`notes`): ledger vuoto, ricostruzione
   *  non disponibile… Il backend il buco lo dice, e prima di questo lotto F1
   *  lo buttava mentre F2 lo mostra. */
  note: string[];
  /** derivata dal valore mostrato, non da `metrics` (v. confine sopra) */
  variazionePct: number;
  /** il movimento del ledger alla data `al`, se c'è */
  flusso: FlussoDelGiorno | null;
}

export interface FlussoDelGiorno {
  /** VERSATI · PRELEVATI · MOVIMENTI (quando i tipi non concordano) */
  parola: 'VERSATI' | 'PRELEVATI' | 'MOVIMENTI';
  /** somma in valore assoluto; `null` quando i tipi non concordano e un
   *  totale non sarebbe un numero che sappiamo nominare */
  importo: number | null;
  quanti: number;
}

export interface QuotaAssente {
  stato: 'assente';
  /** in italiano, per il PM: perché il titolo della pagina manca */
  motivo: string;
}

/** ⚠️ IN ATTESA NON È ASSENTE, e tenerle insieme costava un allarme falso a
 *  ogni apertura della pagina. La chiamata al motore ha 90 secondi di
 *  timeout: finché è in volo, «VALORE QUOTA NON DISPONIBILE» in ambra
 *  sarebbe una frase non ancora vera. Il confine passa dove passa
 *  l'informazione — e «ho già chiesto e non è tornato niente» il frontend lo
 *  sa per intero. */
export interface QuotaAttesa {
  stato: 'attesa';
}

export type EsitoQuota = QuotaViva | QuotaAttesa | QuotaAssente;

/** IL GIUDIZIO — uno solo, e tutto il resto si deriva da qui.
 *
 *  `errore` è il motivo raccolto dal chiamante quando la chiamata non è
 *  arrivata a destinazione. Prima di questo lotto quel ramo era un
 *  `.catch(() => {})`: il motivo esisteva e veniva buttato.
 *  `inCorso` è vero mentre la chiamata è ancora in volo. */
export function leggiQuota(
  p: TwrPayload | null, errore?: string | null, inCorso = false,
): EsitoQuota {
  if (errore) return { stato: 'assente', motivo: errore };
  if (!p) {
    return inCorso
      ? { stato: 'attesa' }
      : { stato: 'assente', motivo: tr('dashboard.accounting_none') };
  }
  // ⚠️ `p.error` non e' garantito stringa: `'…' + {}` dava «[object Object]».
  const errPayload = leggiDetail(p.error);
  if (errPayload) return { stato: 'assente', motivo: tr('dashboard.accounting_prefix') + errPayload };

  const dates = p.dates || [];
  const idx = p.twr_index || [];
  if (!dates.length || !idx.length) {
    return { stato: 'assente', motivo: tr('dashboard.quota_no_series') };
  }
  // Se non so DI CHE GIORNO è il numero, non lo mostro: il timbro della data
  // è metà del punto di questo lotto (v. stato 2 in testa al file).
  if (dates.length !== idx.length) {
    return {
      stato: 'assente',
      motivo: tr('dashboard.quota_length', {a: idx.length, b: dates.length}),
    };
  }

  const base = Number(idx[0]);
  const valore = Number(idx[idx.length - 1]);
  // ⚠️ NON `base <= 0`: quella guardia protegge l'aritmetica e non il TESTO.
  // Con base 1e-12 passava, e la pagina scriveva «BASE 0» (cioè proprio quello
  // che la guardia esiste per impedire) sopra una percentuale di venti cifre.
  // Un indice base 100 non parte da un milionesimo.
  if (!isFinite(base) || base < 1e-6) {
    return { stato: 'assente', motivo: tr('dashboard.quota_base_bad') };
  }
  if (!isFinite(valore)) {
    return { stato: 'assente', motivo: tr('dashboard.quota_last_bad') };
  }

  const al = soloData(dates[dates.length - 1]);
  const regimes = p.regimes || [];
  const rs = p.regime_summary;
  return {
    stato: 'viva',
    valore,
    base,
    dal: soloData(dates[0]),
    al,
    punti: dates.length,
    // `regimes` può mancare o arrivare di lunghezza diversa (backend più
    // vecchio del contratto): in quel caso `null`, che si rende «REGIME N.D.».
    regime: regimes.length === dates.length
      ? (regimes[regimes.length - 1] === 'official' ? 'official' : 'reconstructed')
      : null,
    ufficialeDal: rs && rs.official_since ? soloData(rs.official_since) : null,
    note: (p.notes || []).map(n => String(n)).filter(n => n.trim() !== ''),
    variazionePct: (valore / base - 1) * 100,
    flusso: flussoAllaData(p.external_flows, al),
  };
}

/** `2026-08-21T00:00:00` → `2026-08-21`.
 *  ⚠️ Esiste perché il modulo si contraddiceva da solo: `dataIt` tollerava
 *  l'ora attaccata (e un caso lo certificava) mentre il confronto delle date
 *  del ledger era esatto — bastava la `T` da UNA delle due parti e il
 *  versamento spariva MUTO. Oggi il backend manda la forma corta da entrambe,
 *  quindi era latente: si normalizza in un punto solo. */
function soloData(v: unknown): string {
  return String(v == null ? '' : v).slice(0, 10);
}

/** Il movimento del LEDGER a una data. Solo `external_flows`: v. il confine
 *  sui versamenti in testa al file. */
function flussoAllaData(
  flussi: TwrPayload['external_flows'], data: string,
): FlussoDelGiorno | null {
  const righe = (flussi || []).filter(f => soloData(f.date) === soloData(data));
  if (!righe.length) return null;

  const tipi = new Set(righe.map(f => String(f.type || '').toUpperCase()));
  const importi = righe.map(f => Math.abs(Number(f.amount_eur)));
  // ⚠️ `Number(x) || 0` avrebbe fatto diventare ZERO un importo illeggibile, e
  // la pagina avrebbe scritto «OGGI VERSATI 0 €»: un ripiego muto travestito
  // da misura (regola PM 14/07). Se un importo non è un numero, il totale non
  // si può nominare — si dice quanti movimenti sono e basta.
  const leggibili = importi.every(v => isFinite(v));
  const somma = leggibili ? importi.reduce((s, v) => s + v, 0) : null;
  if (leggibili && tipi.size === 1 && tipi.has('DEPOSIT')) {
    return { parola: 'VERSATI', importo: somma, quanti: righe.length };
  }
  if (leggibili && tipi.size === 1 && tipi.has('WITHDRAWAL')) {
    return { parola: 'PRELEVATI', importo: somma, quanti: righe.length };
  }
  if (!leggibili && tipi.size === 1) {
    const p = tipi.has('WITHDRAWAL') ? 'PRELEVATI' : tipi.has('DEPOSIT') ? 'VERSATI' : 'MOVIMENTI';
    return { parola: p, importo: null, quanti: righe.length };
  }
  // Tipi discordi (o sconosciuti): la somma dei valori assoluti non è
  // «quanto è entrato», e non ho un campione di WITHDRAWAL su cui misurare
  // il segno di `amount_eur`. Dichiaro quanti sono e non invento un totale.
  return { parola: 'MOVIMENTI', importo: null, quanti: righe.length };
}

/** COSA MOSTRA LA CIMA DELLA PAGINA — derivata dal giudizio, mai decisa a
 *  parte. È la lezione del banco di prova di F5: due giudici sulla stessa
 *  cosa prima o poi dissentono. */
export interface CimaF1 {
  tipo: 'quota' | 'attesa' | 'rimpiazzo';
  /** la riga sopra la cifra */
  occhiello: string;
  /** l'orologio DEL NUMERO grande, in coda all'occhiello. Vuoto sul rimpiazzo
   *  e in attesa, dove l'orologio giusto è già in testata.
   *  ⚠️ Sta in coda all'occhiello per una MISURA, non per gusto: a 1440×900 —
   *  la finestra con cui si apre Electron — la colonna di F1 dà **237px**
   *  utili (non 265: quella è la larghezza padding compreso, ed è l'errore
   *  con cui l'avevo disegnato la prima volta). L'occhiello ne chiede 341 e
   *  va a capo comunque: la seconda riga resta mezza vuota e il timbro ci sta
   *  a costo zero. Appeso invece alla riga della spiegazione la portava da
   *  due capi a TRE. */
  timbro: string;
  /** COME si formatta la cifra grande. ⚠️ Prima qui c'era `cifra: string`, già
   *  formattata — e la pagina NON la usava: rendeva il valore animato
   *  riformattandolo con una scelta quota/euro sua. Cioè un SECONDO GIUDICE,
   *  esattamente quello contro cui questo modulo mette in guardia, e tre casi
   *  della batteria (compresa la proprietà per cui il lotto esiste) misuravano
   *  un campo morto. Ora la mappatura vive in un posto solo: `formattaCifra`. */
  unita: 'quota' | 'eur';
  /** da dove parte il conta-alla-rovescia e dove arriva */
  contaDa: number;
  contaA: number;
  /** falso quando il numero da mostrare non è un numero: la cifra diventa un
   *  `n/a` dichiarato invece di uno zero che sembra una misura */
  numerabile: boolean;
  /** la riga sotto la cifra */
  sotto: string;
  /** quello che il MOTORE dichiara di sé, per il tooltip: stringa vuota se non
   *  dichiara niente */
  nota: string;
  /** il rimpiazzo si vede: non è uno stato normale */
  allarme: boolean;
}

/** L'UNICO posto che sa come si scrive la cifra grande. La pagina chiama
 *  questa sul valore animato, così il conteggio e il giudizio non possono
 *  divergere. */
export function formattaCifra(c: CimaF1, valore: number): string {
  if (!c.numerabile || !isFinite(valore)) return 'n/a';
  return c.unita === 'quota' ? fmtNum(valore, 2) : fmtEUR(valore);
}

export function cimaF1(q: EsitoQuota, patrimonioEur: number, oggiISO = ''): CimaF1 {
  if (q.stato === 'attesa') {
    // La pagina è già utile: il patrimonio c'è e viene da un'altra fonte.
    // Si dice cosa sta arrivando, e NON si suona l'allarme per un'attesa.
    return {
      tipo: 'attesa',
      occhiello: tr('dashboard.wealth'),
      timbro: '',
      unita: 'eur',
      contaDa: 0,
      contaA: isFinite(patrimonioEur) ? patrimonioEur : 0,
      numerabile: isFinite(patrimonioEur),
      sotto: tr('dashboard.quota_pending'),
      nota: '',
      allarme: false,
    };
  }
  if (q.stato === 'assente') {
    return {
      tipo: 'rimpiazzo',
      occhiello: tr('dashboard.quota_replacement'),
      timbro: '',
      unita: 'eur',
      contaDa: 0,
      contaA: isFinite(patrimonioEur) ? patrimonioEur : 0,
      numerabile: isFinite(patrimonioEur),
      sotto: q.motivo + tr('dashboard.quota_assets_warning'),
      nota: '',
      allarme: true,
    };
  }
  const segno = q.variazionePct > 0 ? '+' : q.variazionePct < 0 ? '−' : '';
  return {
    tipo: 'quota',
    // la base si scrive con i decimali SOLO se ne ha: `fmtNum(100.5, 0)` diceva
    // «BASE 101», cioè una base che non è quella con cui è calcolato niente
    // altro nella cima.
    occhiello: tr('dashboard.quota_base', {a: fmtNum(q.base, Number.isInteger(q.base) ? 0 : 2)})
      + tr('dashboard.since_caps', {a: dataIt(q.dal)}),
    timbro: timbroDi(q, oggiISO),
    unita: 'quota',
    contaDa: q.base,
    contaA: q.valore,
    numerabile: true,
    sotto: tr('dashboard.quota_return', {a: segno, b: fmtNum(Math.abs(q.variazionePct), 2)})
      + tr('dashboard.quota_flow_adjusted'),
    nota: notaCima(q),
    allarme: false,
  };
}

/** L'OROLOGIO DELLA CIFRA GRANDE, e le tre cose che deve dire senza mentire.
 *
 *  ⚠️ «CHIUSURA» sull'ultimo giorno ERA FALSO, misurato: lo snapshot NAV di
 *  oggi viene riscritto in UPSERT a ogni giro dell'updater (`twr_engine.py`,
 *  `ON CONFLICT(date) DO UPDATE`; il task gira ogni 15 minuti, e F1 stessa
 *  forza un aggiornamento prezzi ogni 60 secondi). Il 21/08 fra le 16:13 e
 *  le 16:23 la quota si è mossa di quasi tre decimi — stessa data, stesso
 *  regime `official`, timbro «CHIUSURA». Il lotto esiste per togliere un
 *  orologio fuorviante da sotto una cifra: sull'ultimo giorno ne rimetteva uno.
 *
 *  ⚠️ E la forma BREVE della data vale solo NELL'ANNO CORRENTE: una quota ferma
 *  da un anno avrebbe portato un timbro identico a quello di oggi, cioè
 *  proprio nello stato in cui la data conta di più. */
/** CIO' CHE IL MOTORE DICHIARA DI SE', per il tooltip dell'occhiello: le sue
 *  `notes` (ledger vuoto, ricostruzione non disponibile…) piu' il confine dei
 *  regimi. Prima di questo lotto F1 buttava le `notes` mentre F2 le mostra —
 *  e il numero che le riguarda da oggi e' il TITOLO della pagina. */
function notaCima(q: QuotaViva): string {
  const p: string[] = [];
  if (q.ufficialeDal && q.ufficialeDal > q.dal) {
    p.push(tr('dashboard.quota_mixed', {a: dataIt(q.ufficialeDal)})
      + tr('dashboard.quota_reconstructed', {a: dataIt(q.dal)}));
  }
  p.push(...q.note.map(note => tr('dashboard.source_original') + ': ' + note));
  return p.join(' · ');
}

function timbroDi(q: QuotaViva, oggiISO: string): string {
  const stessoAnno = oggiISO.slice(0, 4) === q.al.slice(0, 4);
  const quando = dataIt(q.al, stessoAnno);
  const parola = q.al === oggiISO ? tr('dashboard.progress')
    : q.regime === 'official' ? tr('dashboard.close')
      : q.regime === 'reconstructed' ? tr('dashboard.reconstructed')
        : tr('dashboard.regime_na');
  // ⚠️ il confine dei regimi NON sta qui, e non e' una dimenticanza: misurato,
  // «· UFFICIALE DAL 11/06» porta l'occhiello da DUE capi a TRE nella colonna
  // da 237px, cioe' costa una riga intera dell'hero in una pagina che a
  // 1440x900 gia' scorre. Sta nella nota (v. `notaCima`), che il tooltip
  // dell'occhiello rende raggiungibile a costo zero.
  return `${parola} ${quando}`;
}

/** La nota del versamento, sulla riga del PATRIMONIO — perché è il
 *  patrimonio che il versamento muove, ed è esattamente la confusione che
 *  questo lotto esiste per togliere. Stringa vuota quando non c'è niente da
 *  dire: mai una riga che occupa spazio per dire «nessun flusso».
 *
 *  `oggiISO` si passa da fuori (niente orologio dentro un modulo che si
 *  prova): «OGGI VERSATI» è vero solo se la quota è di oggi. */
export function notaFlusso(q: EsitoQuota, oggiISO: string): string {
  if (q.stato !== 'viva' || !q.flusso) return '';
  const f = q.flusso;
  const parole = { VERSATI: tr('dashboard.deposit'), PRELEVATI: tr('dashboard.withdrawal'), MOVIMENTI: tr('dashboard.flows') };
  // tre frasi, tutte vere: il totale quando lo so, il conteggio quando i tipi
  // non concordano, e il verso col buco DICHIARATO quando so cosa è successo
  // ma non quanto.
  const quanto = f.importo != null
    ? `${parole[f.parola]} ${fmtEUR(f.importo, false, 0)}`
    : f.parola === 'MOVIMENTI'
      ? tr('dashboard.flow_count', {a: f.quanti})
      : tr('dashboard.flow_unknown_amount', {a: parole[f.parola]});
  if (q.al === oggiISO) return tr('dashboard.flow_today', {a: quanto});
  // forma breve solo nell'anno corrente: «VERSATI 12.500 € IL 21/08» senza
  // anno, su un movimento dell'anno scorso, e' un'affermazione sui SOLDI a cui
  // manca il pezzo che la rende vera.
  const stessoAnno = String(oggiISO).slice(0, 4) === q.al.slice(0, 4);
  return tr('dashboard.flow_date', {a: quanto, b: dataIt(q.al, stessoAnno)});
}

/** IL MOTIVO, in una riga, da un errore di trasporto.
 *
 *  Esiste perché prima di questo lotto il ramo era `.catch(() => {})`: il
 *  motivo c'era, lo si buttava, e finché la quota era un chip in fondo non
 *  costava niente. Ora è il titolo della pagina.
 *
 *  ⚠️ Il `detail` del backend viene TAGLIATO a 120 caratteri perché deve
 *  stare su una riga sola — ma il taglio si DICHIARA coi numeri invece di
 *  chiudersi con dei puntini che si travestono da fine frase. */
export function motivoChiamata(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string };
  const stato = e?.response?.status;
  if (stato) {
    const det = leggiDetail(e?.response?.data?.detail);
    return tr('dashboard.accounting_http') + stato + (det ? ' — ' + taglia(det) : '');
  }
  const m = String(e?.message || '').trim();
  return tr('dashboard.accounting_prefix') + (m ? taglia(m) : tr('dashboard.accounting_no_reason'));
}

/** ⚠️ `String(detail)` dava «[object Object]» sulla forma d'errore più comune
 *  di FastAPI: il 422 di validazione manda una LISTA di oggetti `{loc, msg,
 *  type}`. Verificato sul backend vivo con
 *  `GET /portfolio/analytics/twr?force=pippo`. Cioè il modulo nato perché «il
 *  motivo c'era e si buttava» lo buttava ancora, e per giunta scrivendo in
 *  cima alla pagina una stringa che non dice niente. */
export function leggiDetail(det: unknown): string {
  if (det == null) return '';
  if (typeof det === 'string') return det.trim();
  if (Array.isArray(det)) {
    return det
      .map(d => (d && typeof d === 'object' && 'msg' in (d as object)
        ? String((d as { msg: unknown }).msg)
        : JSON.stringify(d)))
      .filter(s => s && s !== 'undefined')
      .join('; ')
      .trim();
  }
  if (typeof det === 'object' && 'msg' in (det as object)) {
    return String((det as { msg: unknown }).msg).trim();
  }
  return JSON.stringify(det);
}

/** ⚠️ Il taglio conta i CARATTERI, non le code unit: `s.slice()` spezza in due
 *  una coppia surrogata e lascia mezzo glifo, mentre il numero dichiarato sarebbe
 *  di un'altra unità di misura. Se dichiaro quanti ne mancano, devo contarli
 *  nella stessa unità in cui li mostro. */
export function taglia(s: string, max = 120): string {
  const car = Array.from(s);
  if (car.length <= max) return s;
  return tr('dashboard.truncated', {a: car.slice(0, max).join(''), b: car.length - max});
}

/** `2026-02-02` → `02/02/2026` (o `02/02` con `breve`). Non `toLocaleDateString`:
 *  quella stringa passa da un fuso, e queste sono date di calendario. */
export function dataIt(iso: string, breve = false): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
  if (!m) return String(iso || tr('dashboard.na'));
  return breve ? `${m[3]}/${m[2]}` : `${m[3]}/${m[2]}/${m[1]}`;
}
