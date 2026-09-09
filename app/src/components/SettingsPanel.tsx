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
import '../pages/settings-veglia.css';

type Backup = { filename: string; path: string; size_mb: number; created: string };
type Esito = { ok: boolean; text: string };

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
const ggmm = (d: Date | null) => (d ? `${due(d.getDate())}/${due(d.getMonth() + 1)}` : 'n.d.');
const hhmm = (d: Date | null) => (d ? `${due(d.getHours())}:${due(d.getMinutes())}` : '');
const chiave = (d: Date) => `${d.getFullYear()}-${due(d.getMonth() + 1)}-${due(d.getDate())}`;
const isAuto = (f: string) => /^bellomberg_backup_/i.test(f);

export default function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tasks, setTasks] = useState<any[] | null>(null);
  const [backups, setBackups] = useState<Backup[] | null>(null);
  const [count, setCount] = useState<number | null>(null);
  const [health, setHealth] = useState<any>(null);
  const [fx, setFx] = useState<Record<string, number> | null>(null);
  const [engines, setEngines] = useState<Record<string, string> | null>(null);
  // regola 14/07: i fetch falliti si DICHIARANO per nome, mai pannelli vuoti muti
  const [buchi, setBuchi] = useState<string[]>([]);
  const [esito, setEsito] = useState<Esito | null>(null);
  const [creando, setCreando] = useState(false);
  const [daCancellare, setDaCancellare] = useState<Backup | null>(null);

  const box = useRef<HTMLDivElement>(null);
  const tornaA = useRef<HTMLElement | null>(null);

  const segnala = useCallback((nome: string) => {
    setBuchi(prev => (prev.includes(nome) ? prev : [...prev, nome]));
  }, []);

  const caricaBackups = useCallback(async () => {
    try {
      const r = await Bellomberg.dbBackupsList();
      setBackups(r.backups || []);
      setCount(typeof r.count === 'number' ? r.count : null);
      setBuchi(prev => prev.filter(b => b !== 'elenco dei backup'));
    } catch {
      setBackups(null);
      segnala('elenco dei backup');
    }
  }, [segnala]);

  useEffect(() => {
    if (!open) return;
    setBuchi([]);
    Bellomberg.scheduledTasks().then(r => setTasks(r.tasks || [])).catch(() => { setTasks(null); segnala('lavori schedulati'); });
    Bellomberg.health().then(setHealth).catch(() => { setHealth(null); segnala('stato del backend'); });
    Bellomberg.fx().then(r => setFx(r.rates || {})).catch(() => { setFx(null); segnala('cambi'); });
    Bellomberg.agentsList()
      .then(r => setEngines((r.engines as unknown as Record<string, string>) || null))
      .catch(() => { setEngines(null); segnala('motori'); });
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
        text: `Backup creato: ${r.size_mb} MB, ${r.files_count} file — ${r.backup_path}` +
          (r.ok ? '' : ` · ATTENZIONE: ${ko.length} database ESCLUSO dal backup per quick_check fallito (${ko.map(([k, v]) => `${k}: ${v}`).join(' · ')})`),
      });
      await caricaBackups();
    } catch (e: any) {
      setEsito({ ok: false, text: 'Backup NON creato: ' + (e?.response?.data?.detail || e?.message || String(e)) });
    } finally {
      setCreando(false);
    }
  };

  const cancella = async (b: Backup) => {
    setDaCancellare(null); setEsito(null);
    try {
      await Bellomberg.dbBackupDelete(b.filename);
      setEsito({ ok: true, text: `Eliminato ${b.filename} (${b.size_mb} MB). Non e' recuperabile: non passa dal cestino.` });
      await caricaBackups();
    } catch (e: any) {
      setEsito({ ok: false, text: 'NON eliminato: ' + (e?.response?.data?.detail || e?.message || String(e)) });
    }
  };

  if (!open) return null;

  const ordinati = (backups || []).slice().sort((a, b) => String(b.created).localeCompare(String(a.created)));
  const ultimo = ordinati[0] ? parseIso(ordinati[0].created) : null;

  return (
    <>
      <div className="f11-scrim" onClick={onClose} />
      <div className="f11v" role="dialog" aria-modal="true" aria-label="Impostazioni" tabIndex={-1} ref={box}>
        <span className="sq tl" /><span className="sq tr" /><span className="sq bl" /><span className="sq br" />

        <div className="phead">
          <span className="tt">Impostazioni</span>
          <span className="sub">La veglia — la guardia di stanotte</span>
          <button className="x" onClick={onClose}><X size={11} /> ESC CHIUDE</button>
        </div>

        <div className="pbody">
          {buchi.length > 0 && (
            <div className="buco">
              DATI NON CARICATI: {buchi.join(' · ')} — il backend ha risposto con un errore.
              I riquadri qui sotto che dipendono da questi dati sono INCOMPLETI, non vuoti.
              Richiudi e riapri il pannello per riprovare.
            </div>
          )}

          {esito && (
            <div className={'esito ' + (esito.ok ? 'ok' : 'ko')}>
              {esito.ok ? <CheckCircle size={11} style={{ verticalAlign: -1, marginRight: 6 }} />
                        : <AlertCircle size={11} style={{ verticalAlign: -1, marginRight: 6 }} />}
              {esito.text}
            </div>
          )}

          {/* ── I GUARDIANI ─────────────────────────────────── */}
          <div className="p">
            <div className="ph a">
              I guardiani — {tasks ? tasks.length : '?'} lavori automatici
              <span className="side">esito diverso da 0 = non riuscito</span>
            </div>
            <div className="pb nopad">
              {tasks === null ? (
                <div style={{ padding: '9px 10px' }} className="nota">
                  <b className="ko">lavori schedulati NON leggibili</b>: il backend ha risposto con un errore.
                </div>
              ) : tasks.length === 0 ? (
                <div style={{ padding: '9px 10px' }} className="nota">
                  Nessun lavoro schedulato. Esegui <b>install_all_schedulers.ps1</b> come amministratore.
                </div>
              ) : tasks.map((t, i) => {
                const r = t.LastTaskResult;
                const spento = String(t.State).toLowerCase() === 'disabled';
                const ok = r === 0;
                const last = parseTask(t.LastRunTime);
                const lasciati = last ? (perGiorno[chiave(last)] || []) : [];
                let frase: React.ReactNode;
                if (spento) {
                  frase = <>spento per scelta — l'esito <b>{String(r)}</b> e' l'ultimo di quando girava, non di stanotte</>;
                } else if (ok) {
                  frase = <>esito <b>0</b>: il lavoro dichiara che e' andata bene</>;
                } else if (lasciati.length) {
                  frase = <>dichiara <b>esito {String(r)}</b> — ma quella notte in cartella c'e' <b>{lasciati.length} backup
                    da {lasciati.reduce((s, x) => s + x.size_mb, 0).toFixed(2)} MB</b>. Le due cose non tornano.</>;
                } else {
                  frase = <>dichiara <b>esito {String(r)}</b> e non ha lasciato nulla in cartella</>;
                }
                return (
                  <div key={i} className={'grd' + (!ok && !spento ? ' ko' : '')}>
                    <span className="nm">{String(t.TaskName || '?').replace('Bellomberg-', '')}</span>
                    <span className="es">
                      <span className={'chip ' + (spento ? 'm' : ok ? 'g' : 'r')}>
                        {spento ? 'SPENTO' : `ESITO ${String(r ?? '?')}`}
                      </span>
                    </span>
                    <span className="qd">{frase}</span>
                    <span className="tm num">
                      {ggmm(last)} {hhmm(last)}
                      <i>prossimo {ggmm(parseTask(t.NextRunTime))} {hhmm(parseTask(t.NextRunTime))}</i>
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
                <div className="k">La dichiarazione del guardiano</div>
                <div className="v">
                  {String(contraddizione.t.TaskName).replace('Bellomberg-', '')}, notte del {ggmm(contraddizione.last)}:{' '}
                  <b>esito {String(contraddizione.t.LastTaskResult)} — non riuscito</b>
                </div>
              </div>
              <div className="lato">
                <div className="k">Il contenuto della cartella</div>
                <div className="v">
                  <em>{contraddizione.lasciati[0].filename}</em> — il backup c'e'
                  {contraddizione.lasciati.length > 1 ? ` (e altri ${contraddizione.lasciati.length - 1})` : ''}
                </div>
              </div>
              <div className="lato">
                <div className="k">La lettura</div>
                <div className="v">il salvataggio riesce, <b>fallisce quello che viene dopo</b></div>
              </div>
            </div>
          )}

          {/* ── IL PETTINE DELLE NOTTI ──────────────────────── */}
          <div className="p">
            <div className="ph c">
              Il pettine delle notti — {calendario.length} giorni
              <span className="side">dente cavo = notte scoperta · altezza = MB · punta per il numero esatto</span>
            </div>
            <div className="pb" style={{ padding: '14px 12px 26px' }}>
              {calendario.length === 0 ? (
                <div className="nota">
                  <b className="ko">nessun backup da rappresentare</b>
                  {backups === null ? ': l\'elenco non e\' leggibile (errore del backend).' : ': la cartella dei backup e\' vuota.'}
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
                            ? `${dd}/${mm}/${aa}: ${c.mb.toFixed(2)} MB in ${c.n} backup`
                            : `${dd}/${mm}/${aa}: notte scoperta, nessun backup`}
                        >
                          <span className="tip">
                            {c.n ? <><b>{c.mb.toFixed(2)} MB</b> in {c.n} backup</> : <b className="ko">notte scoperta</b>}
                            <i>{dd}/{mm}/{aa}{c.n ? (c.mano ? ` · ${c.mano} presi a mano` : ' · automatico') : ' — nessun backup'}</i>
                          </span>
                          <span className="st" style={{ height: h ? h + 'px' : undefined }} />
                          {eti && <span className="gg num">{eti}</span>}
                        </span>
                      );
                    })}
                  </div>
                  <div className="legenda">
                    <span><i className="sw notte" />notturno</span>
                    <span><i className="sw mano" />preso a mano</span>
                    <span><i className="sw vuoto" />notte scoperta</span>
                    <span>coperte <b>{calendario.length - scoperte}</b> notti su <b>{calendario.length}</b></span>
                    <span>scoperte <b className="ko">{scoperte}</b> ({(100 * scoperte / calendario.length).toFixed(1)}%)</span>
                  </div>
                  {nascosti > 0 && (
                    <div className="nota">
                      Il backend dichiara <b>{count}</b> file e ne consegna <b>{backups?.length}</b>:
                      <b className="ko"> {nascosti} non sono elencabili</b> da qui (il taglio e' a 50).
                      La copertura vera puo' essere migliore di quella disegnata.
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {/* ── IL REGISTRO (innestato dall'opzione B) ──────── */}
          <div className="p">
            <div className="ph r">
              Il registro — quello che il terminale afferma, e quanto regge
              {/* 3 voci fisse + quella dei lavori guasti, se ce ne sono. La prima
                  stesura diceva "3 voci" mentre ne rendeva 4: un conto sbagliato
                  in un riquadro che si chiama REGISTRO e' il posto peggiore. */}
              <span className="side">{3 + (guasti.length ? 1 : 0)} voci</span>
            </div>
            <div className="pb nopad">
              <table className="reg"><tbody>
                {guasti.length > 0 && (
                  <tr>
                    <td className="st"><span className="chip r">NON RIUSCITO</span></td>
                    <td className="af">Il salvataggio notturno del database</td>
                    <td className="no">
                      {guasti.map(t => `${String(t.TaskName).replace('Bellomberg-', '')} esito ${t.LastTaskResult}`).join(' · ')}.
                      {' '}Prossimo tentativo {ggmm(parseTask(guasti[0].NextRunTime))} {hhmm(parseTask(guasti[0].NextRunTime))}.
                      {' '}La causa e' fuori dal terminale: sta nello script <code>run_db_backup.bat</code>.
                    </td>
                  </tr>
                )}
                <tr>
                  <td className="st"><span className="chip o">NON DICHIARATO</span></td>
                  <td className="af">Il contenuto dello zip</td>
                  <td className="no">
                    Nessuna chiamata di sola lettura lo dice. Si accende <b>dopo</b> un backup fatto da qui:
                    la risposta porta l'elenco dei file e il <code>quick_check</code> per database.
                  </td>
                </tr>
                <tr>
                  <td className="st"><span className={'chip ' + (nascosti > 0 ? 'r' : 'g')}>{nascosti > 0 ? 'INCOMPLETO' : 'COMPLETO'}</span></td>
                  <td className="af">L'elenco dei backup</td>
                  <td className="no">
                    {nascosti > 0
                      ? <>Il backend ne dichiara <b>{count}</b> e ne consegna <b>{backups?.length}</b>: <b>{nascosti} non sono elencabili</b>.</>
                      : <>Il backend ne dichiara <b>{count ?? '?'}</b> e li consegna tutti. Sopra i <b>50</b> comincia a troncarli, e allora questo conto va letto come parziale.</>}
                  </td>
                </tr>
                <tr>
                  <td className="st"><span className="chip g">CORRETTO</span></td>
                  <td className="af">La cartella dei backup</td>
                  <td className="no">
                    {ordinati[0]
                      ? <code>{ordinati[0].path.replace(/[/\\][^/\\]+$/, '')}</code>
                      : <code>data/backups</code>}
                    {' '}— percorso vero dal payload, non una stringa scritta a mano.
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
              In cartella — {count ?? '?'} file, in ordine di data
              <span className="side">{totMb.toFixed(0)} MB</span>
            </div>
            <div className="pb" style={{ padding: '8px 10px', flex: '0 0 auto' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <button className="btn am" onClick={creaBackup} disabled={creando}>
                  <HardDrive size={11} />{creando ? 'BACKUP IN CORSO...' : 'FAI UN BACKUP ADESSO'}
                </button>
                <span className="nota" style={{ margin: 0 }}>
                  {ultimo ? <>l'ultimo e' del <b>{ggmm(ultimo)} {hhmm(ultimo)}</b></> : 'nessun backup in cartella'}
                </span>
              </div>
            </div>
            <div className="pb scroll nopad">
              {backups === null ? (
                <div style={{ padding: '9px 10px' }} className="nota">
                  <b className="ko">elenco NON leggibile</b>: il backend ha risposto con un errore.
                </div>
              ) : ordinati.map(b => {
                const d = parseIso(b.created);
                return (
                  <div key={b.filename} className="bl">
                    <span className="aut">
                      <span className={'chip ' + (isAuto(b.filename) ? 'c' : 'm')}>
                        {isAuto(b.filename) ? 'NOTTE' : 'A MANO'}
                      </span>
                    </span>
                    <span className="fn" title={b.path}>{b.filename}</span>
                    <span className="mb num">{b.size_mb.toFixed(2)} MB</span>
                    <span className="dt num">{ggmm(d)} {hhmm(d)}</span>
                    <span className="rm">
                      <button onClick={() => setDaCancellare(b)} title={'Elimina ' + b.filename}
                              aria-label={'Elimina ' + b.filename}>
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
              <div className="ph">La macchina</div>
              <div className="pb">
                <div className="kv"><span className="k">stato</span>
                  <span className="v">{health ? String(health.status || 'n.d.').toUpperCase() : 'N.D.'}</span></div>
                <div className="kv"><span className="k">versione backend</span>
                  <span className="v num">{health?.version || 'n.d.'}</span></div>
                {/* (B10, 02/09) attribuzione che la licenza di Lightweight Charts
                    richiede (Apache-2.0 con avviso TradingView): il grafico non
                    mostra il logo (`attributionLogo: false` in TerminalChart),
                    quindi il link sta qui. Impianto A scelto dal PM sulla rosa
                    in situ (mockup_f11_licenza/). */}
                <div className="kv"><span className="k">grafici</span>
                  <span className="v"><a className="lnk" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts</a> · Apache-2.0</span></div>
                <div className="kv"><span className="k">lavori accesi</span>
                  <span className="v num">{tasks ? `${attivi.length} / ${tasks.length}` : 'n.d.'}</span></div>
                <div className="kv"><span className="k">non riusciti</span>
                  <span className={'v num' + (guasti.length ? ' ko' : '')}>{tasks ? guasti.length : 'n.d.'}</span></div>
                {spenti.length > 0 && (
                  <div className="nota">
                    {spenti.length} lavoro spento per scelta ({spenti.map(t => String(t.TaskName).replace('Bellomberg-', '')).join(', ')}):
                    il suo vecchio esito <b>non</b> conta come guasto.
                  </div>
                )}
              </div>
            </div>

            <div className="p">
              <div className="ph">Cambi</div>
              <div className="pb">
                {fx === null ? <div className="nota"><b className="ko">cambi NON leggibili</b>: errore del backend.</div>
                  : Object.keys(fx).length === 0 ? <div className="nota">nessun cambio consegnato</div>
                  : Object.entries(fx).map(([c, v]) => (
                    <div className="kv" key={c}>
                      <span className="k">1 {c}</span>
                      <span className="v num">
                        {typeof v === 'number' && isFinite(v) ? v.toFixed(6) : '—'} EUR
                      </span>
                    </div>
                  ))}
              </div>
            </div>

            <div className="p">
              <div className="ph">Motori</div>
              <div className="pb">
                {engines === null ? (
                  <div className="nota">
                    campo <b>engines</b> assente: si accende al riavvio del backend.
                    Nessun modello e' scritto a mano qui.
                  </div>
                ) : Object.entries(engines).filter(([k]) => !/_error$/.test(k)).map(([k, v]) => (
                  <div className="kv" key={k}>
                    <span className="k">{k.replace(/_/g, ' ')}</span>
                    <span className="v">{String(v).replace('claude-', '').toUpperCase()}</span>
                  </div>
                ))}
                {engines && Object.entries(engines).filter(([k]) => /_error$/.test(k)).map(([k, v]) => (
                  <div className="nota" key={k}><b className="ko">{k}</b>: {String(v)}</div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="pfoot">
          <span>F19 APRE E CHIUDE</span>
          <span>ESC CHIUDE</span>
          <span className="dx">
            {count ?? '?'} file · {totMb.toFixed(0)} MB · {calendario.length} giorni di arco
          </span>
        </div>
      </div>

      {/* ── la conferma prima di cancellare ─────────────────
          Non e' un vezzo: un backup cancellato NON passa dal cestino
          e non e' recuperabile. Al posto del confirm() nudo del
          browser, che in Electron e' anche fuori stile. */}
      {daCancellare && (
        <div className="f11-ask" role="dialog" aria-modal="true" aria-label="Conferma eliminazione">
          <div className="box">
            <div className="bh">Eliminazione definitiva</div>
            <div className="bb">
              <b>{daCancellare.filename}</b><br />
              {daCancellare.size_mb.toFixed(2)} MB · {ggmm(parseIso(daCancellare.created))} {hhmm(parseIso(daCancellare.created))}
              <div className="nota">
                L'eliminazione e' <b className="ko">definitiva</b>: il file non passa dal cestino
                e non esistono copie shadow del volume. Dopo, in cartella
                ne restano <b>{Math.max(0, (count ?? 1) - 1)}</b>.
              </div>
            </div>
            <div className="bf">
              <button className="btn" onClick={() => setDaCancellare(null)} autoFocus>ANNULLA</button>
              <button className="btn ko" onClick={() => cancella(daCancellare)}>ELIMINA DEFINITIVAMENTE</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
