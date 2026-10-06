/* ============================================================
   Impostazioni (F19) — stile Nuova, ottobre 2026.
   Resta un overlay sopra la pagina in cui il PM sta lavorando: si apre con
   F19, con CONFIG o dalla palette; si chiude con ESC, con «Chiudi» o
   cliccando fuori. A sinistra l'indice (Generale · Backup · Lavori
   automatici · Motori e cambi) con una riga di stato per sezione, a destra
   una sezione alla volta. Tutte le sezioni restano montate (le inattive sono
   `hidden`), cosi' letture e stati non ripartono passando da una all'altra.

   Regole che restano dalla «veglia» (F11 v4, 27/07):
     · ogni fetch fallito e' DICHIARATO per nome (regola 14/07), mai un
       riquadro vuoto che sembra «nessun dato»;
     · un lavoro spento per scelta non e' un guasto;
     · la contraddizione «dichiara esito ≠ 0 ma ha lasciato un backup»
       compare solo se tiene davvero, dentro la riga del lavoro.
   Novita': su macOS il backend non puo' leggere il Task Scheduler (manca
   `powershell`): e' uno stato neutro, non un errore rosso.
   ============================================================ */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { AlertCircle, CheckCircle, Trash2, X } from 'lucide-react';
import { Bellomberg } from '@/lib/api';
import SceltaLingua from './SceltaLingua';
import ModernPage from './ModernPage';
import NewInterfaceBoundary from './NewInterfaceBoundary';
import { useLingua, useT } from '../i18n/provider';
import { localeDi } from '../i18n/lingua';
import {
  type Backup, type SezioneId, SEZIONI, calendarioDi, contraddizioneDi, ggmm, hhmm, lavori, parseIso, perGiornoDi, powershellAssente,
} from './impostazioni/logica';
import { type Numero, ID_SEZIONE, SezioneBackup, SezioneGenerale, SezioneHead, SezioneLavori, SezioneMotori } from './impostazioni/Viste';
import './nuova/nuova.css';
import './impostazioni/impostazioni.css';

function DeferredSettingsView({ render }: { render: () => ReactNode }) {
  return render();
}

function SettingsViewBoundary({ render }: { render: () => ReactNode }) {
  const language = useLingua();
  return <NewInterfaceBoundary language={language}>
    <DeferredSettingsView render={render} />
  </NewInterfaceBoundary>;
}

type Esito = { ok: boolean; text: (tr: ReturnType<typeof useT>, num: Numero) => string };
const missingLabels = { backups: 'settingsPage.missing_backups', tasks: 'settingsPage.missing_tasks', health: 'settingsPage.missing_health', fx: 'settingsPage.missing_fx', engines: 'settingsPage.missing_engines' } as const;
type MissingData = keyof typeof missingLabels;

export default function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const tr = useT(), language = useLingua();
  const locale = localeDi(language);
  const num: Numero = (value, digits) => value.toLocaleString(locale, digits === undefined
    ? { maximumSignificantDigits: 21, useGrouping: true }
    : { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: true });
  const [tasks, setTasks] = useState<any[] | null>(null);
  const [taskDiagnostic, setTaskDiagnostic] = useState<string | null>(null);
  const [backups, setBackups] = useState<Backup[] | null>(null);
  const [count, setCount] = useState<number | null>(null);
  const [health, setHealth] = useState<any>(null);
  const [fx, setFx] = useState<Record<string, number> | null>(null);
  const [engines, setEngines] = useState<Record<string, string> | null>(null);
  const [loadingReads, setLoadingReads] = useState({ tasks: true, backups: true, health: true, fx: true, engines: true });
  const [buchi, setBuchi] = useState<MissingData[]>([]);
  const [esito, setEsito] = useState<Esito | null>(null);
  const [creando, setCreando] = useState(false);
  const [daCancellare, setDaCancellare] = useState<Backup | null>(null);
  // nuovo stato in coda: l'harness SSR dei test indicizza gli hook per posizione
  const [sezione, setSezione] = useState<SezioneId>('generale');

  const box = useRef<HTMLDivElement>(null);
  const tornaA = useRef<HTMLElement | null>(null);
  const triggerCancellazione = useRef<HTMLElement | null>(null);
  const daCancellareRef = useRef<Backup | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const mostraConfermaCancellazione = (backup: Backup | null) => {
    daCancellareRef.current = backup;
    setDaCancellare(backup);
  };

  const chiudiConfermaCancellazione = () => {
    mostraConfermaCancellazione(null);
    const restoreTriggerFocus = () => {
      if (open) triggerCancellazione.current?.focus();
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(restoreTriggerFocus);
    else queueMicrotask(restoreTriggerFocus);
  };

  const segnala = useCallback((nome: MissingData) => {
    setBuchi(prev => (prev.includes(nome) ? prev : [...prev, nome]));
  }, []);

  const caricaBackups = useCallback(async () => {
    setLoadingReads(current => ({ ...current, backups: true }));
    try {
      const r = await Bellomberg.dbBackupsList();
      setBackups(r.backups || []);
      setCount(typeof r.count === 'number' ? r.count : null);
      setBuchi(prev => prev.filter(b => b !== 'backups'));
    } catch {
      setBackups(null);
      segnala('backups');
    } finally {
      setLoadingReads(current => ({ ...current, backups: false }));
    }
  }, [segnala]);

  useEffect(() => {
    if (!open) return;
    setBuchi([]);
    setTaskDiagnostic(null);
    setLoadingReads({ tasks: true, backups: true, health: true, fx: true, engines: true });
    Bellomberg.scheduledTasks().then(r => {
      if ('error' in r && r.error) throw new Error(String(r.error));
      setTasks(r.tasks || []);
    }).catch((e: unknown) => {
      const diagnostic = e instanceof Error ? e.message : String(e);
      setTasks(null);
      // su macOS manca powershell: sistema non supportato, non un dato mancante
      if (!powershellAssente(diagnostic)) segnala('tasks');
      setTaskDiagnostic(diagnostic);
    }).finally(() => setLoadingReads(current => ({ ...current, tasks: false })));
    Bellomberg.health().then(setHealth).catch(() => { setHealth(null); segnala('health'); })
      .finally(() => setLoadingReads(current => ({ ...current, health: false })));
    Bellomberg.fx().then(r => setFx(r.rates || {})).catch(() => { setFx(null); segnala('fx'); })
      .finally(() => setLoadingReads(current => ({ ...current, fx: false })));
    Bellomberg.agentsList()
      .then(r => setEngines((r.engines as unknown as Record<string, string>) || null))
      .catch(() => { setEngines(null); segnala('engines'); })
      .finally(() => setLoadingReads(current => ({ ...current, engines: false })));
    caricaBackups();
  }, [open, caricaBackups, segnala]);

  /* ESC chiude; il fuoco entra nel pannello all'apertura e torna a chi l'ha
     aperto alla chiusura (un pannello che rapisce il fuoco senza restituirlo
     e' una trappola per chi naviga da tastiera). */
  useEffect(() => {
    if (!open) return;
    tornaA.current = document.activeElement as HTMLElement | null;
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        if (daCancellareRef.current) chiudiConfermaCancellazione();
        else onCloseRef.current();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      try { tornaA.current?.focus(); } catch { /* il nodo puo' non esistere piu' */ }
    };
  }, [open]);

  const { attivi, spenti, guasti, prossimo } = useMemo(() => lavori(tasks), [tasks]);
  const perGiorno = useMemo(() => perGiornoDi(backups), [backups]);
  const calendario = useMemo(() => calendarioDi(perGiorno), [perGiorno]);
  const contraddizione = useMemo(() => contraddizioneDi(guasti, perGiorno), [guasti, perGiorno]);

  const totMb = (backups || []).reduce((s, b) => s + (b.size_mb || 0), 0);
  const nascosti = count != null && backups ? Math.max(0, count - backups.length) : 0;
  const scoperte = calendario.filter(c => c.n === 0).length;
  const nonSupportati = tasks === null && powershellAssente(taskDiagnostic);

  const creaBackup = async () => {
    setCreando(true); setEsito(null);
    try {
      const r = await Bellomberg.dbBackupCreate();
      const ko = Object.entries(r.db_quick_check || {}).filter(([, v]) => v !== 'ok');
      setEsito({
        ok: r.ok,
        text: (tr, num) => tr('settingsPage.backupCreated', {a: num(r.size_mb), b: num(r.files_count), c: r.backup_path}) +
          (r.ok ? '' : tr('settingsPage.backupExcluded', {a: ko.length, b: ko.map(([k, v]) => `${k}: ${v}`).join(' · ')})),
      });
      await caricaBackups();
    } catch (e: any) {
      setEsito({ ok: false, text: tr => tr('settingsPage.backupNotCreated') + (e?.response?.data?.detail || e?.message || String(e)) });
    } finally {
      setCreando(false);
    }
  };

  const cancella = async (b: Backup) => {
    mostraConfermaCancellazione(null); setEsito(null);
    try {
      await Bellomberg.dbBackupDelete(b.filename);
      setEsito({ ok: true, text: (tr, num) => tr('settingsPage.deleted', {a: b.filename, b: num(b.size_mb)}) });
      await caricaBackups();
    } catch (e: any) {
      setEsito({ ok: false, text: tr => tr('settingsPage.notDeleted') + (e?.response?.data?.detail || e?.message || String(e)) });
    }
  };

  if (!open) return null;

  const nd = tr('settings.nd');
  const ordinati = (backups || []).slice().sort((a, b) => String(b.created).localeCompare(String(a.created)));
  const ultimo = ordinati[0] ? parseIso(ordinati[0].created) : null;
  const passo = (d: number) => setSezione(s => SEZIONI[(SEZIONI.indexOf(s) + d + SEZIONI.length) % SEZIONI.length]);
  const nomeValuta = (c: string) => {
    try { const n = new Intl.DisplayNames([locale], { type: 'currency' }).of(c) || c; return n.charAt(0).toUpperCase() + n.slice(1); } catch { return c; }
  };
  const caricamento = (testo: string) => <span role="status" aria-busy="true">{testo}</span>;

  /* Riga di stato per sezione nell'indice: dice cosa c'e', o che la lettura e'
     in corso, o che e' fallita — mai un vuoto muto. */
  const indice: Array<{ id: SezioneId; titolo: string; riga: ReactNode; tono: 'ok' | 'warn' | 'ko' | 'muted' }> = [
    { id: 'generale', titolo: tr('settingsPage.secGeneral'),
      riga: loadingReads.health && !health ? caricamento(tr('ui.loading'))
        : tr('settingsPage.idxGeneral', { lang: language === 'it' ? 'Italiano' : 'English', v: health?.version || nd }),
      tono: health && String(health.status).toLowerCase() === 'ok' ? 'ok' : health ? 'warn' : loadingReads.health ? 'muted' : 'ko' },
    { id: 'backup', titolo: tr('settingsPage.secBackup'),
      riga: backups === null && loadingReads.backups ? caricamento(tr('ui.loading'))
        : backups === null ? tr('settingsPage.idxBackupUnreadable')
        : !backups.length ? tr('settingsPage.idxNoBackups')
        : tr(count === 1 ? 'settingsPage.idxBackup_one' : 'settingsPage.idxBackup_other', { n: count ?? '?', mb: num(totMb, 0), c: calendario.length - scoperte, d: calendario.length }),
      tono: backups === null ? (loadingReads.backups ? 'muted' : 'ko') : !backups.length || scoperte > calendario.length - scoperte ? 'warn' : 'ok' },
    { id: 'lavori', titolo: tr('settingsPage.secJobs'),
      riga: tasks === null && loadingReads.tasks ? caricamento(tr('ui.loading'))
        : nonSupportati ? tr('settingsPage.idxJobsMac')
        : tasks === null ? tr('settingsPage.idxJobsUnreadable')
        : !tasks.length ? tr('settingsPage.idxNoJobs')
        : tr('settingsPage.idxJobs', { a: attivi.length, b: guasti.length, c: spenti.length }),
      tono: nonSupportati || (tasks === null && loadingReads.tasks) ? 'muted' : tasks === null || guasti.length ? 'ko' : !tasks.length ? 'warn' : 'ok' },
    { id: 'motori', titolo: tr('settingsPage.secEngines'),
      riga: (engines === null && loadingReads.engines) || (fx === null && loadingReads.fx) ? caricamento(tr('ui.loading'))
        : fx === null ? tr('settingsPage.idxFxUnreadable')
        : engines === null ? tr('settingsPage.idxEnginesUnreadable')
        : tr('settingsPage.idxEngines', { a: Object.keys(engines).filter(k => !/_error$/.test(k)).length, b: Object.keys(fx).length }),
      tono: (engines === null && loadingReads.engines) || (fx === null && loadingReads.fx) ? 'muted' : fx === null || engines === null ? 'ko' : 'ok' },
  ];
  const percorso = ordinati[0]?.path ? ordinati[0].path.replace(/[/\\][^/\\]+$/, '') : null;
  const titoloDi = (id: SezioneId) => indice.find(x => x.id === id)!.titolo;
  const head = (id: SezioneId, extra?: ReactNode) => <SezioneHead n={ID_SEZIONE[id]} titolo={titoloDi(id)} extra={extra} onStep={passo} tr={tr} />;

  return <ModernPage page="settings" className="bb-page-settings" render={() => <SettingsViewBoundary render={() => (
    <>
      <div className="f11-scrim bbn-imp-scrim" onClick={onClose} />
      <div className="f11v bbn-imp" role="dialog" aria-modal="true" aria-labelledby="bbn-imp-title" tabIndex={-1} ref={box}>
        <div className="phead bbn-imp-head">
          <h1 id="bbn-imp-title">{tr('settingsPage.title')}</h1>
          <span className={'bbn-imp-chip ' + (health ? (String(health.status).toLowerCase() === 'ok' ? 'is-ok' : 'is-warn') : loadingReads.health ? '' : 'is-ko')}>
            <i className="dot" />{tr('settingsPage.chipBackend')} <b>{health ? String(health.status || nd).toUpperCase() : loadingReads.health ? '…' : tr('settings.nd_upper')}</b></span>
          <span className="bbn-imp-chip">{tr('settingsPage.chipLastBackup')} <b>{ultimo ? `${ggmm(ultimo, nd)} ${hhmm(ultimo)}` : nd}</b></span>
          {calendario.length > 0 && (
            <span className={'bbn-imp-chip ' + (scoperte > calendario.length - scoperte ? 'is-warn' : 'is-ok')}>
              <i className="dot" />{tr('settingsPage.chipNights')} <b>{tr('settingsPage.ofN', { a: calendario.length - scoperte, b: calendario.length })}</b></span>
          )}
          {nonSupportati
            ? <span className="bbn-imp-chip"><i className="dot" />{tr('settingsPage.chipJobs')} <b>{tr('settingsPage.chipJobsMac')}</b></span>
            : guasti.length > 0 && <span className="bbn-imp-chip is-ko"><i className="dot" />{tr('settingsPage.chipJobsFailed')} <b>{guasti.length}</b></span>}
          {buchi.length > 0 && <span className="bbn-imp-chip is-ko"><i className="dot" />{tr('settingsPage.chipReadsFailed')} <b>{buchi.length}</b></span>}
          <span className="bbn-imp-grow" />
          <button type="button" className="x bbn-imp-close" onClick={onClose}>{tr('settingsPage.close')} <kbd>{'Esc'}</kbd></button>
        </div>

        <div className="bbn-imp-body">
          <nav className="bbn-imp-index" aria-label={tr('settingsPage.sections')}>
            {indice.map(s => (
              <button type="button" key={s.id} className="bbn-imp-ix" data-sezione={s.id} aria-current={sezione === s.id ? 'true' : undefined}
                onClick={() => setSezione(s.id)}>
                <span className="n">{ID_SEZIONE[s.id]}</span>
                <span className="t">{s.titolo}</span>
                <span className={'d is-' + s.tono} aria-hidden="true" />
                <span className="s">{s.riga}</span>
              </button>
            ))}
            <span className="bbn-imp-grow" />
            <div className="bbn-imp-folder">
              <span className="k">{tr('settingsPage.folderLabel')}</span>
              <span className="v" title={percorso || undefined}>{percorso || tr('settingsPage.pathNotDeclared')}</span>
              <button type="button" className="bbn-imp-link" onClick={() => setSezione('backup')}>{tr('settingsPage.seeFiles')}</button>
            </div>
          </nav>

          <div className="pbody bbn-imp-main">
            {buchi.length > 0 && (
              <div className="buco bbn-imp-note is-ko"><AlertCircle size={15} />
                <span><b>{tr('settingsPage.dataMissing', { list: buchi.map(b => tr(missingLabels[b])).join(', ') })}</b> {tr('settingsPage.dataMissingDetail')}</span>
              </div>
            )}
            <section className="bbn-imp-sec" data-pannello="generale" hidden={sezione !== 'generale'}>
              {head('generale')}
              <div className="bbn-imp-sec-body">
                <SezioneGenerale tr={tr} health={health} loadingHealth={loadingReads.health} tasks={tasks} nonSupportati={nonSupportati}
                  attivi={attivi.length} guasti={guasti.length} spenti={spenti}
                  lingua={<SceltaLingua variant="nuova" />}
                  scorciatoie={[['F19', tr('settingsPage.keyF19')], ['Ctrl K', tr('settingsPage.keyCtrlK')], ['Esc', tr('settingsPage.keyEsc')]]} />
              </div>
            </section>
            <section className="bbn-imp-sec" data-pannello="backup" hidden={sezione !== 'backup'}>
              {head('backup')}
              <div className="bbn-imp-sec-body">
                <SezioneBackup tr={tr} num={num} backups={backups} loading={loadingReads.backups} count={count} ordinati={ordinati}
                  cal={calendario} scoperte={scoperte} totMb={totMb} nascosti={nascosti} guasti={guasti} creando={creando} locale={locale}
                  onCrea={creaBackup}
                  onElimina={(b, el) => { triggerCancellazione.current = el; mostraConfermaCancellazione(b); }} />
              </div>
            </section>
            <section className="bbn-imp-sec" data-pannello="lavori" hidden={sezione !== 'lavori'}>
              {head('lavori', !nonSupportati && <span className="bbn-imp-sub">{tr('settingsPage.jobsRule')}</span>)}
              <div className="bbn-imp-sec-body bbn-imp-scroll">
                <SezioneLavori tr={tr} num={num} tasks={tasks} loading={loadingReads.tasks} diagnostica={taskDiagnostic} nonSupportati={nonSupportati}
                  attivi={attivi.length} guasti={guasti.length} prossimo={prossimo} perGiorno={perGiorno}
                  contraddizione={contraddizione} spenti={spenti} onBackup={() => setSezione('backup')} />
              </div>
            </section>
            <section className="bbn-imp-sec" data-pannello="motori" hidden={sezione !== 'motori'}>
              {head('motori')}
              <div className="bbn-imp-sec-body">
                <SezioneMotori tr={tr} num={num} engines={engines} loadingEngines={loadingReads.engines}
                  fx={fx} loadingFx={loadingReads.fx} nomeValuta={nomeValuta} />
              </div>
            </section>
          </div>
        </div>

        <div className="pfoot bbn-imp-foot">
          <span><kbd>{'F19'}</kbd>{tr('settingsPage.footF19')}</span>
          <span><kbd>{'Esc'}</kbd>{tr('settingsPage.footEsc')}</span>
          <span className="bbn-imp-grow" />
          <span>{tr('settingsPage.footTotals', {
            files: tr(count === 1 ? 'settingsPage.filesN_one' : 'settingsPage.filesN_other', { n: count ?? '?' }), mb: num(totMb, 0),
            days: tr(calendario.length === 1 ? 'settingsPage.footDays_one' : 'settingsPage.footDays_other', { n: calendario.length }) })}</span>
        </div>

        {esito && (
          <div className={'esito bbn-imp-toast bbn-imp-note ' + (esito.ok ? 'is-ok' : 'is-ko')} role="status">
            {esito.ok ? <CheckCircle size={15} /> : <AlertCircle size={15} />}
            <span>{esito.text(tr, num)}</span>
            <button type="button" className="bbn-imp-icon" aria-label={tr('settingsPage.dismiss')} onClick={() => setEsito(null)}><X size={14} /></button>
          </div>
        )}
      </div>

      {/* La conferma prima di cancellare: un backup cancellato NON passa dal
          cestino e non e' recuperabile. */}
      {daCancellare && (
        <div className="f11-ask bbn-imp-ask" role="dialog" aria-modal="true" aria-label={tr('settingsPage.askLabel')}>
          <div className="box">
            <div className="top"><span className="ic"><Trash2 size={18} /></span><h3>{tr('settingsPage.askTitle')}</h3></div>
            <div className="file">
              <b>{daCancellare.filename}</b>
              <span>{num(daCancellare.size_mb, 2)} MB · {ggmm(parseIso(daCancellare.created), nd)} {hhmm(parseIso(daCancellare.created))}</span>
            </div>
            <p>{tr('settingsPage.askBody', { n: Math.max(0, (count ?? 1) - 1) })}</p>
            <div className="bf">
              <button type="button" className="bbn-imp-btn" onClick={chiudiConfermaCancellazione} autoFocus>{tr('settingsPage.cancel')}</button>
              <button type="button" className="bbn-imp-btn is-danger" onClick={() => cancella(daCancellare)}><Trash2 size={14} />{tr('settingsPage.deleteForever')}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )} />} />;
}
