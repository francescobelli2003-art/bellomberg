/* Viste del pannello Impostazioni: solo presentazione. Lo stato, le letture e
   le azioni restano in SettingsPanel (l'harness SSR indicizza gli hook in un
   ordine globale, quindi qui niente hook). */
import type { CSSProperties, ReactNode } from 'react';
import {
  AlertCircle, Check, Clock, Cpu, ExternalLink, FileText, Globe, HardDrive, Laptop, Loader2,
  MessageSquare, Moon, Newspaper, Power, ShieldCheck, Trash2, Users,
} from 'lucide-react';
import type { useT } from '../../i18n/provider';
import {
  type Backup, type Giorno, type GruppoMotori, type Lavoro, type SezioneId,
  chiave, engineLabels, etichetteAsse, etichetteMb, ggmm, gruppoMotore, hhmm, isAuto, nomeLavoro, parseIso, parseTask, spento,
} from './logica';

export type Tr = ReturnType<typeof useT>;
export type Numero = (value: number, digits?: number) => string;

const Status = ({ children }: { children: ReactNode }) => (
  <div className="bbn-imp-note is-info" role="status" aria-busy="true"><Loader2 size={14} className="bbn-imp-spin" />{children}</div>
);

export function SezioneHead({ n, titolo, extra, onStep, tr }: { n: string; titolo: string; extra?: ReactNode; onStep: (d: number) => void; tr: Tr }) {
  return (
    <div className="bbn-imp-sec-head">
      <span className="bbn-imp-num">{n}</span>
      <h2>{titolo}</h2>
      {extra}
      <span className="bbn-imp-grow" />
      <button type="button" className="bbn-imp-icon" aria-label={tr('settingsPage.prevSection')} onClick={() => onStep(-1)}>
        <span aria-hidden="true" className="bbn-imp-chev is-left" />
      </button>
      <button type="button" className="bbn-imp-icon" aria-label={tr('settingsPage.nextSection')} onClick={() => onStep(1)}>
        <span aria-hidden="true" className="bbn-imp-chev" />
      </button>
    </div>
  );
}

/* ── 01 Generale ─────────────────────────────────────────── */
export function SezioneGenerale(p: {
  tr: Tr; lingua: ReactNode; health: any; loadingHealth: boolean; tasks: Lavoro[] | null; nonSupportati: boolean;
  attivi: number; guasti: number; spenti: Lavoro[]; scorciatoie: Array<[string, string]>;
}) {
  const { tr } = p;
  const na = <span className="bbn-imp-pill is-muted">{tr('settingsPage.naMac')}</span>;
  const nd = tr('settings.nd');
  const stato = p.health ? String(p.health.status || nd) : p.loadingHealth ? null : tr('settings.nd_upper');
  return (
    <div className="bbn-imp-gen">
      <div className="bbn-imp-card">{p.lingua}</div>
      <div className="bbn-imp-card bbn-imp-sys">
        <div className="bbn-imp-card-head"><h3>{tr('settingsPage.systemTitle')}</h3><span className="bbn-imp-card-note">{tr('settingsPage.systemNote')}</span></div>
        <div className="bbn-imp-card-body">
          <div className="bbn-imp-kvs">
            <div className="bbn-imp-kv"><span className="k">{tr('settingsPage.backendStatus')}</span>
              <span className="v">{stato === null ? <span role="status" aria-busy="true">{tr('ui.loading')}</span>
                : <span className={'bbn-imp-pill ' + (String(p.health?.status).toLowerCase() === 'ok' ? 'is-ok' : 'is-muted')}>{String(p.health?.status).toLowerCase() === 'ok' && <Check size={13} />}{stato.toUpperCase()}</span>}</span></div>
            <div className="bbn-imp-kv"><span className="k">{tr('settingsPage.backendVersion')}</span>
              <span className="v">{p.health?.version || (p.loadingHealth ? tr('ui.loading') : nd)}</span></div>
            <div className="bbn-imp-kv"><span className="k">{tr('settingsPage.enabledJobs')}</span>
              <span className="v">{p.nonSupportati ? na : p.tasks ? tr('settingsPage.ofN', { a: p.attivi, b: p.tasks.length }) : nd}</span></div>
            <div className="bbn-imp-kv"><span className="k">{tr('settingsPage.failedJobs')}</span>
              <span className={'v' + (p.guasti ? ' is-ko' : '')}>{p.nonSupportati ? na : p.tasks ? p.guasti : nd}</span></div>
            {/* (B10, 02/09) attribuzione richiesta dalla licenza di Lightweight Charts
                (Apache-2.0 con avviso TradingView): il grafico non mostra il logo. */}
            <div className="bbn-imp-kv"><span className="k">{tr('settingsPage.charts')}</span>
              <span className="v"><a className="bbn-imp-link" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts<ExternalLink size={13} /></a> · Apache-2.0</span></div>
          </div>
          {p.spenti.length > 0 && (
            <div className="bbn-imp-note is-info"><Power size={14} />
              <span>{tr('settingsPage.disabledNote', { names: p.spenti.map(nomeLavoro).join(', ') })}</span></div>
          )}
        </div>
      </div>
      <div className="bbn-imp-card bbn-imp-keys-card">
        <div className="bbn-imp-card-head"><h3>{tr('settingsPage.shortcutsTitle')}</h3></div>
        <div className="bbn-imp-card-body"><div className="bbn-imp-keys">
          {p.scorciatoie.map(([k, d]) => <div className="bbn-imp-key" key={k}><kbd>{k}</kbd><span>{d}</span></div>)}
        </div></div>
      </div>
    </div>
  );
}

/* ── 02 Backup ───────────────────────────────────────────── */
function Grafico({ cal, tr, num }: { cal: Giorno[]; tr: Tr; num: Numero }) {
  const max = Math.max(1, ...cal.map(c => c.mb));
  const fitto = cal.length > 45;
  const [alto, mezzo] = etichetteMb(max, num);
  return (
    <div className={'bbn-imp-chart' + (fitto ? ' is-dense' : '')}>
      <div className="bbn-imp-plot">
        <span className="bbn-imp-grid" style={{ top: 0 }} /><span className="bbn-imp-gl" style={{ top: 0 }}>{alto} MB</span>
        <span className="bbn-imp-grid" style={{ top: '50%' }} /><span className="bbn-imp-gl" style={{ top: '50%' }}>{mezzo}</span>
        {cal.map((c, i) => {
          const h = c.n ? Math.max(6, Math.round(100 * c.mb / max)) : 0;
          const [aa, mm, dd] = c.g.split('-');
          const data = `${dd}/${mm}/${aa}`;
          const soloMano = c.n > 0 && c.mano === c.n;
          const lato = i < 3 ? ' is-start' : i > cal.length - 4 ? ' is-end' : '';
          return (
            <span key={c.g} tabIndex={0} role="img"
              className={'bbn-imp-bar' + (c.n ? (soloMano ? ' is-manual' : '') : ' is-empty') + (i === cal.length - 1 ? ' is-last' : '') + lato}
              style={{ '--h': (c.n ? h : 0) + '%' } as CSSProperties}
              aria-label={c.n ? tr(c.n === 1 ? 'settingsPage.nightAria_one' : 'settingsPage.nightAria_other', { d: data, mb: num(c.mb, 2), n: c.n }) : tr('settingsPage.nightEmptyAria', { d: data })}>
              <i />
              <span className="bbn-imp-tip">
                {c.n ? <><b>{tr(c.n === 1 ? 'settingsPage.tipBackups_one' : 'settingsPage.tipBackups_other', { mb: num(c.mb, 2), n: c.n })}</b>
                  <span>{data} · {c.mano ? tr(c.mano === 1 ? 'settingsPage.tipManual_one' : 'settingsPage.tipManual_other', { n: c.mano }) : tr('settingsPage.tipAuto')}</span></>
                  : <><b>{tr('settingsPage.tipEmpty')}</b><span>{data}</span></>}
              </span>
            </span>
          );
        })}
      </div>
      <div className="bbn-imp-xax" aria-hidden="true">{etichetteAsse(cal).map((e, i) => <span key={cal[i].g}>{e}</span>)}</div>
    </div>
  );
}

export function SezioneBackup(p: {
  tr: Tr; num: Numero; backups: Backup[] | null; loading: boolean; count: number | null; ordinati: Backup[]; cal: Giorno[];
  scoperte: number; totMb: number; nascosti: number; guasti: Lavoro[]; creando: boolean; locale: string;
  onCrea: () => void; onElimina: (b: Backup, el: HTMLElement) => void;
}) {
  const { tr, num } = p;
  const nd = tr('settings.nd');
  const ultimo = p.ordinati[0] ? parseIso(p.ordinati[0].created) : null;
  const coperte = p.cal.length - p.scoperte;
  const oggi = new Date();
  const gruppi: Array<[string, Backup[]]> = [];
  p.ordinati.forEach(b => {
    const d = parseIso(b.created);
    const nome = !d ? nd
      : d.toDateString() === oggi.toDateString() ? tr('settingsPage.groupToday')
      : (s => s.charAt(0).toUpperCase() + s.slice(1))(d.toLocaleDateString(p.locale, { month: 'long', year: 'numeric' }));
    const g = gruppi[gruppi.length - 1];
    if (g && g[0] === nome) g[1].push(b); else gruppi.push([nome, [b]]);
  });
  const percorso = p.ordinati[0]?.path ? p.ordinati[0].path.replace(/[/\\][^/\\]+$/, '') : null;
  const prossimo = p.guasti[0] ? parseTask(p.guasti[0].NextRunTime) : null;
  return (
    <div className="bbn-imp-bk">
      <div className="bbn-imp-tiles">
        <div className="bbn-imp-tile"><div className="k">{tr('settingsPage.tileLast')}</div>
          <div className="v">{ultimo ? <>{ggmm(ultimo, nd)} <small>{hhmm(ultimo)}</small></> : nd}</div></div>
        <div className="bbn-imp-tile"><div className="k">{tr('settingsPage.tileCovered')}</div>
          <div className="v">{p.cal.length ? <>{coperte} <small>{tr('settingsPage.ofSmall', { n: p.cal.length })}</small></> : nd}</div></div>
        <div className="bbn-imp-tile"><div className="k">{tr('settingsPage.tileUncovered')}</div>
          <div className={'v' + (p.scoperte ? ' is-ko' : '')}>{p.cal.length ? <>{p.scoperte} <small>{num(100 * p.scoperte / p.cal.length, 1)}%</small></> : nd}</div></div>
        <div className="bbn-imp-tile"><div className="k">{tr('settingsPage.tileSpace')}</div>
          <div className="v">{p.backups ? <>{num(p.totMb, 0)} MB <small>{tr(p.count === 1 ? 'settingsPage.filesN_one' : 'settingsPage.filesN_other', { n: p.count ?? '?' })}</small></> : nd}</div></div>
      </div>

      <div className="bbn-imp-card bbn-imp-bk-chart">
        <div className="bbn-imp-card-head">
          <h3>{tr('settingsPage.chartTitle')}</h3>
          <span className="bbn-imp-card-note">{tr(p.cal.length === 1 ? 'settingsPage.chartNote_one' : 'settingsPage.chartNote_other', { n: p.cal.length })}</span>
          <span className="bbn-imp-grow" />
          <div className="bbn-imp-legend">
            <span><i className="sw a" />{tr('settingsPage.legendOvernight')}</span>
            <span><i className="sw m" />{tr('settingsPage.legendManual')}</span>
            <span><i className="sw e" />{tr('settingsPage.legendUncovered')}</span>
            <span><i className="sw t" />{tr('settingsPage.legendToday')}</span>
          </div>
        </div>
        <div className="bbn-imp-card-body">
          {p.cal.length === 0 ? (
            p.backups === null && p.loading ? <Status>{tr('ui.loading')}</Status>
              : <div className={'bbn-imp-note ' + (p.backups === null ? 'is-ko' : 'is-info')}><AlertCircle size={14} />
                <span><b>{tr('settingsPage.chartEmpty')}</b> · {p.backups === null ? tr('settingsPage.listError') : tr('settingsPage.folderEmpty')}</span></div>
          ) : <Grafico cal={p.cal} tr={tr} num={num} />}
          {p.nascosti > 0 && (
            <div className="bbn-imp-note is-warn"><AlertCircle size={14} />
              <span>{tr('settingsPage.truncated', { count: p.count ?? '?', n: p.backups?.length ?? 0, h: p.nascosti })}</span></div>
          )}
        </div>
      </div>

      <div className="bbn-imp-card bbn-imp-files-card">
        <div className="bbn-imp-card-head">
          <h3>{tr('settingsPage.filesTitle')}</h3>
          <span className="bbn-imp-card-note">{tr(p.count === 1 ? 'settingsPage.filesNote_one' : 'settingsPage.filesNote_other', { n: p.count ?? '?' })}</span>
          <span className="bbn-imp-grow" />
          <button type="button" className="btn am bbn-imp-btn is-primary" data-azione="backup" onClick={p.onCrea} disabled={p.creando}>
            {p.creando ? <Loader2 size={15} className="bbn-imp-spin" /> : <HardDrive size={15} />}
            {p.creando ? tr('settingsPage.backupRunning') : tr('settingsPage.backupNow')}
          </button>
        </div>
        <div className="bbn-imp-card-body bbn-imp-files">
          {p.backups === null && p.loading ? <Status>{tr('ui.loading')}</Status>
            : p.backups === null ? <div className="bbn-imp-note is-ko"><AlertCircle size={14} /><span><b>{tr('settingsPage.listUnreadable')}</b> · {tr('settingsPage.jobsUnreadableBody')}</span></div>
            : p.ordinati.length === 0 ? <div className="bbn-imp-note is-info"><span>{tr('settingsPage.folderEmpty')}</span></div>
            : gruppi.map(([nome, fs]) => (
              <div key={nome} className="bbn-imp-grp">
                <div className="bbn-imp-grp-h">{nome}</div>
                {fs.map(b => {
                  const d = parseIso(b.created);
                  const label = tr('settingsPage.deleteAria', { f: b.filename });
                  return (
                    <div key={b.filename} className="bl bbn-imp-fr">
                      <span>{isAuto(b.filename)
                        ? <span className="bbn-imp-pill is-accent"><Moon size={12} />{tr('settingsPage.pillNight')}</span>
                        : <span className="bbn-imp-pill is-muted">{tr('settingsPage.pillManual')}</span>}</span>
                      <span className="fn" title={b.path}>{b.filename}</span>
                      <span className="mb">{num(b.size_mb, 2)} MB</span>
                      <span className="dt">{ggmm(d, nd)} {hhmm(d)}</span>
                      <button type="button" className="bbn-imp-icon is-danger" title={label} aria-label={label}
                        onClick={e => p.onElimina(b, e.currentTarget)}><Trash2 size={14} /></button>
                    </div>
                  );
                })}
              </div>
            ))}
        </div>
      </div>

      <div className="bbn-imp-card bbn-imp-rel-card">
        <div className="bbn-imp-card-head"><h3>{tr('settingsPage.relTitle')}</h3><span className="bbn-imp-card-note">{tr('settingsPage.relNote')}</span></div>
        <div className="bbn-imp-card-body bbn-imp-rel">
          {p.guasti.length > 0 && (
            <div className="bbn-imp-rr"><span className="h">{tr('settingsPage.relFailedTitle')}</span>
              <span className="bbn-imp-pill is-ko">{tr('settingsPage.pillUnsuccessful')}</span>
              <span className="b">
                {p.guasti.map(t => tr('settingsPage.taskResult', { a: nomeLavoro(t), b: String(t.LastTaskResult) })).join(' · ')}.{' '}
                {tr('settingsPage.relFailedNext', { t: `${ggmm(prossimo, nd)} ${hhmm(prossimo)}`.trim() })}{' '}
                {tr('settingsPage.causeNotDeclared')}
              </span></div>
          )}
          <div className="bbn-imp-rr"><span className="h">{tr('settingsPage.relZipTitle')}</span>
            <span className="bbn-imp-pill is-warn">{tr('settingsPage.pillNotDeclared')}</span>
            <span className="b">{tr('settingsPage.relZipA')} <b>{tr('settingsPage.relZipAfter')}</b> {tr('settingsPage.relZipB')} <code>quick_check</code> {tr('settingsPage.relZipC')}</span></div>
          {/* Senza `count` (o senza elenco) la completezza non e' misurabile: si dichiara, non si promuove a «Completo». */}
          <div className="bbn-imp-rr"><span className="h">{tr('settingsPage.relListTitle')}</span>
            <span className={'bbn-imp-pill ' + (p.nascosti > 0 ? 'is-ko' : p.count == null || p.backups === null ? 'is-warn' : 'is-ok')}>{p.nascosti > 0 ? tr('settingsPage.pillIncomplete')
              : p.count == null || p.backups === null ? tr('settingsPage.pillNotDeclared') : tr('settingsPage.pillComplete')}</span>
            <span className="b">{p.nascosti > 0
              ? tr('settingsPage.relListIncomplete', { count: p.count ?? '?', n: p.backups?.length ?? 0, h: p.nascosti })
              : p.count == null || p.backups === null ? tr('settingsPage.relListNoCount')
              : tr('settingsPage.relListComplete', { count: p.count })}</span></div>
          <div className="bbn-imp-rr"><span className="h">{tr('settingsPage.relFolderTitle')}</span>
            <span className={'bbn-imp-pill ' + (percorso ? 'is-ok' : 'is-warn')}>{percorso ? tr('settingsPage.pillCorrect') : tr('settingsPage.pillNotDeclared')}</span>
            <span className="b">{percorso ? <><code>{percorso}</code> {tr('settingsPage.relFolderBody')}</> : tr('settingsPage.pathNotDeclared')}</span></div>
        </div>
      </div>
    </div>
  );
}

/* ── 03 Lavori automatici ───────────────────────────────── */
const iconaLavoro = (nome: string) => /backup/i.test(nome) ? HardDrive : /news/i.test(nome) ? Newspaper
  : /consigliere/i.test(nome) ? Users : /briefing/i.test(nome) ? FileText : Clock;

export function SezioneLavori(p: {
  tr: Tr; num: Numero; tasks: Lavoro[] | null; loading: boolean; diagnostica: string | null; nonSupportati: boolean;
  attivi: number; guasti: number; prossimo: Date | null; perGiorno: Record<string, Backup[]>;
  contraddizione: { t: Lavoro; last: Date; lasciati: Backup[] } | null; spenti: Lavoro[]; onBackup: () => void;
}) {
  const { tr, num } = p;
  const nd = tr('settings.nd');
  if (p.tasks === null && p.loading) return <Status>{tr('ui.loading')}</Status>;
  if (p.nonSupportati) return (
    <div className="bbn-imp-empty"><div className="box">
      <span className="big"><Laptop size={28} /></span>
      <h3>{tr('settingsPage.macTitle')}</h3>
      <p>{tr('settingsPage.macBody')}</p>
      <p className="sm">{tr('settingsPage.macHint')} <button type="button" className="bbn-imp-link" onClick={p.onBackup}>{tr('settingsPage.secBackup')}</button>.</p>
      <details><summary>{tr('settingsPage.techDetail')}</summary><code>{p.diagnostica}</code></details>
    </div></div>
  );
  if (p.tasks === null) return (
    <div className="bbn-imp-note is-ko"><AlertCircle size={14} />
      <span><b>{tr('settingsPage.jobsUnreadable')}</b> · {tr('settingsPage.jobsUnreadableBody')}
        {p.diagnostica && <code className="bbn-imp-diag">{p.diagnostica}</code>}</span></div>
  );
  if (p.tasks.length === 0) return (
    <div className="bbn-imp-note is-info"><AlertCircle size={14} />
      <span>{tr('settingsPage.noJobs')} <b>powershell -ExecutionPolicy Bypass -File .\tools\ops\windows\install_all_schedulers.ps1</b> {tr('settingsPage.asAdmin')} {tr('settingsPage.installerWarning')}</span></div>
  );
  return (
    <>
      <div className="bbn-imp-tiles">
        <div className="bbn-imp-tile"><div className="k">{tr('settingsPage.tJobs')}</div><div className="v">{p.tasks.length}</div></div>
        <div className="bbn-imp-tile"><div className="k">{tr('settingsPage.tEnabled')}</div><div className="v">{p.attivi} <small>{tr('settingsPage.ofSmall', { n: p.tasks.length })}</small></div></div>
        <div className="bbn-imp-tile"><div className="k">{tr('settingsPage.tFailed')}</div><div className={'v' + (p.guasti ? ' is-ko' : '')}>{p.guasti}</div></div>
        <div className="bbn-imp-tile"><div className="k">{tr('settingsPage.tNext')}</div><div className="v">{p.prossimo ? <>{ggmm(p.prossimo, nd)} <small>{hhmm(p.prossimo)}</small></> : nd}</div></div>
      </div>
      <div className="bbn-imp-jobs">
        {p.tasks.map((t, i) => {
          const r = t.LastTaskResult;
          const off = spento(t), ok = r === 0;
          const tono = off ? 'off' : ok ? 'ok' : 'ko';
          const last = parseTask(t.LastRunTime), next = parseTask(t.NextRunTime);
          const lasciati = last ? (p.perGiorno[chiave(last)] || []) : [];
          const frase = off ? tr('settingsPage.phraseDisabled', { r: String(r ?? '?') })
            : ok ? tr('settingsPage.phraseOk')
            : lasciati.length ? tr('settingsPage.phraseKoFiles', { r: String(r ?? '?'), n: lasciati.length, mb: num(lasciati.reduce((s, x) => s + x.size_mb, 0), 2) })
            : tr('settingsPage.phraseKoNone', { r: String(r ?? '?') });
          const Icona = iconaLavoro(nomeLavoro(t));
          const c = p.contraddizione && p.contraddizione.t === t ? p.contraddizione : null;
          return (
            <div key={i} className={'bbn-imp-job is-' + tono}>
              <span className="ic"><Icona size={18} /></span>
              <span className="nm">{nomeLavoro(t)}</span>
              <span>{off ? <span className="bbn-imp-pill is-muted"><Power size={12} />{tr('settingsPage.pillDisabled')}</span>
                : <span className={'bbn-imp-pill ' + (ok ? 'is-ok' : 'is-ko')}>{ok ? <Check size={12} /> : <AlertCircle size={12} />}{tr('settingsPage.pillResult', { a: String(r ?? '?') })}</span>}</span>
              <span className="q">{frase}</span>
              <span className="tm">{ggmm(last, nd)} {hhmm(last)}<small>{tr('settingsPage.nextRun', { t: `${ggmm(next, nd)} ${hhmm(next)}`.trim() })}</small></span>
              {c && (
                <div className="bbn-imp-contra">
                  <div><span className="k">{tr('settingsPage.contraClaim')}</span>
                    <b>{tr('settingsPage.contraClaimV', { job: nomeLavoro(c.t), d: ggmm(c.last, nd), r: String(c.t.LastTaskResult) })}</b></div>
                  <div><span className="k">{tr('settingsPage.contraFolder')}</span>
                    <b className="mono">{c.lasciati[0].filename}</b> {tr('settingsPage.contraFolderV')}
                    {c.lasciati.length > 1 ? tr('settingsPage.contraOthers', { a: c.lasciati.length - 1 }) : ''}</div>
                  <div><span className="k is-warn">{tr('settingsPage.contraReading')}</span>{tr('settingsPage.contraReadingV')}</div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      {p.spenti.length > 0 && (
        <div className="bbn-imp-note is-info"><Power size={14} />
          <span>{tr('settingsPage.disabledNote', { names: p.spenti.map(nomeLavoro).join(', ') })}</span></div>
      )}
    </>
  );
}

/* ── 04 Motori e cambi ──────────────────────────────────── */
const iconaMotore = (k: string, g: GruppoMotori) => k === 'chat' || k === 'reflection' ? MessageSquare
  : k === 'briefing' || k === 'news_classifier' ? Newspaper
  : k === 'capo' || k === 'red_team' ? ShieldCheck
  : k === 'action_extractor' || k === 'synthesizer' ? FileText
  : g === 'committee' ? Users : Cpu;

export function SezioneMotori(p: {
  tr: Tr; num: Numero; engines: Record<string, string> | null; loadingEngines: boolean;
  fx: Record<string, number> | null; loadingFx: boolean; nomeValuta: (c: string) => string;
}) {
  const { tr, num } = p;
  const voci = p.engines ? Object.entries(p.engines).filter(([k]) => !/_error$/.test(k)) : [];
  const errori = p.engines ? Object.entries(p.engines).filter(([k]) => /_error$/.test(k)) : [];
  const gruppi: Array<[GruppoMotori, string]> = [['committee', tr('settingsPage.grpCommittee')], ['review', tr('settingsPage.grpReview')],
    ['other', tr('settingsPage.grpOther')], ['unknown', tr('settingsPage.grpUnknown')]];
  return (
    <div className="bbn-imp-eng">
      <div className="bbn-imp-card bbn-imp-eng-card">
        <div className="bbn-imp-card-head"><h3>{tr('settingsPage.enginesTitle')}</h3><span className="bbn-imp-card-note">{tr('settingsPage.enginesNote')}</span></div>
        <div className="bbn-imp-card-body">
          {p.engines === null && p.loadingEngines ? <Status>{tr('ui.loading')}</Status>
            : p.engines === null ? (
              <div className="bbn-imp-note is-ko"><AlertCircle size={14} />
                <span>{tr('settingsPage.enginesField')} <b>engines</b> {tr('settingsPage.enginesAbsent')}</span></div>
            ) : gruppi.map(([g, nome]) => {
              const qui = voci.filter(([k]) => gruppoMotore(k) === g);
              if (!qui.length) return null;
              return (
                <div key={g} className="bbn-imp-eng-grp">
                  <div className="bbn-imp-grp-h">{nome}</div>
                  <div className="bbn-imp-eng-list">
                    {qui.map(([k, v]) => {
                      const Icona = iconaMotore(k, g);
                      return (
                        <div className="bbn-imp-eg" key={k}>
                          <span className="ic"><Icona size={16} /></span>
                          <span className="tx"><b>{engineLabels[k] ? tr(engineLabels[k]) : k.replace(/_/g, ' ')}</b>
                            <span>{String(v).replace('claude-', '').toUpperCase()}</span></span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          {errori.map(([k, v]) => (
            <div className="bbn-imp-note is-ko" key={k}><AlertCircle size={14} />
              <span><b>{k === 'engines_error' ? tr('settingsPage.enginesReadError') : k}</b>: {String(v)}</span></div>
          ))}
        </div>
      </div>
      <div className="bbn-imp-card bbn-imp-fx-card">
        <div className="bbn-imp-card-head"><h3>{tr('settingsPage.fxTitle')}</h3><span className="bbn-imp-card-note">{tr('settingsPage.fxNote')}</span></div>
        <div className="bbn-imp-card-body">
          {p.fx === null && p.loadingFx ? <Status>{tr('ui.loading')}</Status>
            : p.fx === null ? <div className="bbn-imp-note is-ko"><AlertCircle size={14} /><span><b>{tr('settingsPage.fxUnreadable')}</b> · {tr('settingsPage.fxUnreadableBody')}</span></div>
            : Object.keys(p.fx).length === 0 ? <div className="bbn-imp-note is-info"><Globe size={14} /><span>{tr('settingsPage.noFx')}</span></div>
            : Object.entries(p.fx).map(([c, v]) => (
              <div className="bbn-imp-fxr" key={c}>
                <span className="cc">{c}</span>
                <span className="k">1 {c}<small>{p.nomeValuta(c)}</small></span>
                <span className="v">{typeof v === 'number' && isFinite(v) ? num(v, 6) : '—'} <small>EUR</small></span>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}

export const ID_SEZIONE: Record<SezioneId, string> = { generale: '01', backup: '02', lavori: '03', motori: '04' };
