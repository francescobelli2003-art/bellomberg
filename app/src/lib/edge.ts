import { t as tr } from '@/i18n/t';
import { fmtNum } from '@/lib/format';
/* ════════════════════════════════════════════════════════════
   F13 EDGE SCANNER — UN SOLO GIUDIZIO SU COSA DICE LA SCANSIONE
   ────────────────────────────────────────────────────────────
   Perché questo modulo esiste (MASTER §9-unquadragies-vicies,
   changelog (F42); ponte 22/08-2 e 22/08-3 della chat backend,
   changelog (79)/(80)/(81)).

   IL FATTO, MISURATO DALLA CHAT BACKEND IL 22/08 (voce 22/08-2):
     · `GET /signals/edge_scan` scansiona ora 28 posizioni su 28
       (prima 20: il taglio nascondeva tre segnali a forza 100).
       Costo misurato: 165,4 s a processo caldo, 381,4 s a freddo.
     · dal (80) c'è una cache in-process (TTL 3600 s; 300 s se la
       scansione era DEGRADATA, decisione (g) del PM): la chiamata
       successiva costa 0,025-0,03 s. Ma il consigliere gira in un
       processo SEPARATO dall'API: la prima apertura dopo un riavvio
       del backend paga SEMPRE la scansione intera.
     · una richiesta che arriva MENTRE una scansione è in corso
       ASPETTA sul lock (fino a ~6,4 min a freddo) e poi esce dalla
       cache — ANCHE un hit: il lock avvolge pure la lettura della
       cache; l'abort del client NON libera il worker lato server.
       Quindi i regimi sono TRE, non due: cache calda · scansione
       fresca · in coda dietro una scansione in corso.
     · `scan_portfolio` sul guasto fa `return {"error": ...}` e
       l'endpoint risponde HTTP 200: la pagina rendeva «Nessun
       segnale sopra la soglia» su un DB lockato o su Polygon giù
       (già ALTO in audit/23, mai curato). Da oggi si distingue dal
       payload: `error` → guasto; `copertura.scansione_degradata` o
       `copertura.nessuna_misura` non vuoti → zero PARZIALE;
       copertura piena e `signals` vuoto → zero MISURATO.
     · sul libro vero 21 nomi su 28 ricevono UN SOLO rilevatore
       per-ticker (lo z-score di prezzo: hanno un punto nel simbolo,
       e fuori dagli USA le catene OPRA e i trade del Congresso non
       esistono). L'assenza di un segnale di volatilità su quei nomi
       NON è una misura: è una copertura che manca, e il payload lo
       dice (`copertura.nota`) — ma la pagina non lo rendeva.

   COSA FA QUESTO MODULO: legge la risposta UNA volta e consegna già
   scritte tutte le frasi che vanno a schermo — lo stato (attesa /
   guasto / viva), la qualificazione dello ZERO, l'età della
   scansione, la copy dell'attesa nei tre regimi, il motivo di una
   chiamata fallita. La pagina non interpreta il payload per conto
   suo: legge l'esito.

   ⚠️ NIENTE RIPIEGHI MUTI (regola PM 14/07). Un campo che il
   contratto promette e non arriva (`cache`, `copertura`,
   `servita_da_cache`) si dichiara come «backend più vecchio del
   contratto» o «fuori contratto», non si finge il ramo felice. Un
   `{"error"}` servito con HTTP 200 è un GUASTO col motivo verbatim,
   non uno zero.

   ⚠️ IL TIMEOUT NON È UN GUASTO DELLA SCANSIONE: è la PAGINA che ha
   smesso di aspettare mentre il backend continua. Ha un'origine sua
   (`timeout`) perché le frasi giuste sono diverse («la scansione
   può essere ancora in corso», non «non è stata eseguita»), e
   perché il tasto giusto dopo è RIPROVA (riusa la scansione quando
   finisce), non RIFAI (che si accoderebbe dietro di essa).

   ⚠️ `generated` sui hit è l'ora della SCANSIONE, non della risposta
   (fino a un'ora prima): qui si usa solo come «scansione delle
   HH:MM», mai come «risposta delle».

   ⚠️ LE LATENZE qui sotto sono MISURE con la data, non promesse: se
   fra un mese la scansione costa altro, la frase in pagina dice
   ancora «misurati il 22/08» — invecchia onestamente, non mente.

   ⚠️ IL CONTRATTO DEL CAMPO `cache` CAMBIA CHIAVI FRA I RAMI:
     fresca → {attiva: true, servita_da_cache: false, ttl_s}
     hit    → {attiva: true, servita_da_cache: true, ttl_s, eta_s,
               scansione_delle}
     bypass → {attiva: false, servita_da_cache: false, motivo}
     (+ `ttl_motivo` su fresca E hit quando la scansione era degradata)
   Si legge con `?.`, `eta_s` non si dà per presente, e il ramo
   «fresca» NON è il default: vale solo con `servita_da_cache === false`.
   ════════════════════════════════════════════════════════════ */

import { leggiDetail, taglia } from './quota';

/** Un segnale come lo consegna `signal_engine.scan_portfolio`. */
export interface Segnale {
  ticker: string;
  category: string;
  name: string;
  value: string | number;
  context: string;
  direction: string;
  strength: number;
  reading: string;
  source: string;
}

/** Le latenze MISURATE dalla chat backend il 22/08 su tre giri veri
 *  (ponte 22/08-2/-3, changelog (79)/(80)). Sono misure con la data:
 *  la copy in pagina le cita come tali. */
export const LATENZE_MISURATE = {
  /** hit della cache in-process (0,025-0,03 s misurati) */
  cacheS: 0.03,
  /** scansione fresca, processo caldo */
  caldoS: 165.4,
  /** scansione fresca, processo freddo (prima apertura dopo un riavvio) */
  freddoS: 381.4,
  /** attesa massima sul lock dietro una scansione già in corso (~6,4 min) */
  lockS: 384,
  posizioni: 28,
  misurateIl: '22/08/2026',
} as const;

/** La soglia più bassa che la pagina sa chiedere (i bottoni: 30/45/60/75). */
export const SOGLIA_MINIMA_PAGINA = 30;

/* ── la copertura: COSA ha risposto e cosa no ─────────────────────── */

export interface CoperturaDichiarata {
  dichiarata: true;
  /** `posizioni_totali` / `posizioni_scansionate`; 0 SOLO se `contate` è false */
  totali: number;
  scansionate: number;
  /** false = il backend non ha mandato i conteggi (e il grado è degradata) */
  contate: boolean;
  /** ogni rilevatore applicabile ha risposto */
  piena: string[];
  /** ticker → quale rilevatore è stato MUTO e perché */
  degradata: Record<string, string>;
  /** l'unico rilevatore per-ticker applicabile è lo z-score di prezzo */
  soloPrezzo: string[];
  /** ticker → nessun rilevatore ha risposto, e perché */
  nessunaMisura: Record<string, string>;
  nonScansionate: string[];
  fattorialiSu: number | null;
  fattorialiKo: string | null;
  /** la prosa già pronta del backend (verbatim) */
  nota: string;
  /** `piena` = nessun rilevatore muto, nessun nome senza misura, tutte le posizioni
   *  scansionate, fattoriali calcolati su tutte. Altrimenti `degradata`, col motivo
   *  contato. */
  grado: 'piena' | 'degradata';
  motivoDegrado: string;
}
export interface CoperturaAssente {
  dichiarata: false;
  motivo: string;
}
export type Copertura = CoperturaDichiarata | CoperturaAssente;

/* ── l'età: QUANDO è stata fatta la scansione che sto guardando ──── */

export interface EtaDichiarata {
  dichiarata: true;
  servitaDaCache: boolean;
  /** età della scansione AL MOMENTO DELLA RISPOSTA (`eta_s` sui hit, 0 su fresca) */
  etaAllaRispostaS: number;
  ttlS: number | null;
  ttlMotivo: string | null;
  /** ISO locale del backend: `scansione_delle` sui hit, `generated` su fresca */
  scansioneDelle: string | null;
  /** `cache.attiva === false`: scansione diretta fuori cache (mai sul percorso della pagina) */
  fuoriCache: boolean;
}
export interface EtaAssente {
  dichiarata: false;
  motivo: string;
}
export type EtaScan = EtaDichiarata | EtaAssente;

/* ── la chiamata fallita: un motivo, e se era il NOSTRO limite ─────── */

export interface EsitoChiamata {
  /** verbatim dove c'è un verbatim; già tagliato se lunghissimo */
  motivo: string;
  /** la PAGINA ha smesso di aspettare (axios timeout): il backend continua */
  timeout: boolean;
}

/* ── i tre stati ─────────────────────────────────────────────────── */

export interface ScanAttesa {
  stato: 'attesa';
  forzata: boolean;
}
export interface ScanGuasto {
  stato: 'guasto';
  /** verbatim dal backend o dalla chiamata, già tagliato se lunghissimo */
  motivo: string;
  /** `payload` = `{"error"}` servito con HTTP 2xx · `timeout` = la pagina ha smesso
   *  di aspettare, il backend continua · `chiamata` = HTTP/rete senza risposta valida
   *  · `forma` = risposta che non rispetta il contratto */
  origine: 'payload' | 'timeout' | 'chiamata' | 'forma';
  /** `_timestamp` del payload d'errore, se c'è (ISO locale del backend) */
  quando: string | null;
}
export interface ScanViva {
  stato: 'viva';
  /** i segnali sopra la soglia, come li ha ordinati il backend (forza decrescente) */
  segnali: Segnale[];
  /** righe di `signals` che non hanno la forma di un segnale: contate, non buttate */
  illeggibili: number;
  /** `n_signals_total` del backend: quanti segnali esistono A QUALUNQUE forza */
  nTotali: number | null;
  /** `n_signals_strong` del backend: quanti sopra la soglia chiesta (conta anche le
   *  righe che qui risultano illeggibili) */
  nForti: number | null;
  perCategoria: Record<string, number>;
  copertura: Copertura;
  eta: EtaScan;
  /** l'ora della SCANSIONE (non della risposta): v. testata */
  generated: string | null;
  /** l'ultima chiamata è fallita ma questa risposta (precedente) è ancora a schermo */
  ultimaChiamataFallita: EsitoChiamata | null;
}
export type EsitoScan = ScanAttesa | ScanGuasto | ScanViva;

export interface OpzioniLettura {
  inCorso: boolean;
  forzata: boolean;
  /** l'esito della chiamata fallita (da `esitoChiamata`), o null */
  errore: EsitoChiamata | null;
}

function oggetto(x: unknown): x is Record<string, unknown> {
  return !!x && typeof x === 'object' && !Array.isArray(x);
}

/** ⚠️ `typeof === 'number'`, non `Number(x)`: `Number(null)` fa ZERO e una riga
 *  senza forza passerebbe per un segnale a forza 0 (lezione di casa). Il backend
 *  manda interi (`_sig`), quindi niente di legittimo viene scartato. */
function numeroFinito(x: unknown): x is number {
  return typeof x === 'number' && Number.isFinite(x);
}

function segnaleValido(s: unknown): s is Segnale {
  if (!oggetto(s)) return false;
  return typeof s.ticker === 'string' && s.ticker.trim() !== '' && numeroFinito(s.strength);
}

function elencoStringhe(x: unknown): string[] {
  return Array.isArray(x) ? x.filter((v): v is string => typeof v === 'string') : [];
}

function mappaStringhe(x: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!oggetto(x)) return out;
  for (const [k, v] of Object.entries(x)) out[k] = String(v);
  return out;
}

function interoONull(x: unknown): number | null {
  if (x == null) return null;
  const n = Number(x);
  return Number.isFinite(n) ? n : null;
}

function testoONull(x: unknown): string | null {
  if (x == null) return null;
  const s = String(x).trim();
  return s === '' ? null : s;
}

/** Legge il blocco `copertura` del payload. Assente = backend più vecchio del
 *  contratto (79): si dichiara, non si finge piena. */
export function leggiCopertura(payload: Record<string, unknown>): Copertura {
  const c = payload.copertura;
  if (!oggetto(c)) {
    return {
      dichiarata: false,
      motivo: tr('edge.f051')
        + tr('edge.f052'),
    };
  }
  // ⚠️ niente `?? 0`: un conteggio che manca direbbe «0/0 posizioni … ZERO MISURATO»
  // con la faccia di una misura. Manca → si dichiara e la copertura è degradata.
  const totaliLetti = interoONull(c.posizioni_totali);
  const scansionateLette = interoONull(c.posizioni_scansionate);
  const totali = totaliLetti ?? 0;
  const scansionate = scansionateLette ?? 0;
  const degradata = mappaStringhe(c.scansione_degradata);
  const nessunaMisura = mappaStringhe(c.nessuna_misura);
  const nonScansionate = elencoStringhe(c.non_scansionate);
  const fattorialiSu = interoONull(c.fattoriali_su);
  const fattorialiKo = testoONull(c.fattoriali_ko);
  const pezzi: string[] = [];
  if (totaliLetti == null || scansionateLette == null) pezzi.push(tr('edge.f053'));
  const nDeg = Object.keys(degradata).length;
  const nNM = Object.keys(nessunaMisura).length;
  if (nDeg) pezzi.push(`${nDeg} ${nDeg === 1 ? tr('edge.f054') : tr('edge.f055')}`);
  if (nNM) pezzi.push(`${nNM} ${nNM === 1 ? tr('edge.f056') : tr('edge.f057')}`);
  if (fattorialiKo) pezzi.push(tr('edge.f058', {a: taglia(fattorialiKo, 80)}));
  else if (fattorialiSu == null) pezzi.push(tr('edge.f059'));
  else if (fattorialiSu < totali) pezzi.push(tr('edge.f060', {a: fattorialiSu, b: totali}));
  if (nonScansionate.length) pezzi.push(tr('edge.f061', {a: nonScansionate.length}));
  else if (totaliLetti != null && scansionateLette != null && scansionate < totali) {
    pezzi.push(tr('edge.f062', {a: totali - scansionate}));
  }
  return {
    dichiarata: true,
    totali,
    scansionate,
    contate: totaliLetti != null && scansionateLette != null,
    piena: elencoStringhe(c.scansione_piena),
    degradata,
    soloPrezzo: elencoStringhe(c.solo_prezzo),
    nessunaMisura,
    nonScansionate,
    fattorialiSu,
    fattorialiKo,
    nota: typeof c.nota === 'string' ? c.nota : '',
    grado: pezzi.length ? 'degradata' : 'piena',
    motivoDegrado: pezzi.join(' · '),
  };
}

/** Legge il campo `cache` del payload (contratto (80)), chiavi diverse per ramo.
 *  ⚠️ Il ramo «fresca» NON è il default: senza `servita_da_cache === false` la frase
 *  «i rilevatori sono stati interrogati» sarebbe un'affermazione non sostenuta. */
export function leggiEta(payload: Record<string, unknown>): EtaScan {
  const generated = testoONull(payload.generated);
  const k = payload.cache;
  if (!oggetto(k)) {
    return {
      dichiarata: false,
      motivo: tr('edge.f063')
        + tr('edge.f064'),
    };
  }
  const ttlS = interoONull(k.ttl_s);
  const ttlMotivo = testoONull(k.ttl_motivo);
  if (k.attiva === false) {
    return {
      dichiarata: true, servitaDaCache: false, etaAllaRispostaS: 0, ttlS: null,
      ttlMotivo: testoONull(k.motivo), scansioneDelle: generated, fuoriCache: true,
    };
  }
  if (k.servita_da_cache === true) {
    const eta = interoONull(k.eta_s);
    if (eta == null || eta < 0) {
      return {
        dichiarata: false,
        motivo: tr('edge.f065', {a: String(k.eta_s)}),
      };
    }
    return {
      dichiarata: true, servitaDaCache: true, etaAllaRispostaS: eta, ttlS, ttlMotivo,
      scansioneDelle: testoONull(k.scansione_delle) ?? generated, fuoriCache: false,
    };
  }
  if (k.servita_da_cache === false) {
    return {
      dichiarata: true, servitaDaCache: false, etaAllaRispostaS: 0, ttlS, ttlMotivo,
      scansioneDelle: generated, fuoriCache: false,
    };
  }
  return {
    dichiarata: false,
    motivo: tr('edge.f066', {a: String(k.servita_da_cache)})
      + tr('edge.f067'),
  };
}

/** IL giudizio: una risposta (o la sua assenza) → uno stato solo. */
export function leggiScan(payload: unknown, o: OpzioniLettura): EsitoScan {
  if (o.inCorso) return { stato: 'attesa', forzata: o.forzata };

  if (oggetto(payload) && Array.isArray(payload.signals) && !payload.error) {
    const grezzi = payload.signals as unknown[];
    const segnali = grezzi.filter(segnaleValido);
    const perCategoria: Record<string, number> = {};
    if (oggetto(payload.by_category)) {
      for (const [k, v] of Object.entries(payload.by_category)) {
        if (numeroFinito(v)) perCategoria[k] = v;
      }
    }
    return {
      stato: 'viva',
      segnali,
      illeggibili: grezzi.length - segnali.length,
      nTotali: interoONull(payload.n_signals_total),
      nForti: interoONull(payload.n_signals_strong),
      perCategoria,
      copertura: leggiCopertura(payload),
      eta: leggiEta(payload),
      generated: testoONull(payload.generated),
      ultimaChiamataFallita: o.errore,
    };
  }

  if (o.errore) {
    return {
      stato: 'guasto', motivo: o.errore.motivo,
      origine: o.errore.timeout ? 'timeout' : 'chiamata', quando: null,
    };
  }

  if (oggetto(payload) && payload.error) {
    return {
      stato: 'guasto',
      motivo: taglia(String(payload.error), 300),
      origine: 'payload',
      quando: testoONull(payload._timestamp),
    };
  }
  if (payload == null) {
    return { stato: 'guasto', motivo: tr('edge.f068'), origine: 'chiamata', quando: null };
  }
  return {
    stato: 'guasto',
    motivo: tr('edge.f069'),
    origine: 'forma',
    quando: null,
  };
}

/* ── lo ZERO: tre zeri diversi, tre frasi diverse ────────────────── */

export interface Vuoto {
  /** `misurato` = zero su copertura piena · `parziale` = zero con rilevatori muti
   *  · `nd` = il backend non dichiara la copertura (o le righe arrivate non sono
   *  leggibili) · `filtro` = ci sono segnali ma non nella categoria scelta */
  tono: 'misurato' | 'parziale' | 'nd' | 'filtro';
  testo: string;
}

/** La frase dello stato vuoto. `nVisibili` = segnali dopo il filtro di categoria
 *  della pagina. Torna null se c'è qualcosa da mostrare. */
export function vuotoScan(
  v: ScanViva, soglia: number, categoria: string, nVisibili: number,
  sogliaMinima: number = SOGLIA_MINIMA_PAGINA,
): Vuoto | null {
  if (nVisibili > 0) return null;
  if (v.segnali.length > 0) {
    return {
      tono: 'filtro',
      testo: tr('edge.f070', {a: categoria, b: soglia})
        + tr('edge.f071', {a: v.segnali.length, b: v.segnali.length === 1 ? tr('edge.f072') : tr('edge.f073')}),
    };
  }
  if (v.illeggibili > 0) {
    return {
      tono: 'nd',
      testo: tr('edge.f074', {a: v.illeggibili, b: v.illeggibili === 1 ? tr('edge.f075') : tr('edge.f076'), c: soglia})
        + tr('edge.f077'),
    };
  }
  let totali = '';
  if (v.nTotali != null) {
    if (v.nTotali <= 0) totali = tr('edge.f078');
    else if (soglia <= sogliaMinima) {
      totali = tr('edge.f079', {a: v.nTotali, b: v.nTotali === 1 ? tr('edge.f072') : tr('edge.f073')})
        + tr('edge.f080', {a: sogliaMinima});
    } else {
      totali = tr('edge.f081', {a: v.nTotali, b: v.nTotali === 1 ? tr('edge.f072') : tr('edge.f073')});
    }
  }
  const c = v.copertura;
  if (!c.dichiarata) {
    return {
      tono: 'nd',
      testo: tr('edge.f082', {a: soglia, b: c.motivo, c: totali}),
    };
  }
  if (c.grado === 'degradata') {
    return {
      tono: 'parziale',
      testo: tr('edge.f083', {a: soglia, b: c.motivoDegrado})
        + tr('edge.f084') + totali,
    };
  }
  const caveat = c.soloPrezzo.length
    ? tr('edge.f085', {a: c.soloPrezzo.length, b: c.soloPrezzo.length === 1 ? tr('edge.f086') : tr('edge.f087')})
      + tr('edge.f088')
    : '';
  return {
    tono: 'misurato',
    testo: tr('edge.f089', {a: soglia, b: c.scansionate, c: c.totali})
      + tr('edge.f090') + caveat + totali,
  };
}

/* ── l'età della scansione, VIVA (invecchia fra un poll e l'altro) ── */

export interface EtaResa {
  /** secondi dalla FINE della scansione, adesso; null se non dichiarata */
  secondi: number | null;
  /** la frase intera */
  testo: string;
  /** la forma corta per un badge/cella: «12 min fa · CACHE» */
  breve: string;
  /** `scaduta` = oltre il TTL: la prossima chiamata può rifare la scansione */
  scaduta: boolean;
  tono: 'fresca' | 'cache' | 'scaduta' | 'nd';
}

/** `ricevutoAlMs` = quando la risposta è arrivata al client; `adessoMs` = l'orologio
 *  della pagina (si passa da fuori: niente orologio dentro un modulo che si prova). */
export function etaScan(eta: EtaScan, ricevutoAlMs: number, adessoMs: number): EtaResa {
  if (!eta.dichiarata) {
    return { secondi: null, testo: eta.motivo, breve: tr('edge.f091'), scaduta: false, tono: 'nd' };
  }
  const trascorsi = Number.isFinite(ricevutoAlMs) && Number.isFinite(adessoMs)
    ? Math.max(0, (adessoMs - ricevutoAlMs) / 1000) : 0;
  const secondi = eta.etaAllaRispostaS + trascorsi;
  const ora = eta.scansioneDelle ? oraIt(eta.scansioneDelle) : null;
  const quando = ora ? tr('edge.f092', {a: ora}) : '';
  const scaduta = eta.ttlS != null && secondi >= eta.ttlS;
  const ttl = eta.ttlS != null ? `TTL ${durata(eta.ttlS)}` : tr('edge.f093');
  const motivoTtl = eta.ttlMotivo ? ` — ${eta.ttlMotivo}` : '';
  // «può rifare», non «rifà»: lo stato del server non è misurato da qui (un altro
  // chiamante può averla già riscaldata, o la richiesta può accodarsi).
  const coda = scaduta
    ? tr('edge.f094')
      + tr('edge.f095', {a: Math.round(LATENZE_MISURATE.caldoS), b: Math.round(LATENZE_MISURATE.freddoS)})
    : '';
  if (eta.fuoriCache) {
    return {
      secondi, scaduta: false, tono: 'fresca',
      testo: tr('edge.f096', {a: durata(secondi), b: quando})
        + (eta.ttlMotivo ? ` — ${eta.ttlMotivo}` : '') + '.',
      breve: tr('edge.f097', {a: durata(secondi)}),
    };
  }
  if (eta.servitaDaCache) {
    return {
      secondi, scaduta, tono: scaduta ? 'scaduta' : 'cache',
      testo: tr('edge.f098', {a: durata(secondi), b: quando})
        + tr('edge.f099', {a: ttl, b: motivoTtl, c: coda}),
      breve: tr('edge.f100', {a: durata(secondi), b: scaduta ? tr('edge.f101') : 'CACHE'}),
    };
  }
  return {
    secondi, scaduta, tono: scaduta ? 'scaduta' : 'fresca',
    testo: tr('edge.f102', {a: durata(secondi), b: quando})
      + tr('edge.f103', {a: ttl, b: motivoTtl, c: coda}),
    breve: tr('edge.f100', {a: durata(secondi), b: scaduta ? tr('edge.f101') : tr('edge.f104')}),
  };
}

/* ── la copertura in una riga ─────────────────────────────────────── */

export interface RigaCopertura {
  /** i pezzi, nell'ordine in cui si leggono; `tono` dice con che colore */
  pezzi: { testo: string; tono: 'neutro' | 'allarme' }[];
  /** la prosa verbatim del backend, per il tooltip */
  nota: string;
}

export function rigaCopertura(c: Copertura): RigaCopertura {
  if (!c.dichiarata) return { pezzi: [{ testo: tr('edge.f105'), tono: 'allarme' }], nota: c.motivo };
  const p: RigaCopertura['pezzi'] = [];
  if (c.contate) {
    p.push({ testo: tr('edge.f106', {a: c.scansionate, b: c.totali}), tono: c.scansionate < c.totali ? 'allarme' : 'neutro' });
  } else {
    p.push({ testo: tr('edge.f107'), tono: 'allarme' });
  }
  if (c.piena.length) p.push({ testo: tr('edge.f108', {a: c.piena.length}), tono: 'neutro' });
  if (c.soloPrezzo.length) p.push({ testo: tr('edge.f109', {a: c.soloPrezzo.length}), tono: 'neutro' });
  const nDeg = Object.keys(c.degradata).length;
  if (nDeg) p.push({ testo: tr('edge.f110', {a: nDeg}), tono: 'allarme' });
  const nNM = Object.keys(c.nessunaMisura).length;
  if (nNM) p.push({ testo: tr('edge.f111', {a: nNM}), tono: 'allarme' });
  if (c.nonScansionate.length) p.push({ testo: tr('edge.f061', {a: c.nonScansionate.length}), tono: 'allarme' });
  if (c.fattorialiSu != null) {
    p.push({ testo: tr('edge.f112', {a: c.fattorialiSu}), tono: c.fattorialiSu < c.totali ? 'allarme' : 'neutro' });
  } else if (c.fattorialiKo) p.push({ testo: tr('edge.f113'), tono: 'allarme' });
  else p.push({ testo: tr('edge.f114'), tono: 'allarme' });
  return { pezzi: p, nota: c.nota };
}

/* ── l'attesa: tre regimi, e si dice da quanto ───────────────────── */

/** `~6,4 min`: il lock del backend, nella forma del ponte (non si arrotonda a «6 min»). */
function lockTesto(): string {
  return `~${fmtNum(LATENZE_MISURATE.lockS / 60, 1)} min`;
}

/** La copy mentre si aspetta. `limiteMs` = timeout della pagina (0 = nessun limite). */
export function copyAttesa(secondiTrascorsi: number, limiteMs: number, forzata: boolean): string {
  const L = LATENZE_MISURATE;
  const da = tr('edge.f115', {a: durata(Math.max(0, secondiTrascorsi))});
  const costo = tr('edge.f116', {a: Math.round(L.caldoS), b: Math.round(L.freddoS), c: L.posizioni, d: L.misurateIl});
  let s = forzata
    ? tr('edge.f117', {a: da, b: costo})
      + tr('edge.f118', {a: Math.round(2 * L.freddoS)})
    : tr('edge.f119', {a: da})
      + tr('edge.f120', {a: fmtNum(L.cacheS, 2), b: costo})
      + tr('edge.f121', {a: lockTesto()});
  if (secondiTrascorsi > L.freddoS) {
    s += tr('edge.f122');
  }
  s += limiteMs > 0 ? tr('edge.f123', {a: durata(limiteMs / 1000)}) : tr('edge.f124');
  return s;
}

/** L'esito di una chiamata fallita: il motivo (verbatim dove c'è un verbatim) e se
 *  è stata la PAGINA a smettere di aspettare. */
export function esitoChiamata(err: unknown, limiteMs: number): EsitoChiamata {
  const e = err as {
    code?: string; message?: string;
    response?: { status?: number; data?: { detail?: unknown; error?: unknown } };
  };
  const msg = String(e?.message || '');
  if (e?.code === 'ECONNABORTED' || /timeout of \d+ ?ms exceeded/i.test(msg)) {
    return {
      timeout: true,
      motivo: tr('edge.f125', {a: durata(limiteMs / 1000)})
        + tr('edge.f126', {a: Math.round(LATENZE_MISURATE.freddoS)})
        + tr('edge.f127')
        + tr('edge.f128', {a: durata(limiteMs / 1000)})
        + tr('edge.f129'),
    };
  }
  const stato = e?.response?.status;
  if (stato) {
    const det = leggiDetail(e?.response?.data?.detail) || testoONull(e?.response?.data?.error) || '';
    return { timeout: false, motivo: `HTTP ${stato}${det ? ' — ' + taglia(det, 300) : ''}` };
  }
  const m = msg.trim();
  return { timeout: false, motivo: m ? taglia(m, 300) : tr('edge.f130') };
}

/** Solo il motivo, per chi non ha bisogno di sapere se era un timeout. */
export function motivoChiamata(err: unknown, limiteMs: number): string {
  return esitoChiamata(err, limiteMs).motivo;
}

/* ── formati ─────────────────────────────────────────────────────── */

/** `12 s` · `3 min` · `1 h 5 min` · `2 h` — una durata leggibile, senza decimali. */
export function durata(s: number): string {
  if (!Number.isFinite(s) || s < 0) return tr('edge.f030');
  const tondi = Math.round(s);
  if (tondi < 60) return `${tondi} s`;
  const min = Math.floor(tondi / 60);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const resto = min % 60;
  return resto ? `${h} h ${resto} min` : `${h} h`;
}

/** `2026-08-22T14:52:03.123456` (ora locale del backend) → `14:52`. */
export function oraIt(iso: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(iso || ''));
  return m ? `${m[4]}:${m[5]}` : null;
}
