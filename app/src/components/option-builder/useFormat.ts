// Locale-aware number/date formatting for the option builder (09/10/2026, Opus 5.5).
import { useMemo } from 'react';
import { useLingua } from '@/i18n/provider';
import { t as tr } from '@/i18n/t';
import { localeDi } from '@/i18n/lingua';

export function useFormat() {
  const language = useLingua();
  const locale = localeDi(language);
  return useMemo(() => {
    const nf = (d: number, sign = false) => new Intl.NumberFormat(locale, { minimumFractionDigits: d, maximumFractionDigits: d, signDisplay: sign ? 'exceptZero' : 'auto' });
    const na = tr('optionbuilder.na');
    return {
      language,
      num: (v: number | null | undefined, d = 2) => v == null || !Number.isFinite(v) ? na : nf(d).format(v),
      signed: (v: number | null | undefined, d = 0) => v == null || !Number.isFinite(v) ? na : nf(d, true).format(v),
      pct: (v: number | null | undefined, d = 1) => v == null || !Number.isFinite(v) ? na : nf(d).format(v * 100) + '%',
      date: (iso: string) => new Date(iso + 'T12:00:00Z').toLocaleDateString(locale, { day: 'numeric', month: 'short', year: '2-digit', timeZone: 'UTC' }),
      strike: (k: number) => new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(k),
      shortDate: (iso: string) => new Date(iso + 'T12:00:00Z').toLocaleDateString(locale, { day: 'numeric', month: 'short', timeZone: 'UTC' }),
      time: (iso: string | null | undefined) => { if (!iso) return na; const d = new Date(iso); return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString(locale, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); },
      na,
    };
  }, [locale, language]);
}

