import { useState } from 'react';
import SceltaLingua from './SceltaLingua';
import { useT } from '@/i18n/provider';
import type { PreferenzaVerificata } from '@/i18n/preferenze';

export default function LinguaGate({ children }: { children: React.ReactNode }) {
  const [verified, setVerified] = useState<PreferenzaVerificata | null>(null);
  const t = useT();
  if (!verified) return <main className="min-h-screen flex items-center justify-center p-6"><SceltaLingua initial onReady={setVerified} /></main>;
  return <>{!verified.cacheSaved && <div role="status" className="text-amber text-xs px-3 py-2">{t('lingua.cache_non_salvata')}</div>}{children}</>;
}
