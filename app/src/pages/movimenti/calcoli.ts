/* Derivazioni della pagina Movimenti (stile Nuova). Restano pure: niente React, niente rete.
   Le regole sui dati sono quelle di lib/movimenti.ts; qui solo ciò che serve alle nuove card. */
import {
  chiaveMese, controvalore, entra, esce, testiDiRiga, tickerDiRiga, versoDi,
  type RigaRegistro, type Trade,
} from '@/lib/movimenti';

export type FiltroMov = 'TUTTI' | 'BUY' | 'ADD' | 'USCITE' | 'DIVIDEND' | 'CASSA' | 'COMMENTO';
export const FILTRI: FiltroMov[] = ['TUTTI', 'BUY', 'ADD', 'USCITE', 'DIVIDEND', 'CASSA', 'COMMENTO'];

/** Le due viste della card di sinistra: il Registro (elenco filtrabile) o il Diario (i soli commenti, in fila). */
export type VistaMov = 'elenco' | 'diario';

/** Il filtro per verbo o per specie. «Uscite» tiene insieme TRIM e SELL, come `esce`. */
export function passaFiltro(r: RigaRegistro, f: FiltroMov): boolean {
  if (f === 'TUTTI') return true;
  if (f === 'COMMENTO') return !testiDiRiga(r).vuoto;
  if (f === 'CASSA') return r.specie === 'cassa';
  if (r.specie !== 'titolo') return false;
  if (f === 'USCITE') return esce(r.t.action);
  return r.t.action === f;
}

/** Le righe del Diario: ogni movimento letto che porta un testo (motivo, nota o causale), in ordine di data
 *  dal più recente. L'ordine è quello di `fondiRegistro` (data vera, poi specie, poi id; le date illeggibili in
 *  fondo): qui NON si riordina, così Diario e Registro non possono mai dire due cronologie diverse.
 *  ⚠️ Lavora su TUTTE le righe lette, non su quelle filtrate: i filtri stanno nella card del Registro e nel
 *  Diario non si vedono, quindi non devono stringerlo di nascosto. */
export const righeDiario = <V extends { r: RigaRegistro }>(tutte: V[]): V[] =>
  tutte.filter(v => passaFiltro(v.r, 'COMMENTO'));

/** Ricerca libera su ticker, motivo, nota e causale; maiuscole e minuscole indifferenti. */
export function passaRicerca(r: RigaRegistro, q: string): boolean {
  const s = q.trim().toLocaleLowerCase();
  if (!s) return true;
  const { rationale, nota } = testiDiRiga(r);
  return [tickerDiRiga(r) || '', rationale, nota].some(x => x.toLocaleLowerCase().includes(s));
}

/** Identità di una riga dentro una lettura: specie, id quando c'è, posizione nel registro.
 *  La posizione rende unica anche una riga legacy senza id. */
export const chiaveRiga = (r: RigaRegistro, i: number) =>
  r.specie === 'cassa' ? `c:${r.m.id ?? 'x'}:${i}` : `t:${r.t.id ?? 'x'}:${i}`;

// ── attività per mese ──────────────────────────────────────────
export interface MeseAttivita {
  chiave: string;
  /** righe del mese, di ogni specie */
  n: number;
  nTitoli: number;
  /** controvalore lordo in EUR di BUY/ADD e di TRIM/SELL, solo righe in EUR */
  entrate: number;
  uscite: number;
  /** versamenti meno prelievi (la cassa è sempre in EUR) */
  cassa: number;
}
export interface Attivita {
  mesi: MeseAttivita[];
  /** righe su titoli in un'altra valuta: contate nel mese, NON sommate (niente cambio inventato) */
  altreValute: number;
  /** righe con data illeggibile: fuori dal grafico, dichiarate */
  senzaData: number;
  /** i mesi letti erano più di quelli mostrati */
  tagliati: boolean;
}

const mesePiu = (k: string, d: number) => {
  const y = Number(k.slice(0, 4)), m = Number(k.slice(5, 7)) - 1 + d;
  const yy = y + Math.floor(m / 12), mm = ((m % 12) + 12) % 12;
  return `${yy}-${String(mm + 1).padStart(2, '0')}`;
};

/** Mesi consecutivi dal più vecchio al più recente (i mesi vuoti restano, a zero), al massimo `max`. */
export function attivitaMensile(registro: RigaRegistro[], max = 12): Attivita {
  const per = new Map<string, MeseAttivita & { tk: Set<string> }>();
  let altreValute = 0, senzaData = 0;
  for (const r of registro) {
    const k = chiaveMese(r.quando);
    if (!k) { senzaData++; continue; }
    let g = per.get(k);
    if (!g) { g = { chiave: k, n: 0, nTitoli: 0, entrate: 0, uscite: 0, cassa: 0, tk: new Set() }; per.set(k, g); }
    g.n++;
    if (r.specie === 'cassa') {
      const v = r.m.amount_eur, verso = versoDi(r.m.type);
      if (typeof v === 'number' && isFinite(v) && verso !== 'ignoto') g.cassa += verso === 'dentro' ? Math.abs(v) : -Math.abs(v);
      continue;
    }
    const t = r.t;
    g.tk.add(t.ticker);
    if (!entra(t.action) && !esce(t.action)) continue;
    const c = controvalore(t);
    if (c == null) continue;
    if ((t.valuta || '').toUpperCase() !== 'EUR') { altreValute++; continue; }
    if (entra(t.action)) g.entrate += c; else g.uscite += c;
  }
  const chiavi = [...per.keys()].sort();
  if (!chiavi.length) return { mesi: [], altreValute, senzaData, tagliati: false };
  const tutte: string[] = [];
  for (let k = chiavi[0], giri = 0; k <= chiavi[chiavi.length - 1] && giri < 600; k = mesePiu(k, 1), giri++) tutte.push(k);
  const viste = tutte.slice(-max);
  return {
    mesi: viste.map(k => {
      const g = per.get(k);
      return g ? { chiave: k, n: g.n, nTitoli: g.tk.size, entrate: g.entrate, uscite: g.uscite, cassa: g.cassa }
        : { chiave: k, n: 0, nTitoli: 0, entrate: 0, uscite: 0, cassa: 0 };
    }),
    altreValute, senzaData, tagliati: tutte.length > viste.length,
  };
}

// ── realizzato per titolo ──────────────────────────────────────
export interface Contributo { ticker: string; somma: number; quota: number }

/** Il realizzato in EUR per titolo, sulle stesse righe di `contaRealizzato` (valore numerico).
 *  `quota` è il peso sul totale in valore assoluto: resta fra 0 e 1 anche con guadagni e perdite. */
export function contributi(trades: Trade[], quanti = 4): Contributo[] {
  const per = new Map<string, number>();
  for (const t of trades) {
    if (typeof t.realized_eur !== 'number' || !isFinite(t.realized_eur)) continue;
    per.set(t.ticker, (per.get(t.ticker) ?? 0) + t.realized_eur);
  }
  const voci = [...per.entries()].filter(([, v]) => v !== 0);
  const assoluto = voci.reduce((s, [, v]) => s + Math.abs(v), 0);
  return voci
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]) || a[0].localeCompare(b[0]))
    .slice(0, quanti)
    .map(([ticker, somma]) => ({ ticker, somma, quota: assoluto > 0 ? Math.abs(somma) / assoluto : 0 }));
}

/** Etichetta di mese in sentence case dal separatore di lib/movimenti («SETTEMBRE 2026» → «Settembre 2026»). */
export const fraseMese = (etichetta: string, locale: string) => {
  const s = etichetta.toLocaleLowerCase(locale);
  return s.charAt(0).toLocaleUpperCase(locale) + s.slice(1);
};
