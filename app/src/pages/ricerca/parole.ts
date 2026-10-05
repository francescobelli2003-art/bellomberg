import { linguaCorrente } from '@/i18n/lingua';
import { traduci, type Chiave, type Parametri } from '@/i18n/t';

/** Le chiavi della pagina, sempre scritte per intero (catalogo i18n/it|en/edge.ts). */
export type ChiavePagina = Extract<Chiave, `edge.${string}`>;

/** I testi della pagina Ricerca opportunità nella lingua corrente. */
export function parole() {
  const l = linguaCorrente();
  const t = (k: ChiavePagina, p?: Parametri) => traduci(l, k, p);
  /** forma singolare o plurale secondo `quanti`, passato anche come `{n}` */
  const n = (uno: ChiavePagina, altri: ChiavePagina, quanti: number, p?: Parametri) =>
    traduci(l, quanti === 1 ? uno : altri, { n: quanti, ...p });
  return { lingua: l, t, n };
}
export type Parole = ReturnType<typeof parole>;
