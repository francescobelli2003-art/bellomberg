/* ============================================================
   F11 v4 "LA VEGLIA" — il pannello delle impostazioni.
   Impianto scelto dal PM 27/07 sui PNG di mockup_f11_settings
   (opzione A, contro B "la catena di custodia" e C "la sala
   macchine"), col REGISTRO di B innestato come secondo riquadro.

   F11 non e' piu' una pagina: scende dalla rotellina in alto e sta
   SOPRA la pagina in cui il PM stava lavorando. Si apre con F11, con
   la rotellina o dalla palette; si chiude con ESC, con la rotellina
   o cliccando fuori.

   La domanda a cui risponde: la macchina ha fatto la guardia stanotte?
   Lo strumento-firma e' in due pezzi:
     · LA CONTRADDIZIONE — confronta cio' che il lavoro DICHIARA con
       cio' che ha effettivamente LASCIATO in cartella. Compare solo
       se la condizione tiene davvero.
     · IL PETTINE DELLE NOTTI — un dente per giorno, cavo dove nessuno
       ha vegliato. Hover/tastiera danno la lettura numerica esatta.

   Regola 14/07 ovunque: ogni fetch fallito e' DICHIARATO, mai un
   pannello vuoto che sembra "nessun dato" quando invece e' un errore.
   ============================================================ */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, CheckCircle, HardDrive, Trash2, X } from 'lucide-react';
import { Bellomberg } from '@/lib/api';
import SceltaLingua from './SceltaLingua';
import { useLingua, useT } from '../i18n/provider';
import { localeDi } from '../i18n/lingua';
import { t as tr, type Chiave } from '../i18n/t';
import '../pages/settings-veglia.css';

type Backup = { filename: string; path: string; size_mb: number; created: string };
type Numero = (value: number, digits?: number) => string;
type Esito = { ok: boolean; text: (tr: ReturnType<typeof useT>, num: Numero) => string };

/* Il payload del Task Scheduler arriva col formato US (07/26/2026 23:00:01).
   Un `new Date(stringa)` qui sarebbe un azzardo: si parsa a mano. */
function parseTask(s: unknown): Date | null {
  const m = String(s ?? '').match(/(\d{2})\/(\d{2})\/(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/);
  if (!m) return null;
  const d = new Date(+m[3], +m[1] - 1, +m[2], +m[4], +m[5], +m[6]);
  return isNaN(d.getTime()) ? null : d;
}
function parseIso(s: unknown): Date | null {
  const d = new Date(String(s ?? ''));
  return isNaN(d.getTime()) ? null : d;
}
const due = (n: number) => String(n).padStart(2, '0');
const ggmm = (d: Date | null) => (d ? `${due(d.getDate())}/${due(d.getMonth() + 1)}` : tr('settings.nd'));
const hhmm = (d: Date | null) => (d ? `${due(d.getHours())}:${due(d.getMinutes())}` : '');
const chiave = (d: Date) => `${d.getFullYear()}-${due(d.getMonth() + 1)}-${due(d.getDate())}`;
const isAuto = (f: string) => /^bellomberg_backup_/i.test(f);
const engineLabels: Record<string, Chiave> = {
  chat: 'settings.engine_chat', committee_r0: 'settings.engine_r0', committee_r1_r2: 'settings.engine_r1_r2',
  committee_macro: 'settings.engine_macro', committee_quant: 'settings.engine_quant', committee_options: 'settings.engine_options',
  committee_fundamentals: 'settings.engine_fundamentals', committee_crypto: 'settings.engine_crypto', committee_eventdesk: 'settings.engine_eventdesk',
  capo: 'settings.engine_capo', red_team: 'settings.engine_redteam', reflection: 'settings.engine_reflection',
  action_extractor: 'settings.engine_extractor', synthesizer: 'settings.engine_synthesizer', briefing: 'settings.engine_briefing', news_classifier: 'settings.engine_news',
};

export default function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const tr = useT(), language = useLingua();
  const num: Numero = (value, digits) => value.toLocaleString(localeDi(language), digits === undefined
    ? { maximumSignificantDigits: 21, useGrouping: true }
    : { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: true });
  const [tasks, setTasks] = useState<any[] | null>(null);
  const [taskDiagnostic, setTaskDiagnostic] = useState<string | null>(null);
  const [backups, setBackups] = useState<Backup[] | null>(null);
  const [count, setCount] = useState<number | null>(null);
  const [health, setHealth] = useState<any>(null);
  const [fx, setFx] = useState<Record<string, number> | null>(null);
  const [engines, setEngines] = useState<Record<string, string> | null>(null);
  // regola 14/07: i fetch falliti si DICHIARANO per nome, mai pannelli vuoti muti
  const missingLabels = { backups: 'settings.missing_backups', tasks: 'settings.missing_tasks', health: 'settings.missing_health', fx: 'settings.missing_fx', engines: 'settings.missing_engines' } as const;
  type MissingData = keyof typeof missingLabels;
  const [buchi, setBuchi] = useState<MissingData[]>([]);
  const [esito, setEsito] = useState<Esito | null>(null);
  const [creando, setCreando] = useState(false);
  const [daCancellare, setDaCancellare] = useState<Backup | null>(null);

  const box = useRef<HTMLDivElement>(null);
  const tornaA = useRef<HTMLElement | null>(null);

  const segnala = useCallback((nome: MissingData) => {
    setBuchi(prev => (prev.includes(nome) ? prev : [...prev, nome]));
  }, []);

  const caricaBackups = useCallback(async () => {
    try {
      const r = await Bellomberg.dbBackupsList();
      setBackups(r.backups || []);
      setCount(typeof r.count === 'number' ? r.count : null);
      setBuchi(prev => prev.filter(b => b !== 'backups'));
    } catch {
      setBackups(null);
      segnala('backups');
    }
  }, [segnala]);

  useEffect(() => {
    if (!open) return;
    setBuchi([]);
    setTaskDiagnostic(null);
    Bellomberg.scheduledTasks().then(r => {
      if ('error' in r && r.error) throw new Error(String(r.error));
      setTasks(r.tasks || []);
    }).catch((e: unknown) => {
      setTasks(null); segnala('tasks');
      setTaskDiagnostic(e instanceof Error ? e.message : String(e));
    });
    Bellomberg.health().then(setHealth).catch(() => { setHealth(null); segnala('health'); });
    Bellomberg.fx().then(r => setFx(r.rates || {})).catch(() => { setFx(null); segnala('fx'); });
    Bellomberg.agentsList()
      .then(r => setEngines((r.engines as unknown as Record<string, string>) || null))
      .catch(() => { setEngines(null); segnala('engines'); });
    caricaBackups();
  }, [open, caricaBackups, segnala]);

  /* ESC chiude; il fuoco entra nel pannello all'apertura e torna
     alla rotellina alla chiusura (un pannello che rapisce il fuoco
     senza restituirlo e' una trappola per chi naviga da tastiera). */
  useEffect(() => {
    if (!open) return;
    tornaA.current = document.activeElement as HTMLElement | null;
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        if (daCancellare) setDaCancellare(null);
        else onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      try { tornaA.current?.focus(); } catch { /* il nodo puo' non esistere piu' */ }
    };
  }, [open, onClose, daCancellare]);

  /* ── i lavori: spento di proposito NON e' un guasto ────────
     Il Consigliere e' disattivato dal 03/07 per scelta del PM e porta
     ancora l'esito 15 del 13/07. Contarlo fra i guasti farebbe suonare
     l'allarme per sempre, e un allarme che suona sempre e' spento. */
  const { attivi, spenti, guasti } = useMemo(() => {
    const t = tasks || [];
    const sp = t.filter(x => String(x.State).toLowerCase() === 'disabled');
    const at = t.filter(x => String(x.State).toLowerCase() !== 'disabled');
    return { attivi: at, spenti: sp, guasti: at.filter(x => x.LastTaskResult !== 0 && x.LastTaskResult != null) };
  }, [tasks]);

  /* ── il calendario delle notti ─────────────────────────────
     Si costruisce sui backup che il backend CONSEGNA. Se ne tronca
     qualcuno la copertura vera puo' essere migliore di quella resa,
     e sotto il pettine lo si dichiara. */
  const { calendario, perGiorno } = useMemo(() => {
    const per: Record<string, Backup[]> = {};
    (backups || []).forEach(b => {
      const d = parseIso(b.created);
      if (!d) return;
      (per[chiave(d)] = per[chiave(d)] || []).push(b);
    });
    const gg = Object.keys(per).sort();
    if (!gg.length) return { calendario: [] as any[], perGiorno: per };
    const cal: Array<{ g: string; n: number; mb: number; mano: number }> = [];
    const d0 = new Date(gg[0] + 'T00:00:00');
    const d1 = new Date(gg[gg.length - 1] + 'T00:00:00');
    for (let d = new Date(d0); d <= d1; d.setDate(d.getDate() + 1)) {
      const k = chiave(d);
      const items = per[k] || [];
      cal.push({
        g: k, n: items.length,
        mb: items.reduce((s, x) => s + (x.size_mb || 0), 0),
        mano: items.filter(x => !isAuto(x.filename)).length,
      });
    }
    return { calendario: cal, perGiorno: per };
  }, [backups]);

  /* ── LA CONTRADDIZIONE ─────────────────────────────────────
     Il primo lavoro guasto che ha comunque lasciato un backup nella
     notte in cui dichiara di aver fallito. Se non ce n'e' nessuno il
     riquadro non si disegna: non si inventa un allarme. */
  const contraddizione = useMemo(() => {
    for (const t of guasti) {
      const last = parseTask(t.LastRunTime);
      if (!last) continue;
      const lasciati = perGiorno[chiave(last)] || [];
      if (lasciati.length) {
        const mb = lasciati.reduce((s, x) => s + (x.size_mb || 0), 0);
        return { t, last, lasciati, mb };
      }
    }
    return null;
  }, [guasti, perGiorno]);

  const totMb = (backups || []).reduce((s, b) => s + (b.size_mb || 0), 0);
  const nascosti = count != null && backups ? Math.max(0, count - backups.length) : 0;
  const scoperte = calendario.filter(c => c.n === 0).length;
  const maxMb = Math.max(1, ...calendario.map(c => c.mb));

  const creaBackup = async () => {
    setCreando(true); setEsito(null);
    try {
      const r = await Bellomberg.dbBackupCreate();
      const ko = Object.entries(r.db_quick_check || {}).filter(([, v]) => v !== 'ok');
      setEsito({
        ok: r.ok,
        text: (tr, num) => tr('settings.backup_created', {a: num(r.size_mb), b: num(r.files_count), c: r.backup_path}) +
          (r.ok ? '' : tr('settings.backup_excluded', {a: ko.length, b: ko.map(([k, v]) => `${k}: ${v}`).join(' · ')})),
      });
      await caricaBackups();
    } catch (e: any) {
      setEsito({ ok: false, text: tr => tr('settings.backup_not_created') + (e?.response?.data?.detail || e?.message || String(e)) });
    } finally {
      setCreando(false);
    }
  };

  const cancella = async (b: Backup) => {
    setDaCancellare(null); setEsito(null);
    try {
      await Bellomberg.dbBackupDelete(b.filename);
      setEsito({ ok: true, text: (tr, num) => tr('settings.deleted', {a: b.filename, b: num(b.size_mb)}) });
      await caricaBackups();
    } catch (e: any) {
      setEsito({ ok: false, text: tr => tr('settings.not_deleted') + (e?.response?.data?.detail || e?.message || String(e)) });
    }
  };

  if (!open) return null;

  const ordinati = (backups || []).slice().sort((a, b) => String(b.created).localeCompare(String(a.created)));
  const ultimo = ordinati[0] ? parseIso(ordinati[0].created) : null;

  return (
    <>
      <div className="f11-scrim" onClick={onClose} />
      <div className="f11v" role="dialog" aria-modal="true" aria-label={tr('settings.title')} tabIndex={-1} ref={box}>
        <span className="sq tl" /><span className="sq tr" /><span className="sq bl" /><span className="sq br" />

        <div className="phead">
          <span className="tt">{tr('settings.title')}</span>
          <span className="sub">{tr('settings.subtitle')}</span>
          <button className="x" onClick={onClose}><X size={11} /> {tr('settings.esc_close')}</button>
        </div>

        <div className="pbody">
          <SceltaLingua />
          {buchi.length > 0 && (
            <div className="buco">
              {tr('settings.data_missing')} {buchi.map(b => tr(missingLabels[b])).join(' · ')} {tr('settings.data_missing_detail')}
            </div>
          )}

          {esito && (
            <div className={'esito ' + (esito.ok ? 'ok' : 'ko')}>
              {esito.ok ? <CheckCircle size={11} style={{ verticalAlign: -1, marginRight: 6 }} />
                        : <AlertCircle size={11} style={{ verticalAlign: -1, marginRight: 6 }} />}
              {esito.text(tr, num)}
            </div>
          )}

          {/* ── I GUARDIANI ─────────────────────────────────── */}
          <div className="p">
            <div className="ph a">
              {tr('settings.guardians')} {tasks ? tasks.length : '?'} {tr('settings.automatic_jobs')}
              <span className="side">{tr('settings.result_nonzero')}</span>
            </div>
            <div className="pb nopad">
              {tasks === null ? (
                <div style={{ padding: '9px 10px' }} className="nota">
                  <b className="ko">{tr('settings.jobs_unreadable')}</b>{tr('settings.backend_error')}
                  {taskDiagnostic && <div>{taskDiagnostic}</div>}
                </div>
              ) : tasks.length === 0 ? (
                <div style={{ padding: '9px 10px' }} className="nota">
                  {tr('settings.no_jobs')} <b>powershell -ExecutionPolicy Bypass -File .\tools\ops\windows\install_all_schedulers.ps1</b> {tr('settings.as_admin')} {tr('settings.installer_warning')}
                </div>
              ) : tasks.map((t, i) => {
                const r = t.LastTaskResult;
                const spento = String(t.State).toLowerCase() === 'disabled';
                const ok = r === 0;
                const last = parseTask(t.LastRunTime);
                const lasciati = last ? (perGiorno[chiave(last)] || []) : [];
                let frase: React.ReactNode;
                if (spento) {
                  frase = <>{tr('settings.disabled_explanation')} <b>{String(r)}</b> {tr('settings.previous_result')}</>;
                } else if (ok) {
                  frase = <>{tr('settings.result')} <b>0</b>{tr('settings.reported_success')}</>;
                } else if (lasciati.length) {
                  frase = <>{tr('settings.reports')} <b>{tr('settings.result')} {String(r)}</b> {tr('settings.but_folder')} <b>{lasciati.length} {tr('settings.backups_size')} {num(lasciati.reduce((s, x) => s + x.size_mb, 0), 2)} MB</b>{tr('settings.inconsistent')}</>;
                } else {
                  frase = <>{tr('settings.reports')} <b>{tr('settings.result')} {String(r)}</b> {tr('settings.nothing_left')}</>;
                }
                return (
                  <div key={i} className={'grd' + (!ok && !spento ? ' ko' : '')}>
                    <span className="nm">{String(t.TaskName || '?').replace('Bellomberg-', '')}</span>
                    <span className="es">
                      <span className={'chip ' + (spento ? 'm' : ok ? 'g' : 'r')}>
                        {spento ? tr('settings.disabled') : tr('settings.result_code', {a: String(r ?? '?')})}
                      </span>
                    </span>
                    <span className="qd">{frase}</span>
                    <span className="tm num">
                      {ggmm(last)} {hhmm(last)}
                      <i>{tr('settings.next')} {ggmm(parseTask(t.NextRunTime))} {hhmm(parseTask(t.NextRunTime))}</i>
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* ── LA CONTRADDIZIONE (solo se tiene davvero) ───── */}
          {contraddizione && (
            <div className="contro">
              <div className="lato">
                <div className="k">{tr('settings.guardian_claim')}</div>
                <div className="v">
                  {String(contraddizione.t.TaskName).replace('Bellomberg-', '')}{tr('settings.night_of')} {ggmm(contraddizione.last)}:{' '}
                  <b>{tr('settings.result')} {String(contraddizione.t.LastTaskResult)} {tr('settings.unsuccessful_suffix')}</b>
                </div>
              </div>
              <div className="lato">
                <div className="k">{tr('settings.folder_content')}</div>
                <div className="v">
                  <em>{contraddizione.lasciati[0].filename}</em> {tr('settings.backup_exists')}
                  {contraddizione.lasciati.length > 1 ? tr('settings.others', {a: contraddizione.lasciati.length - 1}) : ''}
                </div>
              </div>
              <div className="lato">
                <div className="k">{tr('settings.interpretation')}</div>
                <div className="v">{tr('settings.save_succeeds')} <b>{tr('settings.after_fails')}</b></div>
              </div>
            </div>
          )}

          {/* ── IL PETTINE DELLE NOTTI ──────────────────────── */}
          <div className="p">
            <div className="ph c">
              {tr('settings.nights_comb')} {calendario.length} {tr('settings.days')}
              <span className="side">{tr('settings.nights_legend')}</span>
            </div>
            <div className="pb" style={{ padding: '14px 12px 26px' }}>
              {calendario.length === 0 ? (
                <div className="nota">
                  <b className="ko">{tr('settings.no_backups_chart')}</b>
                  {backups === null ? tr('settings.list_error') : tr('settings.folder_empty')}
                </div>
              ) : (
                <>
                  <div className="pettine">
                    {calendario.map((c, i) => {
                      const h = c.n ? Math.max(11, Math.round(88 * c.mb / maxMb)) : 0;
                      const [aa, mm, dd] = c.g.split('-');
                      const eti = (i === 0 || i === calendario.length - 1 || dd === '01') ? `${dd}/${mm}` : '';
                      const soloMano = c.n > 0 && c.mano === c.n;
                      return (
                        <span
                          key={c.g}
                          className={'dente' + (c.n ? (soloMano ? ' mano' : '') : ' vuoto') + (i === calendario.length - 1 ? ' oggi' : '')}
                          tabIndex={0}
                          role="img"
                          aria-label={c.n
                            ? tr('settings.night_aria', {a: dd, b: mm, c: aa, d: num(c.mb, 2), e: c.n})
                            : tr('settings.night_empty_aria', {a: dd, b: mm, c: aa})}
                        >
                          <span className="tip">
                            {c.n ? <><b>{num(c.mb, 2)} MB</b> {tr('settings.across')} {c.n} {tr('settings.backup_count')}</> : <b className="ko">{tr('settings.uncovered_night')}</b>}
                            <i>{dd}/{mm}/{aa}{c.n ? (c.mano ? tr('settings.manual_count', {a: c.mano}) : tr('settings.automatic_suffix')) : tr('settings.no_backup_suffix')}</i>
                          </span>
                          <span className="st" style={{ height: h ? h + 'px' : undefined }} />
                          {eti && <span className="gg num">{eti}</span>}
                        </span>
                      );
                    })}
                  </div>
                  <div className="legenda">
                    <span><i className="sw notte" />{tr('settings.overnight')}</span>
                    <span><i className="sw mano" />{tr('settings.manual')}</span>
                    <span><i className="sw vuoto" />{tr('settings.uncovered_night')}</span>
                    <span>{tr('settings.covered')} <b>{calendario.length - scoperte}</b> {tr('settings.nights_of')} <b>{calendario.length}</b></span>
                    <span>{tr('settings.uncovered')} <b className="ko">{scoperte}</b> ({num(100 * scoperte / calendario.length, 1)}%)</span>
                  </div>
                  {nascosti > 0 && (
                    <div className="nota">
                      {tr('settings.backend_reports')} <b>{count}</b> {tr('settings.files_returns')} <b>{backups?.length}</b>:
                      <b className="ko"> {nascosti} {tr('settings.not_listable')}</b> {tr('settings.coverage_partial')}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {/* ── IL REGISTRO (innestato dall'opzione B) ──────── */}
          <div className="p">
            <div className="ph r">
              {tr('settings.register')}
              {/* 3 voci fisse + quella dei lavori guasti, se ce ne sono. La prima
                  stesura diceva "3 voci" mentre ne rendeva 4: un conto sbagliato
                  in un riquadro che si chiama REGISTRO e' il posto peggiore. */}
              <span className="side">{3 + (guasti.length ? 1 : 0)} {tr('settings.entries')}</span>
            </div>
            <div className="pb nopad">
              <table className="reg"><tbody>
                {guasti.length > 0 && (
                  <tr>
                    <td className="st"><span className="chip r">{tr('settings.unsuccessful')}</span></td>
                    <td className="af">{tr('settings.nightly_backup')}</td>
                    <td className="no">
                      {guasti.map(t => tr('settings.task_result', {a: String(t.TaskName).replace('Bellomberg-', ''), b: t.LastTaskResult})).join(' · ')}.
                      {' '}{tr('settings.next_attempt')} {ggmm(parseTask(guasti[0].NextRunTime))} {hhmm(parseTask(guasti[0].NextRunTime))}.
                      {' '}{tr('settings.cause_not_declared')}
                    </td>
                  </tr>
                )}
                <tr>
                  <td className="st"><span className="chip o">{tr('settings.not_declared')}</span></td>
                  <td className="af">{tr('settings.zip_content')}</td>
                  <td className="no">
                    {tr('settings.read_doesnt_tell')} <b>{tr('settings.after')}</b> {tr('settings.after_backup')} <code>quick_check</code> {tr('settings.per_database')}
                  </td>
                </tr>
                <tr>
                  <td className="st"><span className={'chip ' + (nascosti > 0 ? 'r' : 'g')}>{nascosti > 0 ? tr('settings.incomplete') : tr('settings.complete')}</span></td>
                  <td className="af">{tr('settings.backup_list')}</td>
                  <td className="no">
                    {nascosti > 0
                      ? <>{tr('settings.backend_reports_them')} <b>{count}</b> {tr('settings.and_returns')} <b>{backups?.length}</b>: <b>{nascosti} {tr('settings.not_listable')}</b>.</>
                      : <>{tr('settings.backend_reports_them')} <b>{count ?? '?'}</b> {tr('settings.returns_all')} <b>50</b> {tr('settings.truncates')}</>}
                  </td>
                </tr>
                <tr>
                  <td className="st"><span className={'chip ' + (ordinati[0]?.path ? 'g' : 'o')}>{ordinati[0]?.path ? tr('settings.correct') : tr('settings.not_declared')}</span></td>
                  <td className="af">{tr('settings.backup_folder')}</td>
                  <td className="no">
                    {ordinati[0]?.path
                      ? <code>{ordinati[0].path.replace(/[/\\][^/\\]+$/, '')}</code>
                      : <span>{tr('settings.path_not_declared')}</span>}
                    {ordinati[0]?.path && <> {tr('settings.path_from_payload')}</>}
                  </td>
                </tr>
              </tbody></table>
            </div>
          </div>

          {/* ── LA CARTELLA ─────────────────────────────────── */}
          {/* altezza FISSA: l'elenco cresce a ogni notte (oggi 44 righe) e senza
              un tetto si mangerebbe il pannello. Scorre dentro di se'. */}
          <div className="p" style={{ height: 300 }}>
            <div className="ph">
              {tr('settings.in_folder')} {count ?? '?'} {tr('settings.files_date_order')}
              <span className="side">{num(totMb, 0)} MB</span>
            </div>
            <div className="pb" style={{ padding: '8px 10px', flex: '0 0 auto' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <button className="btn am" onClick={creaBackup} disabled={creando}>
                  <HardDrive size={11} />{creando ? tr('settings.backup_updating') : tr('settings.backup_now')}
                </button>
                <span className="nota" style={{ margin: 0 }}>
                  {ultimo ? <>{tr('settings.latest_from')} <b>{ggmm(ultimo)} {hhmm(ultimo)}</b></> : tr('settings.no_backups_folder')}
                </span>
              </div>
            </div>
            <div className="pb scroll nopad">
              {backups === null ? (
                <div style={{ padding: '9px 10px' }} className="nota">
                  <b className="ko">{tr('settings.list_unreadable')}</b>{tr('settings.backend_error')}
                </div>
              ) : ordinati.map(b => {
                const d = parseIso(b.created);
                return (
                  <div key={b.filename} className="bl">
                    <span className="aut">
                      <span className={'chip ' + (isAuto(b.filename) ? 'c' : 'm')}>
                        {isAuto(b.filename) ? tr('settings.night') : tr('settings.manual_upper')}
                      </span>
                    </span>
                    <span className="fn" title={b.path}>{b.filename}</span>
                    <span className="mb num">{num(b.size_mb, 2)} MB</span>
                    <span className="dt num">{ggmm(d)} {hhmm(d)}</span>
                    <span className="rm">
                      <button onClick={() => setDaCancellare(b)} title={tr('settings.delete_prefix') + b.filename}
                              aria-label={tr('settings.delete_prefix') + b.filename}>
                        <Trash2 size={11} />
                      </button>
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* ── LA CODA ─────────────────────────────────────── */}
          <div className="coda">
            <div className="p">
              <div className="ph">{tr('settings.machine')}</div>
              <div className="pb">
                <div className="kv"><span className="k">{tr('settings.status')}</span>
                  <span className="v">{health ? String(health.status || tr('settings.nd')).toUpperCase() : tr('settings.nd_upper')}</span></div>
                <div className="kv"><span className="k">{tr('settings.backend_version')}</span>
                  <span className="v num">{health?.version || tr('settings.nd')}</span></div>
                {/* (B10, 02/09) attribuzione che la licenza di Lightweight Charts
                    richiede (Apache-2.0 con avviso TradingView): il grafico non
                    mostra il logo (`attributionLogo: false` in TerminalChart),
                    quindi il link sta qui. Impianto A scelto dal PM sulla rosa
                    in situ (mockup_f11_licenza/). */}
                <div className="kv"><span className="k">{tr('settings.charts')}</span>
                  <span className="v"><a className="lnk" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts</a> · Apache-2.0</span></div>
                <div className="kv"><span className="k">{tr('settings.enabled_jobs')}</span>
                  <span className="v num">{tasks ? `${attivi.length} / ${tasks.length}` : tr('settings.nd')}</span></div>
                <div className="kv"><span className="k">{tr('settings.failed_jobs')}</span>
                  <span className={'v num' + (guasti.length ? ' ko' : '')}>{tasks ? guasti.length : tr('settings.nd')}</span></div>
                {spenti.length > 0 && (
                  <div className="nota">
                    {spenti.length} {tr('settings.disabled_job_count')}{spenti.map(t => String(t.TaskName).replace('Bellomberg-', '')).join(', ')}{tr('settings.old_result')} <b>{tr('settings.not')}</b> {tr('settings.counts_failure')}
                  </div>
                )}
              </div>
            </div>

            <div className="p">
              <div className="ph">{tr('settings.fx')}</div>
              <div className="pb">
                {fx === null ? <div className="nota"><b className="ko">{tr('settings.fx_unreadable')}</b>{tr('settings.backend_error_short')}</div>
                  : Object.keys(fx).length === 0 ? <div className="nota">{tr('settings.no_fx')}</div>
                  : Object.entries(fx).map(([c, v]) => (
                    <div className="kv" key={c}>
                      <span className="k">1 {c}</span>
                      <span className="v num">
                        {typeof v === 'number' && isFinite(v) ? num(v, 6) : '—'} EUR
                      </span>
                    </div>
                  ))}
              </div>
            </div>

            <div className="p">
              <div className="ph">{tr('settings.engines')}</div>
              <div className="pb">
                {engines === null ? (
                  <div className="nota">
                    {tr('settings.field')} <b>engines</b> {tr('settings.engines_absent')}
                  </div>
                ) : Object.entries(engines).filter(([k]) => !/_error$/.test(k)).map(([k, v]) => (
                  <div className="kv" key={k}>
                    <span className="k">{engineLabels[k] ? tr(engineLabels[k]) : k.replace(/_/g, ' ')}</span>
                    <span className="v">{String(v).replace('claude-', '').toUpperCase()}</span>
                  </div>
                ))}
                {engines && Object.entries(engines).filter(([k]) => /_error$/.test(k)).map(([k, v]) => (
                  <div className="nota" key={k}><b className="ko">{k === 'engines_error' ? tr('settings.engines_read_error') : k}</b>: {String(v)}</div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="pfoot">
          <span>{tr('settings.f19_toggle')}</span>
          <span>{tr('settings.esc_close')}</span>
          <span className="dx">
            {count ?? '?'} {tr('settings.files_dot')} {num(totMb, 0)} MB · {calendario.length} {tr('settings.days_span')}
          </span>
        </div>
      </div>

      {/* ── la conferma prima di cancellare ─────────────────
          Non e' un vezzo: un backup cancellato NON passa dal cestino
          e non e' recuperabile. Al posto del confirm() nudo del
          browser, che in Electron e' anche fuori stile. */}
      {daCancellare && (
        <div className="f11-ask" role="dialog" aria-modal="true" aria-label={tr('settings.delete_confirm')}>
          <div className="box">
            <div className="bh">{tr('settings.delete_permanent')}</div>
            <div className="bb">
              <b>{daCancellare.filename}</b><br />
              {num(daCancellare.size_mb, 2)} MB · {ggmm(parseIso(daCancellare.created))} {hhmm(parseIso(daCancellare.created))}
              <div className="nota">
                {tr('settings.deletion_is')} <b className="ko">{tr('settings.permanent')}</b>{tr('settings.delete_explanation')} <b>{Math.max(0, (count ?? 1) - 1)}</b>.
              </div>
            </div>
            <div className="bf">
              <button className="btn" onClick={() => setDaCancellare(null)} autoFocus>{tr('settings.cancel')}</button>
              <button className="btn ko" onClick={() => cancella(daCancellare)}>{tr('settings.delete_forever')}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
