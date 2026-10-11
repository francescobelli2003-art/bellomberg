import { t as tr } from '@/i18n/t';
import { linguaCorrente } from '@/i18n/lingua';
import * as vq from '@/lib/vol-quant';
import { finite, numText } from '@/lib/vol-deck';
import type { FlagItem, ForwardWhy, VolQuant } from './quant';

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
/** Sedute (New York) delle chiusure usate dal livello 2: una data, un intervallo, o «non riportata». */
function daysText(days: string[]): string | null {
  const d = [...new Set(days)].sort();
  return d.length === 0 ? null : d.length === 1 ? d[0] : `${d[0]} – ${d[d.length - 1]}`;
}
const closeDaysText = (res: vq.LeveledForward) => daysText(res.closeDays || []);
/** Avvisi del forward (livello 2) in parole, separati da « · »; '' se nessuno. */
export function fwdWarningsText(res: vq.LeveledForward | null | undefined): string {
  if (!res) return '';
  const out = (res.warnings || []).map(w => tr(`voldeck.q_fwd_warn_${w}`));
  const ex = res.closeExcluded;
  if (res.level === 'parity_close' && ex && (ex.otherSession || ex.undated)) out.push(tr('voldeck.q_fwd_close_excluded', { a: ex.otherSession, b: ex.undated }));
  return out.join(' · ');
}
/** Il numero poggia su un forward PROXY (livelli 3 e 3b)? Allora va marcato «proxy» dove compare. */
export const onProxy = (q: VolQuant, expiry: string | null | undefined): boolean => Boolean(expiry) && vq.isProxyLevel(q.forwards[expiry as string]?.level);
/** Testo del suggerimento di una cella che poggia sul proxy (tooltip). */
export const proxyHint = (q: VolQuant, expiry: string): string => tr('voldeck.q_proxy_hint', { l: fwdLevelText(q.forwards[expiry]) });
/** Livello del forward in parole (gerarchia dichiarata 11/10): fonte e, per il proxy, cosa ignora. */
export function fwdLevelText(res: vq.LeveledForward | null | undefined): string {
  if (!res?.level) return '';
  if (res.level === 'parity_quotes') return tr('voldeck.q_fwd_level_quotes');
  if (res.level === 'parity_close') {
    const d = closeDaysText(res);
    return d ? tr('voldeck.q_fwd_level_close', { d }) : tr('voldeck.q_fwd_level_close_undated');
  }
  if (res.level === 'proxy_carry_q' && res.dividend)
    return tr('voldeck.q_fwd_level_proxy_q', { q: numText(res.dividend.q * 100, 2), s: res.dividend.source, d: res.dividend.asOf || na() });
  return tr('voldeck.q_fwd_level_proxy');
}
/** Etichetta breve del livello accanto a un numero (tabelle). */
export const fwdLevelTag = (level: vq.ForwardLevel | null | undefined): string => level ? tr(`voldeck.q_fwd_tag_${level}`) : '';
/** Qualita' del forward (regola PM: residuo de-americanizzato e qualita' dove compare il forward). */
export function fwdQuality(res: vq.ForwardResult | vq.LeveledForward): string {
  const level = (res as vq.LeveledForward).level;
  if (level === 'proxy_carry') return tr('voldeck.q_fwd_q_proxy');
  if (level === 'proxy_carry_q') return fwdLevelText(res as vq.LeveledForward) + ' · ' + tr('voldeck.q_fwd_q_proxy_q');
  if (level === 'parity_close') {
    const w = fwdWarningsText(res as vq.LeveledForward);
    return fwdLevelText(res as vq.LeveledForward) + ' · ' + tr('voldeck.q_fwd_q_close', { n: res.pairs.length,
      d: finite(res.dispersionRel) ? numText(res.dispersionRel * 100, 3) : na() }) + (w ? ' · ' + w : '');
  }
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

/** Una riga sola sul forward della pagina: livello per livello quante scadenze, o il motivo se manca. */
export function forwardBasisLine(q: VolQuant, expiries: string[]): string {
  const total = expiries.length;
  const parts = vq.FORWARD_LEVELS.filter(l => q.forwardLevels[l]).map(l => {
    const all = Object.values(q.forwards).filter(f => f.level === l);
    // livello 2: le sedute di TUTTI i forward del livello (review 11/10), e quanti portano un avviso
    if (l === 'parity_close') {
      const d = daysText(all.flatMap(f => f.closeDays || []));
      const warned = all.filter(f => (f.warnings || []).length).length;
      return `${d ? tr('voldeck.q_fwd_level_close', { d }) : tr('voldeck.q_fwd_level_close_undated')} (${q.forwardLevels[l]}/${total})`
        + (warned ? ' · ' + tr('voldeck.q_fwd_warned', { n: warned }) : '');
    }
    return `${fwdLevelText(all[0])} (${q.forwardLevels[l]}/${total})`;
  });
  if (parts.length) return `${tr('voldeck.q_fwd_basis')}: ${parts.join(' · ')}`;
  const why = expiries.map(e => q.forwardOf(e).why).find(Boolean);
  return tr('voldeck.q_fwd_basis_none', { why: whyText(why ?? 'forward_missing') });
}
