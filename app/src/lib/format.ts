import { linguaCorrente, localeDi, type Lingua } from '../i18n/lingua.js';
export { localeDi } from '../i18n/lingua.js';

export const fmtEUR = (v: number | null | undefined, sign = false, dec = 2, lingua: Lingua = linguaCorrente()) => {
  if (v == null || !isFinite(v)) return 'n/a';
  const opts: Intl.NumberFormatOptions = { style: 'currency', currency: 'EUR', maximumFractionDigits: dec, useGrouping: true };
  const s = new Intl.NumberFormat(localeDi(lingua), opts).format(v);
  return sign && v > 0 ? '+' + s : s;
};
export const fmtPct = (v: number | null | undefined, sign = true, dec = 2, lingua: Lingua = linguaCorrente()) => {
  if (v == null || !isFinite(v)) return 'n/a';
  return (sign && v > 0 ? '+' : '') + v.toLocaleString(localeDi(lingua), { minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: false }) + '%';
};
export const fmtNum = (v: number | null | undefined, dec = 2, lingua: Lingua = linguaCorrente()) => {
  if (v == null || !isFinite(v)) return 'n/a';
  return v.toLocaleString(localeDi(lingua), { minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: true });
};

export const fmtDataBreve = (d: Date, lingua: Lingua = linguaCorrente()) =>
  new Intl.DateTimeFormat(localeDi(lingua), { day: '2-digit', month: 'short', year: 'numeric' })
    .formatToParts(d).map(p => p.type === 'month' ? p.value.slice(0, 3) : p.value).join('').toUpperCase();
export const fmtOra = (d: Date, lingua: Lingua = linguaCorrente()) =>
  d.toLocaleTimeString(localeDi(lingua), { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
