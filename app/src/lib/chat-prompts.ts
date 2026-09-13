import { t as tr } from '@/i18n/t';
/** Generic research questions. Portfolio instruments are supplied by the user's API. */
export const AGENT_QUESTIONS: Record<string, string[]> = {
  get capo() { return [tr('communications.q_capo_a'), tr('communications.q_capo_b'), tr('communications.q_capo_c')]; },
  get macro() { return [tr('communications.q_macro_a'), tr('communications.q_macro_b'), tr('communications.q_macro_c')]; },
  get options() { return [tr('communications.q_options_a'), tr('communications.q_options_b'), tr('communications.q_options_c')]; },
  get quant() { return [tr('communications.q_quant_a'), tr('communications.q_quant_b'), tr('communications.q_quant_c')]; },
  get fundamentals() { return [tr('communications.q_fundamentals_a'), tr('communications.q_fundamentals_b'), tr('communications.q_fundamentals_c')]; },
  get crypto() { return [tr('communications.q_crypto_a'), tr('communications.q_crypto_b'), tr('communications.q_crypto_c')]; },
  get eventdesk() { return [tr('communications.q_eventdesk_a'), tr('communications.q_eventdesk_b'), tr('communications.q_eventdesk_c')]; },
  get politics() { return [tr('communications.q_politics_a'), tr('communications.q_politics_b'), tr('communications.q_politics_c')]; },
  get news() { return [tr('communications.q_news_a'), tr('communications.q_news_b'), tr('communications.q_news_c')]; },
};
function focusLabels(): Record<string, string> { return {
  capo: tr('communications.focus_capo'),
  macro: tr('communications.focus_macro'),
  options: tr('communications.focus_options'),
  quant: tr('communications.focus_quant'),
  fundamentals: tr('communications.focus_fundamentals'),
  crypto: tr('communications.focus_crypto'),
  eventdesk: tr('communications.focus_eventdesk'),
  politics: tr('communications.focus_politics'),
  news: tr('communications.focus_news'),
}; }
function checkedTicker(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Z0-9^][A-Z0-9.^=_/\-]{0,39}$/i.test(value.trim())) {
    throw new Error(tr('communications.invalidTicker'));
  }
  return value.trim();
}
export function portfolioTickers(positions: unknown): string[] {
  if (!Array.isArray(positions)) throw new Error(tr('communications.missingPositions'));
  return [...new Set(positions.map(row => checkedTicker(row?.ticker)))];
}
export function tickerPrompt(agent: string, ticker: string): string {
  const symbol = checkedTicker(ticker);
  const focus = focusLabels()[agent] || tr('communications.focusDefault');
  return tr('communications.tickerPrompt', {a: symbol, b: focus});
}
