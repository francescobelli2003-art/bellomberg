import type { Intervallo } from '@/lib/fattori';
import { cifra, p2, suAsse } from './calcoli';
import type { Parole } from './parole';

/** Il «?» accanto ai titoli: la spiegazione lunga sta qui, raggiungibile anche da tastiera. */
export function Info({ testo }: { testo: string }) {
  return <span className="fat-info" tabIndex={0} role="img" aria-label={testo} title={testo}>?</span>;
}

/** Baffo di confidenza: il punto è la stima, la barra l'intervallo al 95%. Colorato solo se
 *  significativo; un coefficiente compatibile con zero resta grigio MENTRE attraversa lo zero. */
export function Baffo({ ic, limite, nome, unita = '', w }: {
  ic: Intervallo | null; limite: number; nome: string; unita?: string; w: Parole;
}) {
  if (!ic || ic.lo === null || ic.hi === null) {
    return <div className="fat-whisk is-empty" data-strato="baffo"><span className="ax" /><span className="z" /></div>;
  }
  const a = suAsse(ic.lo, limite), b = suAsse(ic.hi, limite), c = suAsse(ic.beta, limite);
  const u = (v: number | null) => cifra(v, 2, true) + unita;
  const lettura = w.whiskAria(nome, u(ic.beta), u(ic.lo), u(ic.hi), cifra(ic.t, 2),
    ic.sig ? w.whiskSig : ic.attraversaZero ? w.whiskZero : w.whiskNs);
  return (
    <div className={'fat-whisk' + (ic.sig ? ' is-sig' : '') + (ic.beta < 0 ? ' is-neg' : '')} data-strato="baffo"
      tabIndex={0} role="img" aria-label={lettura} title={lettura}>
      <span className="ax" /><span className="z" />
      <span className="ic" style={{ left: p2(a), width: p2(b - a) }} />
      <span className="pt" style={{ left: p2(c) }} />
    </div>
  );
}
