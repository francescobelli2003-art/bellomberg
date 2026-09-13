import { useEffect, useState, type ReactNode } from 'react';
import { useLingua } from '@/i18n/provider';
import { linguaCorrente, localeDi } from '@/i18n/lingua';

/* 206-UI: tick-flash (verde sale / rosso scende, come un terminale vero).
   Mappa modulo-condivisa: la chiave identifica la SERIE, non la pagina —
   navigando tra F1 e F2 lo stesso valore non ri-flasha, un valore cambiato si'. */
const _prev = new Map<string, number>();

export function FlashPx({ k, value }: { k: string; value: number | null }) {
  useLingua();
  const [cls, setCls] = useState('');
  useEffect(() => {
    if (value == null) return;
    const prev = _prev.get(k);
    _prev.set(k, value);
    if (prev != null && prev !== value) {
      setCls(value > prev ? 'tick-up' : 'tick-down');
      const t = setTimeout(() => setCls(''), 950);
      return () => clearTimeout(t);
    }
  }, [k, value]);
  return <span className={cls}>{value != null ? value.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '-'}</span>;
}

// flash su un contenuto arbitrario (per il P&L GG live)
export function FlashVal({ k, value, children }: { k: string; value: number | null; children: ReactNode }) {
  const [cls, setCls] = useState('');
  useEffect(() => {
    if (value == null) return;
    const prev = _prev.get(k);
    _prev.set(k, value);
    if (prev != null && prev !== value) {
      setCls(value > prev ? 'tick-up' : 'tick-down');
      const t = setTimeout(() => setCls(''), 950);
      return () => clearTimeout(t);
    }
  }, [k, value]);
  return <span className={cls}>{children}</span>;
}
