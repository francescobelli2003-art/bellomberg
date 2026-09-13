import { useCallback, useEffect, useRef, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import { caricaPreferenza, scegliLingua, ErrorePreferenza, type PreferenzaVerificata } from '@/i18n/preferenze';
import { type Lingua } from '@/i18n/lingua';
import { useLingua, useT } from '@/i18n/provider';

type Props = { initial?: boolean; onReady?: (preference: PreferenzaVerificata) => void };

/** An explicit first choice; a failed save always offers readback before retry. */
export default function SceltaLingua({ initial = false, onReady }: Props) {
  const current = useLingua(), t = useT();
  const [choice, setChoice] = useState<Lingua | null>(initial ? null : current);
  const [state, setState] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading');
  const [problem, setProblem] = useState<ErrorePreferenza | null>(null);
  const [notice, setNotice] = useState<'saved' | 'cache' | 'backup' | null>(null);
  const alive = useRef(true);
  const ready = useRef(onReady);
  ready.current = onReady;
  const load = useCallback(async () => {
    setState('loading'); setProblem(null); setNotice(null);
    try {
      const result = await caricaPreferenza(Bellomberg);
      if (!alive.current) return;
      setChoice(result.selected ? result.language : null);
      setState('ready');
      if (result.selected) {
        if (!result.cacheSaved) setNotice('cache');
        ready.current?.(result);
      }
    } catch (e) { if (alive.current) { setProblem(e as ErrorePreferenza); setState('error'); } }
  }, []);
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; }; }, [load]);
  const save = async () => {
    if (!choice || state === 'saving') return;
    const fingerprint = problem?.fingerprint;
    setState('saving'); setNotice(null);
    try {
      const result = await scegliLingua(choice, Bellomberg, fingerprint);
      if (!alive.current) return;
      setProblem(null); setState('ready');
      setNotice(!result.cacheSaved ? 'cache' : result.backupCreated ? 'backup' : 'saved');
      ready.current?.(result);
    } catch (e) {
      if (alive.current) { setProblem(e as ErrorePreferenza); setState('error'); }
    }
  };
  const busy = state === 'loading' || state === 'saving';
  const repair = !!problem?.fingerprint;
  return <section className="panel p-5 max-w-2xl font-mono text-xs" aria-labelledby="language-title">
    <h1 id="language-title" className="text-amber text-base mb-3">{initial ? 'Scegli la lingua / Choose your language' : t('lingua.titolo')}</h1>
    <p className="text-text-dim mb-4">{initial
      ? 'Interfaccia e nuovi contenuti seguiranno la scelta. I documenti esistenti restano nella lingua originale. / The interface and new content will follow your choice. Existing documents keep their original language.'
      : t('lingua.descrizione')}</p>
    <fieldset disabled={busy} className="flex gap-5 mb-4">
      <legend className="sr-only">{initial ? 'Language / Lingua' : t('lingua.titolo')}</legend>
      {(['it', 'en'] as const).map(value => <label key={value} className="flex items-center gap-2 cursor-pointer">
        <input type="radio" name="language" value={value} checked={choice === value} onChange={() => setChoice(value)} />
        <span lang={value}>{value === 'it' ? 'Italiano' : 'English'}</span>
      </label>)}
    </fieldset>
    {state === 'loading' && <p role="status">{initial ? 'Caricamento preferenza / Loading preference…' : t('lingua.caricamento')}</p>}
    {problem && <div role="alert" className="text-crimson mb-3">
      <p>{initial ? 'Preferenza non verificata / Preference not verified' : t('lingua.non_verificata')}</p>
      <p>{problem.message}</p>
      <p>{repair
        ? (initial ? 'La scelta ripristina il file illeggibile e ne conserva una copia verificata. / Saving repairs the unreadable file and preserves a verified backup.' : t('lingua.ripara_nota'))
        : (initial ? 'Rileggi il profilo prima di riprovare: il salvataggio potrebbe essere riuscito. / Reload the profile before trying again: the save may have succeeded.' : t('lingua.rileggi_nota'))}</p>
    </div>}
    <div className="flex gap-3">
      <button className="btn btn-amber" disabled={!choice || busy || (state === 'error' && !repair)} onClick={() => void save()}>
        {state === 'saving' ? (initial ? 'SALVATAGGIO / SAVING…' : t('lingua.salvataggio'))
          : repair ? (initial ? 'RIPRISTINA E SALVA / REPAIR AND SAVE' : t('lingua.ripara'))
          : (initial ? 'SALVA LINGUA / SAVE LANGUAGE' : t('lingua.salva'))}
      </button>
      {state === 'error' && <button className="btn" disabled={busy} onClick={() => void load()}>{initial ? 'RILEGGI / RELOAD' : t('lingua.rileggi')}</button>}
    </div>
    {notice && <p role="status" className="text-text-dim mt-3">{t(notice === 'cache' ? 'lingua.cache_non_salvata' : notice === 'backup' ? 'lingua.backup_salvato' : 'lingua.salvata')}</p>}
  </section>;
}
