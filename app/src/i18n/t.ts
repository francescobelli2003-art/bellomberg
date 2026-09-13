// t(): la funzione di traduzione, pura (niente React). I componenti passano
// da useT() (provider.tsx), le lib pure da traduci(lingua, …) o t(…).
import { it } from './it/index.js';
import { en } from './en/index.js';
import { linguaCorrente, type Lingua } from './lingua.js';

export type Dizionari = typeof it;
/** 'spazio.chiave' per ogni chiave dei dizionari: una chiave inventata e' un
 *  errore di tsc, non una sorpresa a runtime. */
export type Chiave = {
  [NS in keyof Dizionari & string]: `${NS}.${keyof Dizionari[NS] & string}`;
}[keyof Dizionari & string];
export type Parametri = Record<string, string | number>;

const DIZIONARI: Record<Lingua, Dizionari> = { it, en };

/** Il marcatore che l'utente VEDE quando una chiave manca (regola 14/07:
 *  mai l'italiano zitto al posto dell'inglese mancante). */
export const MARCATORE_MANCANTE = (chiave: string) => `⟦${chiave}⟧`;

function cerca(lingua: Lingua, chiave: string): string | undefined {
  const punto = chiave.indexOf('.');
  if (punto <= 0) return undefined;
  const ns = chiave.slice(0, punto), k = chiave.slice(punto + 1);
  const spazio = (DIZIONARI[lingua] as unknown as Record<string, Record<string, string>>)[ns];
  const v = spazio ? spazio[k] : undefined;
  return typeof v === 'string' ? v : undefined;
}

function interpola(lingua: Lingua, chiave: string, testo: string, parametri?: Parametri): string {
  return testo.replace(/\{([a-zA-Z_]+)\}/g, (intero, nome: string) => {
    const v = parametri ? parametri[nome] : undefined;
    if (v === undefined) {
      console.error(`[i18n] parametro {${nome}} mancante per ${lingua}:${chiave}`);
      return intero;                                       // resta visibile: {nome}
    }
    return String(v);
  });
}

/** Chiave costruita a runtime (es. `stato.${x}`): il tipo non la vede, quindi
 *  la mancanza si dichiara qui — marcatore visibile + console.error. */
export function tDinamica(lingua: Lingua, chiave: string, parametri?: Parametri): string {
  const testo = cerca(lingua, chiave);
  if (testo === undefined) {
    console.error(`[i18n] chiave mancante: ${lingua}:${chiave}`);
    return MARCATORE_MANCANTE(chiave);
  }
  return interpola(lingua, chiave, testo, parametri);
}

/** Traduzione con lingua esplicita: per le lib pure e per i test. */
export function traduci(lingua: Lingua, chiave: Chiave, parametri?: Parametri): string {
  return tDinamica(lingua, chiave, parametri);
}

/** Traduzione nella lingua corrente della UI (v. lingua.ts). */
export function t(chiave: Chiave, parametri?: Parametri): string {
  return tDinamica(linguaCorrente(), chiave, parametri);
}

/** Sceglie la chiave singolare o plurale in base a n (niente ICU: due chiavi). */
export function plurale(lingua: Lingua, n: number, uno: Chiave, molti: Chiave, parametri?: Parametri): string {
  return traduci(lingua, n === 1 ? uno : molti, { n, ...(parametri || {}) });
}
