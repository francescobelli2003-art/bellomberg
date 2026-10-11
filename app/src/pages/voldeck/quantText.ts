import { t as tr } from '@/i18n/t';
import { linguaCorrente } from '@/i18n/lingua';
import * as vq from '@/lib/vol-quant';
import { finite, numText } from '@/lib/vol-deck';
import type { FlagItem, ForwardWhy } from './quant';

/* Testi del Vol Deck «quant» (10/10/2026, Opus 5.5): solo formattazione dei risultati di
   lib/vol-quant e dei motivi; nessun calcolo. Punti vol = frazione × 100 col segno esplicito. */

const lang = (): 'it' | 'en' => (linguaCorrente() === 'it' ? 'it' : 'en');
const na = () => tr('voldeck.ui_n_a_15');

/** Motivo di un n.d.: i codici della libreria nel suo testo, quelli della pagina nei nostri. */
export function whyText(code: ForwardWhy | vq.ReasonCode | null | undefined): string {
  if (!code) return '';
  if (code === 'no_download' || code === 'chain_loading' || code === 'chain_error' || code === 'rate_loading') return tr(`voldeck.q_why_${code}`);
  // mai «arbitraggio» senza livello: la varianza decrescente fra IV e' un segnale di modello (indicativo)
  if (code === 'calendar_arbitrage') return tr('voldeck.q_why_calendar_arbitrage');
  return vq.reasonText(code as vq.ReasonCode, lang());
}
/** «n.d. · motivo» (il motivo resta sempre leggibile accanto al buco). */
export const naWhy = (code: ForwardWhy | vq.ReasonCode | null | undefined) => code ? `${na()} · ${whyText(code)}` : na();

/** Punti vol con segno: 0,0123 → «+1,23 pt». */
export function ptText(v: number | null | undefined, digits = 2): string {
  if (!finite(v)) return na();
  return (v > 0 ? '+' : v < 0 ? '−' : '') + numText(Math.abs(v) * 100, digits) + ' pt';
}
/** Rapporto adimensionale con segno (skew normalizzato). */
export function ratioText(v: number | null | undefined, digits = 3): string {
  if (!finite(v)) return na();
  return (v > 0 ? '+' : v < 0 ? '−' : '') + numText(Math.abs(v), digits);
}

export const kindText = (kind: vq.ArbitrageKind) => tr(`voldeck.q_kind_${kind}`);
export const originText = (origin: vq.ArbitrageFlag['origin']) => tr(`voldeck.q_origin_${origin}`);
/** prova del flag: in prezzo eseguibile (bid/ask veri) o sul modello con la tolleranza di rumore */
export const testText = (test: vq.ArbitrageFlag['test']) => tr(`voldeck.q_test_${test}`);
/** Qualita' del forward (regola PM: residuo de-americanizzato e qualita' dove compare il forward). */
export function fwdQuality(res: vq.ForwardResult): string {
  return tr('voldeck.q_fwd_quality', { n: res.pairs.length, d: finite(res.dispersionRel) ? numText(res.dispersionRel * 100, 3) : na(),
    raw: finite(res.rawForward) ? numText(res.rawForward, 2) : na(),
    res: finite(res.deamericanizationResidual) ? numText(res.deamericanizationResidual, 6) : na() });
}
export const sourceText = (set: 'grid' | 'observed', source: vq.SmileSource | 'mixed') =>
  tr(set === 'observed' ? 'voldeck.q_src_observed' : source === 'mixed' ? 'voldeck.q_src_mixed' : 'voldeck.q_src_grid');

/** Entita' nell'unita' propria del tipo (le unita' non si confrontano fra tipi: v. R1). */
export function magnitudeText(f: vq.ArbitrageFlag, level: FlagItem['level'] = 'executable'): string {
  if (f.kind === 'calendar') return tr('voldeck.q_mag_calendar', { v: finite(f.volPoints) ? numText(f.volPoints * 100, 2) : na(), w: numText(f.magnitude * 1e4, 2) });
  // su bid/ask ma indicativo (giunzione put/call): mai «arbitraggio eseguibile» accanto a «indicativo»
  if (f.unit === 'price') return tr(level === 'executable' ? 'voldeck.q_mag_price' : 'voldeck.q_mag_price_ind', { v: numText(f.magnitude, 2) });
  if (f.unit === 'probability') return tr('voldeck.q_mag_mass', { v: numText(f.magnitude * 100, 3) });
  if (f.kind === 'butterfly') return tr('voldeck.q_mag_butterfly', { v: numText(f.magnitude * 100, 3) });
  if (f.kind === 'vertical') return tr('voldeck.q_mag_vertical', { v: numText(f.magnitude, 4) });
  return tr('voldeck.q_mag_density', { v: numText(f.magnitude, 4) });
}
export const levelText = (level: FlagItem['level']) => tr(`voldeck.q_level_${level}`);
export const ratioOfFlag = (item: FlagItem) => item.level === 'executable' ? tr('voldeck.q_ratio_exec')
  : item.flag.origin === 'junction' ? tr('voldeck.q_ratio_junction')
  : item.ratio == null ? tr('voldeck.q_ratio_na') : tr('voldeck.q_ratio', { v: numText(item.ratio, 1) });

/** Riga di lettura di un flag (hover di 3D, griglia, smile; riga della tabella). */
export function flagLine(item: FlagItem): string {
  const f = item.flag;
  return `${levelText(item.level)}${f.indicative ? ' (' + tr('voldeck.q_level_quoted') + ')' : ''} · ${kindText(f.kind)} · ${f.expiries.join(' → ')} · ${magnitudeText(f, item.level)} · ${ratioOfFlag(item)} · ${testText(f.test)} · ${originText(f.origin)} · ${sourceText(item.set, f.source)}`;
}
