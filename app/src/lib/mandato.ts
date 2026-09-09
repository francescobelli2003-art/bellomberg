export const BLOCCHI_MANDATO = ['profilo', 'rischio', 'sizing', 'cassa', 'disciplina', 'opzioni', 'note'] as const;
export type BloccoMandato = typeof BLOCCHI_MANDATO[number];

export interface CampoMandato {
  blocco: BloccoMandato;
  tipo: 'scelta'|'bool'|'int'|'num'|'pct'|'intervallo_pct'|'intervallo_int'|'lista'|'lista_testo'|'interruttori'|'testo'|'valuta';
  obbligatorio: boolean;
  descrizione: string;
  unita: string;
  intervallo: [number, number] | null;
  scelte: string[] | null;
  massimo_char: number | null;
}

export type ValoriMandato = Record<string, unknown> & Record<BloccoMandato, Record<string, unknown>>;
export interface StatoMandato {
  dichiarato: boolean; causa: string | null; dettaglio: string | null;
  campi_mancanti: string[]; valori: ValoriMandato | null; origine: string | null;
  impronta: string | null; dichiarato_il: string | null;
  campi: Record<string, CampoMandato>; errori: string[]; esempio: ValoriMandato | null;
}
export interface AnteprimaMandato { testo: string; impronta: string; origine: string }
export type LetturaMandato = { ok: true; valore: number | null } | { ok: false; motivo: string };

export function leggiNumeroMandato(s: string, campo: Pick<CampoMandato, 'tipo'|'obbligatorio'|'intervallo'>): LetturaMandato {
  const t = (s || '').trim().replace(/\s/g, '');
  if (!t) return campo.obbligatorio ? { ok: false, motivo: 'campo obbligatorio' } : { ok: true, valore: null };
  if (!/^[0-9.,]+$/.test(t)) return { ok: false, motivo: 'usa solo cifre, virgola o punto' };
  const virgole = (t.match(/,/g) || []).length, punti = (t.match(/\./g) || []).length;
  if (virgole > 1) return { ok: false, motivo: 'più di una virgola' };
  let normalizzato = t;
  if (virgole === 1) {
    if (punti && !/^[1-9]\d{0,2}(\.\d{3})*,\d+$/.test(t)) return { ok: false, motivo: 'separatori in posizione ambigua' };
    normalizzato = t.replace(/\./g, '').replace(',', '.');
  } else if (punti === 1) {
    const [intero, decimali] = t.split('.');
    if (decimali.length === 3 && intero !== '0') return { ok: false, motivo: `${t} è ambiguo: usa la virgola per i decimali` };
  } else if (punti > 1) {
    if (!/^[1-9]\d{0,2}(\.\d{3})+$/.test(t)) return { ok: false, motivo: 'punti in posizione non valida' };
    normalizzato = t.replace(/\./g, '');
  }
  const valore = Number(normalizzato);
  if (!Number.isFinite(valore)) return { ok: false, motivo: 'non è un numero' };
  if ((campo.tipo === 'int' || campo.tipo === 'intervallo_int') && !Number.isInteger(valore)) return { ok: false, motivo: 'serve un numero intero' };
  if (campo.intervallo && (valore < campo.intervallo[0] || valore > campo.intervallo[1])) return { ok: false, motivo: `deve essere fra ${campo.intervallo[0]} e ${campo.intervallo[1]}` };
  return { ok: true, valore };
}

export function dettaglioLeggibile(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown }; status?: number }; message?: string };
  const d = e?.response?.data?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x: any) => {
    if (typeof x === 'string') return x;
    const dove = Array.isArray(x?.loc) ? x.loc.filter((v: unknown) => v !== 'body').join('.') : '';
    return [dove, x?.msg || JSON.stringify(x)].filter(Boolean).join(': ');
  }).join('\n');
  if (d != null) { try { return JSON.stringify(d); } catch { return String(d); } }
  return e?.message || 'errore sconosciuto';
}
export function statusHttp(err: unknown): number | null { const n = (err as any)?.response?.status; return typeof n === 'number' ? n : null; }
export function rispostaDellaRevisione(attesa: number, corrente: number): boolean { return attesa === corrente; }
const RIFIUTO_RUN_KEY = 'bellomberg_mandato_rifiuto_run_v1';
export const RIFIUTO_RUN_EVENT = 'bb:mandato-run-refused';
export function conservaDettaglioRun(dettaglio: string): void {
  try { sessionStorage.setItem(RIFIUTO_RUN_KEY, dettaglio); } catch {}
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(RIFIUTO_RUN_EVENT, { detail: dettaglio }));
}
export function consumaDettaglioRun(): string | null { try { const d = sessionStorage.getItem(RIFIUTO_RUN_KEY); sessionStorage.removeItem(RIFIUTO_RUN_KEY); return d; } catch { return null; } }

const MOTORE = new Set(['var99_1g_pct','stress_gfc_pct','base_single_pct','cap_single_pct','base_veicolo_pct','cap_veicolo_pct','cap_settore_pct','limite_minimo_pct','posizione_minima_pct']);
const SOLO_INFO = new Set(['broker','residenza_fiscale']);
const VALIDAZIONE = new Set(['volatilita_target_pct','drawdown_max_pct']);
export function coperturaCampo(nome: string): { etichetta: string; nota: string; classe: string } {
  if (MOTORE.has(nome)) return { etichetta: 'MOTORE', nota: 'usato da un controllo o calcolo Python', classe: 'motore' };
  if (VALIDAZIONE.has(nome)) return { etichetta: 'VALIDAZIONE', nota: 'controllato per coerenza; non impone il sizing', classe: 'validazione' };
  if (SOLO_INFO.has(nome)) return { etichetta: 'INFORMATIVO', nota: 'entra nel profilo; nessun controllo automatico', classe: 'informativo' };
  return { etichetta: 'PROMPT', nota: 'istruzione ai modelli; nessun blocco deterministico', classe: 'prompt' };
}

// Nome pubblico della voce sessionStorage, condiviso fra salvataggio e recupero.
export const CHIAVE_BOZZA_MANDATO = 'bellomberg_mandato_bozza_v1';
export interface BozzaMandato { baseImpronta: string | null; form: Record<string, unknown>; metadati?: Record<string, unknown>; salvataIl: string }
export function leggiBozza(): BozzaMandato | null { try { const x = JSON.parse(sessionStorage.getItem(CHIAVE_BOZZA_MANDATO) || 'null'); return x && typeof x === 'object' && x.form ? x as BozzaMandato : null; } catch { return null; } }
export function salvaBozza(bozza: BozzaMandato): void { try { sessionStorage.setItem(CHIAVE_BOZZA_MANDATO, JSON.stringify(bozza)); } catch {} }
export function eliminaBozza(): void { try { sessionStorage.removeItem(CHIAVE_BOZZA_MANDATO); } catch {} }
