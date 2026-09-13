import { t as tr } from '@/i18n/t';
interface PortfolioValuesInput {
  totale_valore_mercato_eur?: number | null;
  cash_disponibile_eur?: number | null;
  nav_total_eur?: number | null;
  cash_source?: string | null;
  cash_source_note?: string | null;
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/** A declared missing cash source also invalidates totals depending on cash. */
export function portfolioValues(snapshot: PortfolioValuesInput | null) {
  const invested = finite(snapshot?.totale_valore_mercato_eur) ? snapshot.totale_valore_mercato_eur : null;
  const cash = snapshot?.cash_source === 'sqlite:cash_state' && finite(snapshot.cash_disponibile_eur)
    ? snapshot.cash_disponibile_eur : null;
  const nav = cash !== null && finite(snapshot?.nav_total_eur) ? snapshot.nav_total_eur : null;
  const note = cash === null
    ? snapshot?.cash_source_note || tr('dashboard.cash_unavailable')
    : nav === null ? tr('dashboard.assets_unavailable') : null;
  return { invested, cash, nav, note };
}
