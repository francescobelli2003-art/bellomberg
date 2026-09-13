// Lingua dell'interfaccia: tipo, persistenza e "lingua corrente".
// NESSUN import: questo modulo e' importabile dalle lib pure (cassa, mandato,
// format) e dai test in sandbox. Gli import interni a src/i18n usano
// l'estensione `.js` (stile NodeNext) perche' tests/mandato/tsconfig.json
// compila lib/mandato.ts da solo, senza gli alias `@/` di Vite.

export type Lingua = 'it' | 'en';
export const LINGUE: readonly Lingua[] = ['it', 'en'];

/** Chiave in localStorage (decisione orchestratore 12/09). */
export const CHIAVE_STORAGE_LINGUA = 'bellomberg.lingua';

/** Lingua in cui l'app e' nata: vale SOLO finche' l'utente non ha scelto
 *  (schermata PIN al primo avvio). Non e' un ripiego su dato mancante: il
 *  cancello LinguaGate chiede la scelta prima di far entrare nell'app. */
export const LINGUA_DEFAULT: Lingua = 'it';

export function eLingua(x: unknown): x is Lingua {
  return x === 'it' || x === 'en';
}

/** Locale BCP-47 per Intl. en-GB e non en-US: cosi' l'ordine giorno/mese
 *  non si inverte fra le due lingue (misura del rapporto 12/09, §3.2). */
export function localeDi(lingua: Lingua): 'it-IT' | 'en-GB' {
  return lingua === 'en' ? 'en-GB' : 'it-IT';
}

/** La preferenza salvata, o null se non c'e'. Un valore estraneo (es. un
 *  vecchio formato) viene DICHIARATO su console e trattato come "non scelto":
 *  mai una lingua indovinata al posto di quella voluta. */
export function leggiLinguaSalvata(): Lingua | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    const v = localStorage.getItem(CHIAVE_STORAGE_LINGUA);
    if (v == null) return null;
    if (eLingua(v)) return v;
    console.error(`[i18n] valore non riconosciuto in ${CHIAVE_STORAGE_LINGUA}: ${JSON.stringify(v)} — la scelta verrà richiesta di nuovo`);
    return null;
  } catch {
    return null;
  }
}

/** Salva la preferenza. Ritorna false (dichiarato al chiamante) se lo storage
 *  rifiuta la scrittura: chi chiama lo mostra, non lo nasconde. */
export function salvaLingua(lingua: Lingua): boolean {
  try {
    if (typeof localStorage === 'undefined') return false;
    localStorage.setItem(CHIAVE_STORAGE_LINGUA, lingua);
    return localStorage.getItem(CHIAVE_STORAGE_LINGUA) === lingua;
  } catch {
    return false;
  }
}

// ── lingua corrente (stato di modulo) ────────────────────────────────────
// Le funzioni pure di formato e lettura numeri (fmtEUR, leggiNumero, …) hanno
// firme storiche senza lingua, usate da decine di file: prendono da qui la
// lingua quando il chiamante non la passa. Il LinguaProvider la tiene
// allineata alla scelta dell'utente; i test la impostano esplicitamente.
let corrente: Lingua = leggiLinguaSalvata() ?? LINGUA_DEFAULT;
const ascoltatori = new Set<() => void>();

/** React subscribes without remounting pages or discarding unsaved forms. */
export function sottoscriviLingua(listener: () => void): () => void {
  ascoltatori.add(listener);
  return () => { ascoltatori.delete(listener); };
}

export function linguaCorrente(): Lingua {
  return corrente;
}

/** Imposta la lingua corrente e allinea `<html lang>` (index.html nasce `it`). */
export function impostaLinguaCorrente(lingua: Lingua): void {
  if (!eLingua(lingua)) throw new Error('Unsupported language / Lingua non supportata');
  const cambiata = corrente !== lingua;
  corrente = lingua;
  applicaLinguaAlDocumento(lingua);
  if (cambiata) for (const listener of ascoltatori) listener();
}

export function applicaLinguaAlDocumento(lingua: Lingua = corrente): void {
  try {
    if (typeof document !== 'undefined' && document.documentElement) document.documentElement.lang = lingua;
  } catch { /* nessun documento (test in node): niente da allineare */ }
}
