// Grammatica dei numeri scritti a mano, PER LINGUA. Cuore condiviso da
// lib/cassa.ts (leggiNumero, leggiNumeroConSegno) e lib/mandato.ts
// (leggiNumeroMandato). Vive qui e non in lib/ perche' mandato.ts viene
// compilato da solo in CI (tests/mandato/tsconfig.json, NodeNext) e puo'
// importare solo con percorsi relativi `.js`.
//
// ⚠ MISURATO (rapporto 12/09, §3.3): con la sola grammatica italiana
// `leggiNumero("1,234.56")` valeva 1.23456 — mille volte meno, senza errore.
// E' la stessa classe di difetto della virgola del 30/07 (`158,50` → 15850),
// a parti invertite. Regola di casa: L'AMBIGUO SI RIFIUTA, NON SI INDOVINA.
//
// Per ogni lingua c'e' un separatore DECIMALE (D) e uno delle MIGLIAIA (T):
//   it: D = ','  T = '.'      en: D = '.'  T = ','
//  1. solo cifre e i due separatori;
//  2. un solo D; i T, se ci sono, in gruppi veri (1-3 cifre, poi gruppi da 3);
//  3. un T nella parte decimale = grafia dell'altra lingua → errore che
//     suggerisce la grafia giusta (`1,234.56` in italiano);
//  4. un SOLO separatore T con esattamente 3 cifre dopo (`1.234` in it,
//     `1,234` in en) e' ambiguo fra migliaia e decimali → errore che dice
//     come scriverlo. Con parte intera `0` l'ambiguita' non esiste (`0.125`);
//  5. un solo T che NON puo' essere migliaia (`12.5` in it, `158,50` in en)
//     si legge come decimale: e' la regola storica di cassa.ts, specchiata.
// Proprieta' provata in tests/i18n/numeri.test.cjs: la stessa stringa non
// vale mai due numeri diversi a seconda della lingua.
import type { Lingua } from './lingua.js';
import { traduci } from './t.js';

export type LetturaNumero =
  | { ok: true; valore: number }
  | { ok: false; motivo: string };

const SEPARATORI: Record<Lingua, { D: string; T: string }> = {
  it: { D: ',', T: '.' },
  en: { D: '.', T: ',' },
};

const scappa = (c: string) => c.replace(/[.]/g, '\\.');

/** Legge una stringa scritta a mano nella grammatica di `lingua`.
 *  null = campo vuoto (assenza, non errore). */
export function analizzaNumero(s: string, lingua: Lingua, linguaMessaggio: Lingua = lingua): LetturaNumero | null {
  const t = (s || '').trim().replace(/\s/g, '');
  if (!t) return null;
  if (!/^[0-9.,]+$/.test(t)) return { ok: false, motivo: traduci(linguaMessaggio, 'numeri.solo_cifre') };

  const { D, T } = SEPARATORI[lingua];
  const nD = t.split(D).length - 1;
  const nT = t.split(T).length - 1;
  const formaMigliaia = new RegExp(`^[1-9]\\d{0,2}(${scappa(T)}\\d{3})+$`);

  let norm: string;
  if (nD > 1) return { ok: false, motivo: traduci(linguaMessaggio, 'numeri.piu_di_un_decimale') };
  const migliaiaErrate = () => traduci(linguaMessaggio,
    linguaMessaggio === lingua ? 'numeri.migliaia_posizione' : 'numeri.migliaia_richieste',
    { esempio: ['1', '234', '567'].join(T) });

  if (nD === 0 && nT === 0) {
    norm = t;
  } else if (nD === 1 && nT === 0) {
    norm = t.replace(D, '.');                              // decimale nativo: 158,50 (it) · 158.50 (en)
  } else if (nD === 0 && nT === 1) {
    const [int, dec] = t.split(T);
    if (dec.length === 3 && int !== '0' && (int + dec).length > 3) {
      return {
        ok: false,
        motivo: traduci(linguaMessaggio, 'numeri.ambiguo', { t, senza: int + dec, decimale: int + D + dec }),
      };
    }
    norm = int + '.' + dec;                                // 12.5 (it) · 158,50 (en): non puo' essere migliaia
  } else if (nD === 1) {
    const [int, dec] = t.split(D);
    if (dec.includes(T)) {
      return { ok: false, motivo: traduci(linguaMessaggio,
        linguaMessaggio === lingua ? 'numeri.grafia_estranea' : 'numeri.grafia_richiesta',
        { t, esempio: '1' + T + '234' + D + '56' }) };
    }
    if (!formaMigliaia.test(int)) return { ok: false, motivo: migliaiaErrate() };
    norm = int.split(T).join('') + '.' + dec;              // 1.234,56 (it) · 1,234.56 (en)
  } else {
    if (!formaMigliaia.test(t)) return { ok: false, motivo: migliaiaErrate() };
    norm = t.split(T).join('');                            // 3.850.000 (it) · 3,850,000 (en)
  }

  const n = Number(norm);
  if (!isFinite(n)) return { ok: false, motivo: traduci(linguaMessaggio, 'numeri.non_numero') };
  return { ok: true, valore: n };
}

/** Scrive un numero nella grafia della lingua, con al massimo `decimali`
 *  cifre e senza zeri finali (12.345 → "12,345" in it, "12.345" in en).
 *  E' la controparte di analizzaNumero: cio' che scrive, l'altra rilegge. */
export function scriviNumero(v: number, lingua: Lingua, decimali = 4): string {
  const { D } = SEPARATORI[lingua];
  return String(Number(v.toFixed(decimali))).replace('.', D);
}
