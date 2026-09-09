export const fmtEUR = (v: number | null | undefined, sign = false, dec = 2) => {
  if (v == null || !isFinite(v)) return 'n/a';
  const opts: Intl.NumberFormatOptions = { style: 'currency', currency: 'EUR', maximumFractionDigits: dec };
  const s = new Intl.NumberFormat('it-IT', opts).format(v);
  return sign && v > 0 ? '+' + s : s;
};
export const fmtPct = (v: number | null | undefined, sign = true, dec = 2) => {
  if (v == null || !isFinite(v)) return 'n/a';
  return (sign && v > 0 ? '+' : '') + v.toFixed(dec) + '%';
};
export const fmtNum = (v: number | null | undefined, dec = 2) => {
  if (v == null || isNaN(v)) return 'n/a';
  return v.toLocaleString('it-IT', { minimumFractionDigits: dec, maximumFractionDigits: dec });
};
