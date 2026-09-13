import { useCallback, useSyncExternalStore } from 'react';
import { linguaCorrente, sottoscriviLingua } from './lingua.js';
import { traduci, type Chiave, type Parametri } from './t.js';

export function useLingua() {
  return useSyncExternalStore(sottoscriviLingua, linguaCorrente, linguaCorrente);
}

export function useT() {
  const lingua = useLingua();
  return useCallback((chiave: Chiave, parametri?: Parametri) => traduci(lingua, chiave, parametri), [lingua]);
}
