import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { ArrowLeft, ArrowRight, Download, FileSpreadsheet, FileText, Play, RefreshCw, RotateCcw, Search, Square, ShieldAlert } from 'lucide-react';
import ConfirmDialog from '@/components/ConfirmDialog';
import { Bellomberg } from '@/lib/api';
import { leggiNumero } from '@/lib/cassa';
import { localeDi } from '@/i18n/lingua';
import { useLingua, useT } from '@/i18n/provider';
import {
  TradeIdeas, tradeIdeaArtifactBlob, readTradeIdeaStorage, storageOf, type TradeIdeaArtifact, type TradeIdeaDetail,
  type TradeIdeaEmailStatus, type TradeIdeaPreflight, type TradeIdeaRun, type TradeIdeaDocumentSource, type TradeIdeaStorage,
} from '@/lib/tradeIdeas';
import './trade-idea.css';
import TradeIdeaDocumentSources from '@/components/TradeIdeaDocumentSources';
import TradeIdeaRecoveryPanel from '@/components/TradeIdeaRecoveryPanel';
import TradeIdeaCostReconcile from '@/components/TradeIdeaCostReconcile';
import TradeIdeaPdfSections from '@/components/TradeIdeaPdfSections';
import TradeIdeaSourceReadiness, { researchOnly } from '@/components/TradeIdeaSourceReadiness';
import TradeIdeaStorageNotice from '@/components/TradeIdeaStorageNotice';

type RouteState = { viewDraft?: string; noteSaved?: boolean } | null;
type Translation = ReturnType<typeof useT>;

const runId = (run?: TradeIdeaRun | null) => run?.id ?? run?.run_id ?? null;
const runStatus = (run?: TradeIdeaRun | null) => run?.technical_status ?? run?.status ?? null;
const inProgress = (status?: string | null) => ['accepted', 'queued', 'pending', 'starting', 'running', 'stopping'].includes(status || '');
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

function statusLabel(t: Translation, status?: string | null): string {
  switch (status) {
    case 'accepted': case 'queued': case 'pending': return t('tradeidea.statusQueued');
    case 'starting': case 'running': case 'stopping': return t('tradeidea.statusRunning');
    case 'completed': return t('tradeidea.statusCompleted');
    case 'incomplete': return t('tradeidea.statusIncomplete');
    case 'failed': return t('tradeidea.statusFailed');
    case 'cancelled': return t('tradeidea.statusCancelled');
    case 'interrupted': return t('tradeidea.statusInterrupted');
    case 'preflight_failed': return t('tradeidea.statusPreflightFailed');
    default: return status || t('tradeidea.statusUnknown');
  }
}

function emailLabel(t: Translation, status?: TradeIdeaEmailStatus | null): string {
  switch (status) {
    case 'not_attempted': return t('tradeidea.emailNotAttempted');
    case 'preparing': return t('tradeidea.emailPreparing');
    case 'ready': return t('tradeidea.emailReady');
    case 'sending': return t('tradeidea.emailSending');
    case 'accepted': return t('tradeidea.emailAccepted');
    case 'failed': return t('tradeidea.emailFailed');
    case 'uncertain': return t('tradeidea.emailUncertain');
    case 'blocked': return t('tradeidea.emailBlocked');
    default: return t('tradeidea.emailUnknown');
  }
}

function judgmentLabel(t: Translation, judgment?: string | null): string {
  switch (judgment) {
    case 'favorable': return t('tradeidea.judgmentFavorable');
    case 'rejected': return t('tradeidea.judgmentRejected');
    case 'watch': return t('tradeidea.judgmentWatch');
    case 'incomplete': return t('tradeidea.judgmentIncomplete');
    default: return t('tradeidea.judgmentMissing');
  }
}

/** R14 seguito (B6): controverifica fonti di una run SALVATA. `unknown_legacy` = run anteriore al marcatore:
 *  dato storico non registrato, mai «completata» né «fallita». Valore assente = non dichiarato. */
export function savedSourceCheckLabel(t: Translation, status?: string | null): string {
  switch (status) {
    case 'completed': return t('tradeidea.sourceCheckSavedCompleted');
    case 'unknown_legacy': return t('tradeidea.sourceCheckSavedLegacy');
    case null: case undefined: case '': return t('tradeidea.sourceCheckSavedUnknown');
    default: return status;
  }
}

function dateLabel(value: string | null | undefined, locale: string, missing: string): string {
  if (!value) return missing;
  const parsed = new Date(value);
  return isNaN(parsed.getTime()) ? value : parsed.toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' });
}

function tokenPrice(value: string | undefined, locale: string, unknown: string): string {
  const perToken = value == null ? NaN : Number(value);
  return Number.isFinite(perToken) && perToken >= 0
    ? new Intl.NumberFormat(locale, { maximumFractionDigits: 6 }).format(perToken * 1_000_000)
    : unknown;
}

function duration(start: string | null | undefined, finish: string | null | undefined, now: number, missing: string): string {
  if (!start) return missing;
  const from = new Date(start).getTime();
  const to = finish ? new Date(finish).getTime() : now;
  if (!isFinite(from) || !isFinite(to) || to < from) return missing;
  const s = Math.floor((to - from) / 1000);
  return `${Math.floor(s / 3600).toString().padStart(2, '0')}:${Math.floor(s % 3600 / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`;
}

function TextList({ title, values }: { title: string; values?: string[] }) {
  if (!values?.length) return null;
  return <section className="ti-list-block"><h4>{title}</h4><ul>{values.map((value, index) => <li key={`${index}-${value}`}>{value}</li>)}</ul></section>;
}

function markdown(value: string) {
  return <div className="ti-markdown"><ReactMarkdown>{value}</ReactMarkdown></div>;
}

export function TradeIdeaRunDetail({ detail, now, onStop, stopping, stopError, onRetryEmail, retryingEmail, retryError, onReuse, onDownload, downloadError, downloading, stopStorage = null, retryStorage = null }: {
  detail: TradeIdeaDetail; now: number; onStop: () => void; stopping: boolean; stopError: string | null;
  onRetryEmail: () => void; retryingEmail: boolean; retryError: string | null; onReuse: () => void;
  onDownload: (artifact: TradeIdeaArtifact) => void; downloadError: string | null; downloading: string | null;
  /** R14: diagnosi del 503 storage di stop / nuovo invio email, mostrata col riquadro invece del detail grezzo */
  stopStorage?: TradeIdeaStorage | null; retryStorage?: TradeIdeaStorage | null;
}) {
  const t = useT(), language = useLingua(), locale = localeDi(language);
  const { run, progress, result } = detail;
  const status = runStatus(run), id = runId(run);
  const destination = run.destination ?? result?.destination;
  const destinationKind = destination?.kind ?? 'none';
  const invalidation = Array.isArray(result?.invalidation) ? result.invalidation : result?.invalidation ? [result.invalidation] : [];
  const email = detail.email;
  const reports = result?.reports ?? progress?.reports;
  const redTeam = result?.red_team ?? progress?.red_team;
  return <div className="ti-detail" aria-live="polite">
    <div className="ti-section-title"><div><span className="ti-overline">{t('tradeidea.detail')}</span><h2>{run.company_name || run.identity?.name || run.ticker}</h2></div><span className={`ti-chip ti-chip-${status || 'unknown'}`}>{statusLabel(t, status)}</span></div>
    {run.language && <p className="ti-muted">{t('tradeidea.originalLanguage', { language: run.language.toUpperCase() })}</p>}
    <div className="ti-run-meta">
      <div><span>{t('tradeidea.runId')}</span><strong>{id || t('tradeidea.unknown')}</strong></div>
      <div><span>{t('tradeidea.started')}</span><strong>{dateLabel(run.started_at ?? run.created_at, locale, t('tradeidea.unknown'))}</strong></div>
      <div><span>{t('tradeidea.elapsed')}</span><strong>{duration(run.started_at, run.finished_at, now, t('tradeidea.unknown'))}</strong></div>
      <div><span>{t('tradeidea.phase')}</span><strong>{progress?.phase || run.phase || t('tradeidea.unknown')}</strong></div>
      {run.source_qualification && <div data-ti-source-check={run.source_qualification.execution_status || 'undeclared'}><span>{t('tradeidea.sourceCheckSaved')}</span><strong>{savedSourceCheckLabel(t, run.source_qualification.execution_status)}</strong></div>}
    </div>
    {(run.error || run.reason) && <div className="ti-notice ti-error" role="alert">{run.error || run.reason}</div>}
    <div className="ti-detail-actions">
      {inProgress(status) && <button className="ti-button ti-danger" onClick={onStop} disabled={stopping}><Square size={12} />{stopping ? t('tradeidea.stopping') : t('tradeidea.stop')}</button>}
      <button className="ti-button ti-quiet" onClick={onReuse}><RotateCcw size={12} />{t('tradeidea.reuse')}</button>
    </div>
    {stopError && (stopStorage ? <TradeIdeaStorageNotice storage={stopStorage} lead={t('tradeidea.stopError', { error: t('tradeidea.storageLead') })} />
      : <div className="ti-notice ti-error" role="alert">{t('tradeidea.stopError', { error: stopError })}</div>)}

    <section className="ti-slab">
      <div className="ti-slab-head"><h3>{t('tradeidea.progress')}</h3><span>{dateLabel(progress?.updated_at ?? run.updated_at, locale, t('tradeidea.unknown'))}</span></div>
      {!progress?.specialists?.length && !progress?.tools?.length && !progress?.events?.length && <p className="ti-muted">{t('tradeidea.noProgress')}</p>}
      {!!progress?.specialists?.length && <div className="ti-progress-grid">{progress.specialists.map((entry, index) => <div className="ti-progress-cell" key={`${entry.id || entry.name || entry.role}-${index}`}><strong>{entry.name || entry.role || entry.id || t('tradeidea.unknown')}</strong><span>{entry.round != null ? `${entry.round} · ` : ''}{statusLabel(t, entry.status)}</span>{entry.detail && <small>{entry.detail}</small>}</div>)}</div>}
      {!!progress?.tools?.length && <div className="ti-tools"><h4>{t('tradeidea.tools')}</h4>{progress.tools.map((entry, index) => <div key={`${entry.name}-${index}`}><b>{entry.name}</b><span>{entry.specialist || t('tradeidea.unknown')}{entry.count != null ? ` · ${entry.count}` : ''}{entry.status ? ` · ${entry.status}` : ''}</span></div>)}</div>}
      {!!progress?.events?.length && <div className="ti-events"><h4>{t('tradeidea.events')}</h4>{progress.events.map((entry, index) => <p key={index}><time>{dateLabel(entry.at, locale, '')}</time><span>{entry.message}</span></p>)}</div>}
      <div className="ti-usage"><span>{t('tradeidea.usage')}</span>{run.usage?.cost_usd != null ? <strong>{new Intl.NumberFormat(locale, { style: 'currency', currency: 'USD' }).format(run.usage.cost_usd)}{run.usage.partial ? ` · ${t('tradeidea.usagePartial')}` : ''}</strong> : run.usage?.cost_eur != null ? <strong>{new Intl.NumberFormat(locale, { style: 'currency', currency: 'EUR' }).format(run.usage.cost_eur)}{run.usage.partial ? ` · ${t('tradeidea.usagePartial')}` : ''}</strong> : <strong>{t('tradeidea.usageUnknown')}</strong>}{run.usage?.reason && <small>{run.usage.reason}</small>}</div>
    </section>

    <section className={`ti-verdict ti-verdict-${result?.judgment || 'unknown'}`}>
      <div className="ti-slab-head"><h3>{t('tradeidea.judgment')}</h3><span>{judgmentLabel(t, result?.judgment)}</span></div>
      {result?.summary ? markdown(result.summary) : <p className="ti-muted">{t('tradeidea.noResult')}</p>}
      {run.view_text && <div className="ti-view-response"><h4>{t('tradeidea.view')}</h4><p>{run.view_text}</p><h4>{t('tradeidea.pmResponse')}</h4>{result?.pm_view_response ? markdown(result.pm_view_response) : <p className="ti-muted">{t('tradeidea.unknown')}</p>}</div>}
      {result?.proposal && <div className="ti-proposal"><h4>{t('tradeidea.proposal')}</h4><strong>{result.proposal.action} · {result.proposal.ticker}</strong>{result.proposal.eur_amount != null && <span>{new Intl.NumberFormat(locale, { style: 'currency', currency: 'EUR' }).format(result.proposal.eur_amount)}</span>}<p>{result.proposal.rationale}</p><small>{result.proposal.timing} · {result.proposal.confidence}{result.proposal.sizing_source ? ` · ${result.proposal.sizing_source}` : ''}</small></div>}
      <div className="ti-destination"><span>{t('tradeidea.destination')}</span><strong>{destinationKind === 'dcn' ? t('tradeidea.destinationDcn') : destinationKind === 'research' ? t('tradeidea.destinationResearch') : t('tradeidea.destinationNone')}</strong>{destination?.reason && <p>{destination.reason}</p>}{destinationKind === 'none' && !destination?.reason && <p>{t('tradeidea.destinationReasonMissing')}</p>}{destinationKind !== 'none' && destination?.decision_id != null && <Link to={`/decisions?decision=${encodeURIComponent(String(destination.decision_id))}`}>{t('tradeidea.openDecision')} <ArrowRight size={12} /></Link>}</div>
    </section>

    {result && <section className="ti-slab ti-analysis">
      <div className="ti-slab-head"><h3>{t('tradeidea.scenarios')}</h3></div>
      <div className="ti-analysis-grid"><TextList title={t('tradeidea.pros')} values={result.pros} /><TextList title={t('tradeidea.cons')} values={result.cons} /><TextList title={t('tradeidea.risks')} values={result.risks} /><TextList title={t('tradeidea.catalysts')} values={result.catalysts} /><TextList title={t('tradeidea.invalidation')} values={invalidation} /><TextList title={t('tradeidea.dataGaps')} values={result.data_gaps} /><TextList title={t('tradeidea.reviewConditions')} values={result.review_conditions} /></div>
      {!!result.scenarios?.length && <div className="ti-scenario-grid">{result.scenarios.map((scenario, index) => <article key={`${scenario.name}-${index}`}><h4>{scenario.name}</h4><p>{scenario.analysis}</p>{!!scenario.evidence_ids?.length && <small>{t('tradeidea.evidenceIds')}: {scenario.evidence_ids.join(', ')}</small>}</article>)}</div>}
      {result.valuation && !researchOnly(run) && <div className="ti-valuation"><h4>{t('tradeidea.valuation')}</h4><p>{[result.valuation.method, result.valuation.status].filter(Boolean).join(' · ') || t('tradeidea.unknown')}</p>{result.valuation.summary && markdown(result.valuation.summary)}{result.valuation.reason && <p>{result.valuation.reason}</p>}</div>}
    </section>}

    {!!result?.objections?.length && <section className="ti-slab"><div className="ti-slab-head"><h3>{t('tradeidea.objections')}</h3></div>{result.objections.map((item, index) => <article className="ti-objection" key={index}><span>{item.resolved ? t('tradeidea.resolved') : t('tradeidea.unresolved')}</span><p>{item.objection}</p><blockquote>{item.response}</blockquote>{!!item.evidence_ids?.length && <small>{t('tradeidea.evidenceIds')}: {item.evidence_ids.join(', ')}</small>}</article>)}</section>}
    {!!result?.history_review?.length && <section className="ti-slab"><div className="ti-slab-head"><h3>{t('tradeidea.historyReview')}</h3></div>{result.history_review.map((item, index) => <div className="ti-history-review" key={`${item.kind}-${item.id}-${index}`}><strong>{item.kind} #{item.id}</strong><p>{item.response}</p></div>)}</section>}
    {!!result?.dossier?.length && <section className="ti-slab"><div className="ti-slab-head"><h3>{t('tradeidea.dossier')}</h3></div>{result.dossier.map(section => <details className="ti-report ti-dossier-section" key={section.key}><summary>{section.title}</summary>{section.paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}{section.tables?.map((table, index) => <div className="ti-table-wrap" key={index}><h4>{table.title}</h4><small>{table.unit} · {table.period} · {table.source}</small><table><thead><tr>{table.columns.map((column, cell) => <th key={cell}>{column}</th>)}</tr></thead><tbody>{table.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, cell) => <td key={cell}>{value}</td>)}</tr>)}</tbody></table></div>)}{!!section.evidence_ids?.length && <small>{t('tradeidea.evidenceIds')}: {section.evidence_ids.join(', ')}</small>}</details>)}</section>}
    {!!result?.evidence?.length && <section className="ti-slab"><div className="ti-slab-head"><h3>{t('tradeidea.evidence')}</h3></div>{result.evidence.map(item => <div className="ti-evidence" key={item.id}><strong>{item.id} · {item.source}</strong><span>{item.as_of}</span><p>{item.summary}</p>{item.url && <a href={item.url} target="_blank" rel="noreferrer">{item.url}</a>}</div>)}</section>}

    {(reports?.length || redTeam || result?.memo) && <section className="ti-slab"><div className="ti-slab-head"><h3>{t('tradeidea.reports')}</h3></div>{reports?.map((report, index) => <details className="ti-report" key={`${report.specialist}-${index}`}><summary>{report.specialist}{report.round != null ? ` · ${report.round}` : ''}{report.status ? ` · ${report.status}` : ''}</summary>{report.text ? markdown(report.text) : <p className="ti-muted">{t('tradeidea.unknown')}</p>}</details>)}{redTeam && <details className="ti-report ti-red-team" open><summary><ShieldAlert size={14} />{t('tradeidea.redTeam')} · {redTeam.status || t('tradeidea.unknown')}</summary>{redTeam.text && markdown(redTeam.text)}<TextList title={t('tradeidea.cons')} values={redTeam.objections} /><TextList title={t('tradeidea.dataGaps')} values={redTeam.unresolved} />{redTeam.reason && <p>{redTeam.reason}</p>}</details>}{result?.memo && <details className="ti-report"><summary>{t('tradeidea.memo')}</summary>{markdown(result.memo)}</details>}</section>}

    <div className="ti-delivery-grid">
      <section className="ti-slab"><div className="ti-slab-head"><h3>{t('tradeidea.artifacts')}</h3></div>{!detail.artifacts?.length ? <p className="ti-muted">{t('tradeidea.artifactsEmpty')}</p> : detail.artifacts.map(artifact => <div className="ti-artifact" key={artifact.id}>{artifact.kind === 'pdf' ? <FileText size={17} /> : <FileSpreadsheet size={17} />}<div><strong>{artifact.name}</strong><span>{artifact.kind.toUpperCase()} · {artifact.status === 'partial' ? t('tradeidea.partialArtifact') : artifact.status}</span>{artifact.reason && <small>{artifact.reason}</small>}{artifact.status !== 'ready' && artifact.status !== 'partial' && !artifact.reason && <small>{t('tradeidea.artifactMissingReason')}</small>}</div>{(artifact.status === 'ready' || (artifact.kind === 'pdf' && artifact.status === 'partial')) && <button className="ti-button ti-quiet" disabled={downloading === artifact.id} onClick={() => onDownload(artifact)}><Download size={12} />{t('tradeidea.download')}</button>}</div>)}{downloadError && <div className="ti-notice ti-error" role="alert">{t('tradeidea.downloadFailed', { error: downloadError })}</div>}<TradeIdeaPdfSections detail={detail} /></section>
      <section className="ti-slab"><div className="ti-slab-head"><h3>{t('tradeidea.email')}</h3></div><p className={`ti-email ti-email-${email?.status || 'unknown'}`}>{emailLabel(t, email?.status)}</p>{(email?.error || email?.reason) && <p className="ti-muted">{email.error || email.reason}</p>}{email?.attempted_at && <p className="ti-muted">{dateLabel(email.attempted_at, locale, t('tradeidea.unknown'))}</p>}{(email?.status === 'failed' || email?.status === 'uncertain') && <button className="ti-button ti-primary" onClick={onRetryEmail} disabled={retryingEmail}>{retryingEmail ? t('tradeidea.retryingEmail') : t(email.status === 'uncertain' ? 'tradeidea.retryUncertain' : 'tradeidea.retryEmail')}</button>}{retryError && (retryStorage ? <TradeIdeaStorageNotice storage={retryStorage} lead={t('tradeidea.retryFailed', { error: t('tradeidea.storageLead') })} />
        : <div className="ti-notice ti-error" role="alert">{t('tradeidea.retryFailed', { error: retryError })}</div>)}</section>
    </div>
    {id && !inProgress(status) && !researchOnly(run) && <section className="ti-slab">
      <h3>{t('tradeidea.recoveryArchived')}</h3><p>{t('tradeidea.recoveryArchivedHint')}</p>
      <Link to="/fundamentals">{t('tradeidea.openFundArchive')} <ArrowRight size={12}/></Link>
    </section>}
  </div>;
}

export default function TradeIdeaPage() {
  const t = useT(), language = useLingua(), locale = localeDi(language);
  const navigate = useNavigate(), location = useLocation(), [searchParams, setSearchParams] = useSearchParams();
  const routeState = location.state as RouteState;
  const initialTicker = searchParams.get('ticker')?.trim() || '';
  const entry = searchParams.get('source') || 'manual';
  const [ticker, setTicker] = useState(initialTicker);
  const [view, setView] = useState(routeState?.viewDraft ?? '');
  const [documentSources, setDocumentSources] = useState<TradeIdeaDocumentSource[]>([]);
  const [viewSource, setViewSource] = useState(entry === 'favorites' ? routeState?.noteSaved === false ? 'manual' : 'favorite_note' : entry === 'market' ? 'market' : 'manual');
  const [sourceHint, setSourceHint] = useState(entry === 'favorites' ? routeState?.noteSaved === false ? 'draft' : 'favorite' : entry);
  const [favoriteError, setFavoriteError] = useState<string | null>(null);
  const viewTouched = useRef(routeState?.viewDraft !== undefined);
  const [budgetText, setBudgetText] = useState('');
  const [budgetLanguage, setBudgetLanguage] = useState(language);
  const budget = useMemo(() => leggiNumero(budgetText, budgetLanguage, language), [budgetText, budgetLanguage, language]);
  const budgetValue = budget?.ok ? budget.valore : null;
  const [preflight, setPreflight] = useState<TradeIdeaPreflight | null>(null);
  const [preflightSignature, setPreflightSignature] = useState('');
  const [preflightError, setPreflightError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [identityConfirmed, setIdentityConfirmed] = useState(false);
  const [askLaunch, setAskLaunch] = useState(false);
  const [launching, setLaunching] = useState(false);
  const launchLock = useRef(false);
  const idempotencyKey = useRef<string | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get('run'));
  const [detail, setDetail] = useState<TradeIdeaDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [history, setHistory] = useState<TradeIdeaRun[]>([]);
  const [historyTotal, setHistoryTotal] = useState<number | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  // R14: diagnosi strutturata del 503 (storage non pronto) accanto al messaggio, per storico/dettaglio/preflight
  const [historyStorage, setHistoryStorage] = useState<TradeIdeaStorage | null>(null);
  const [detailStorage, setDetailStorage] = useState<TradeIdeaStorage | null>(null);
  const [preflightStorage, setPreflightStorage] = useState<TradeIdeaStorage | null>(null);
  // R14/U4: lo stato «run attiva» non letto si dichiara n.d., mai «nessuna run attiva»
  const [activeError, setActiveError] = useState<{ text: string; storage: TradeIdeaStorage | null } | null>(null);
  const [launchStorage, setLaunchStorage] = useState<TradeIdeaStorage | null>(null);
  const [stopStorage, setStopStorage] = useState<TradeIdeaStorage | null>(null);
  const [retryStorage, setRetryStorage] = useState<TradeIdeaStorage | null>(null);
  const [historyTicker, setHistoryTicker] = useState('');
  const [historyStatus, setHistoryStatus] = useState('');
  const [historyLimit, setHistoryLimit] = useState(20);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [refreshing, setRefreshing] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const [retryingEmail, setRetryingEmail] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [askStop, setAskStop] = useState(false);
  const [askUncertainEmail, setAskUncertainEmail] = useState(false);
  // R14 seguito: rilettura manuale di /active quando lo stato della run attiva non è verificabile (hook in coda)
  const [recheckingActive, setRecheckingActive] = useState(false);

  const signature = JSON.stringify([ticker.trim(), view, viewSource, budgetValue, documentSources]);
  const documentsComplete = documentSources.every(source => /^https:\/\//i.test(source.url));
  const preflightCurrent = preflight !== null && preflightSignature === signature;
  const resolved = preflight?.identity?.ticker || preflight?.ticker;
  const changedIdentity = !!resolved && resolved.toUpperCase() !== ticker.trim().toUpperCase();
  const prices = preflight?.budget?.model_prices;
  const catalog = preflight?.catalog_snapshot;
  const observedQuote = preflight?.source_qualification?.coverage?.sources?.current_quotation;
  const historicalPreparation = preflight?.source_qualification?.status === 'preparation_required';
  const researchRequired = preflight?.source_qualification?.status === 'research_required';
  const researchMode = researchOnly(preflight) || researchOnly(preflight?.source_qualification);
  const preflightReadyStorage = readTradeIdeaStorage(preflight?.storage);
  const canLaunch = !!(preflightCurrent && preflight?.ok && (preflight.source_qualification?.status === 'qualified' || historicalPreparation || researchRequired) && preflight.source_qualification?.fingerprint && preflight.identity?.ticker && preflight.models?.length && budgetValue != null && (!changedIdentity || identityConfirmed) && !launching && !activeId && !activeError);

  useEffect(() => {
    if (entry !== 'favorites' || !initialTicker || routeState?.viewDraft !== undefined) return;
    let alive = true;
    Bellomberg.favorites().then(data => {
      if (!alive || viewTouched.current) return;
      const favorite = data.favorites.find(item => item.ticker === initialTicker);
      if (favorite?.note) { setView(favorite.note); setViewSource('favorite_note'); setSourceHint('favorite'); }
    }).catch(error => { if (alive) setFavoriteError(errorText(error)); });
    return () => { alive = false; };
  }, []);

  const loadHistory = useCallback(async (filterTicker: string, filterStatus: string, limit: number) => {
    setLoadingHistory(true);
    try {
      const data = await TradeIdeas.list({ ticker: filterTicker.trim() || undefined, status: filterStatus || undefined, limit });
      setHistory(data.runs); setHistoryTotal(data.total ?? null); setHistoryError(null); setHistoryStorage(null);
    } catch (error) { setHistoryError(errorText(error)); setHistoryStorage(storageOf(error)); }
    finally { setLoadingHistory(false); }
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    try { const data = await TradeIdeas.detail(id); setDetail(data); setDetailError(null); setDetailStorage(null); }
    catch (error) { setDetailError(errorText(error)); setDetailStorage(storageOf(error)); }
  }, []);

  const loadActive = useCallback(async () => {
    try { const active = await TradeIdeas.active(); setActiveId(active.run_id || null); setActiveError(null); }
    catch (error) { setActiveError({ text: errorText(error), storage: storageOf(error) }); }
  }, []);

  useEffect(() => {
    let alive = true;
    Promise.allSettled([TradeIdeas.active(), TradeIdeas.list({ limit: historyLimit })]).then(results => {
      if (!alive) return;
      const active = results[0].status === 'fulfilled' ? results[0].value.run_id : null;
      if (results[0].status === 'fulfilled') { setActiveId(active || null); setActiveError(null); }
      else setActiveError({ text: errorText(results[0].reason), storage: storageOf(results[0].reason) });
      const listed = results[1].status === 'fulfilled' ? results[1].value : null;
      if (listed) { setHistory(listed.runs); setHistoryTotal(listed.total ?? null); }
      else if (results[1].status === 'rejected') { setHistoryError(errorText(results[1].reason)); setHistoryStorage(storageOf(results[1].reason)); }
      if (!selectedId) setSelectedId(active || (listed?.runs[0] ? runId(listed.runs[0]) : null));
    });
    return () => { alive = false; };
  }, []);

  useEffect(() => { if (selectedId) void loadDetail(selectedId); }, [selectedId, loadDetail]);
  useEffect(() => {
    if (!selectedId) return;
    let busy = false;
    const timer = setInterval(async () => {
      if (busy) return;
      busy = true;
      try {
        // dettaglio e run attiva falliscono ciascuno per conto suo: l'errore dell'uno non si attribuisce all'altro
        const [fresh, active] = await Promise.allSettled([TradeIdeas.detail(selectedId), TradeIdeas.active()]);
        if (fresh.status === 'fulfilled') { setDetail(fresh.value); setDetailError(null); setDetailStorage(null); }
        else { setDetailError(errorText(fresh.reason)); setDetailStorage(storageOf(fresh.reason)); }
        if (active.status === 'fulfilled') { setActiveId(active.value.run_id || null); setActiveError(null); }
        else setActiveError({ text: errorText(active.reason), storage: storageOf(active.reason) });
      } finally { busy = false; }
    }, 5000);
    return () => clearInterval(timer);
  }, [selectedId]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);

  const openRun = (id: string) => {
    setSelectedId(id); setDetail(null); setDetailError(null); setStopError(null); setRetryError(null); setDownloadError(null);
    const next = new URLSearchParams(searchParams); next.set('run', id); setSearchParams(next);
    requestAnimationFrame(() => document.querySelector('.ti-history-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  };

  const recheckActive = async () => {
    setRecheckingActive(true);
    try { await loadActive(); } finally { setRecheckingActive(false); }
  };

  const invalidatePreflight = () => { setPreflight(null); setPreflightError(null); setIdentityConfirmed(false); idempotencyKey.current = null; };
  const verify = async () => {
    if (!ticker.trim() || budgetValue == null || !documentsComplete) return;
    setVerifying(true); setPreflightError(null); setIdentityConfirmed(false);
    const checkedSignature = signature;
    try {
      const answer = await TradeIdeas.preflight(ticker.trim(), view, viewSource, budgetValue, undefined, documentSources);
      setPreflight(answer); setPreflightSignature(checkedSignature); setPreflightStorage(null);
    } catch (error) { setPreflight(null); setPreflightError(errorText(error)); setPreflightStorage(storageOf(error)); }
    finally { setVerifying(false); }
  };

  const start = async () => {
    if (launchLock.current || !canLaunch || budgetValue == null) return;
    launchLock.current = true; setAskLaunch(false); setLaunching(true); setLaunchError(null); setLaunchStorage(null);
    const key = idempotencyKey.current || crypto.randomUUID();
    idempotencyKey.current = key;
    try {
      const accepted = await TradeIdeas.start(ticker.trim(), view, viewSource, budgetValue, key, preflight!.source_qualification!.fingerprint!, documentSources);
      setActiveId(accepted.run_id); openRun(accepted.run_id);
      await loadHistory('', '', historyLimit);
    } catch (error) {
      setLaunchError(errorText(error)); setLaunchStorage(storageOf(error));
      try { const active = await TradeIdeas.active(); if (active.run_id) { setActiveId(active.run_id); openRun(active.run_id); } } catch { /* avvio ambiguo dichiarato sopra */ }
    } finally { launchLock.current = false; setLaunching(false); }
  };

  const refresh = async () => {
    setRefreshing(true);
    try { await Promise.all([selectedId ? loadDetail(selectedId) : Promise.resolve(), loadHistory(historyTicker, historyStatus, historyLimit), loadActive()]); }
    finally { setRefreshing(false); }
  };

  const stop = async () => {
    if (!selectedId || stopping) return;
    setAskStop(false); setStopping(true); setStopError(null); setStopStorage(null);
    try { await TradeIdeas.stop(selectedId); await Promise.all([loadDetail(selectedId), loadActive()]); }
    catch (error) { setStopError(errorText(error)); setStopStorage(storageOf(error)); }
    finally { setStopping(false); }
  };

  const retryEmail = async (acknowledgeUncertain = false) => {
    if (!selectedId || retryingEmail || !(detail?.email?.status === 'failed' || (detail?.email?.status === 'uncertain' && acknowledgeUncertain))) return;
    setAskUncertainEmail(false);
    setRetryingEmail(true); setRetryError(null); setRetryStorage(null);
    try { await TradeIdeas.retryEmail(selectedId, acknowledgeUncertain); await loadDetail(selectedId); }
    catch (error) { setRetryError(errorText(error)); setRetryStorage(storageOf(error)); }
    finally { setRetryingEmail(false); }
  };

  const download = async (artifact: TradeIdeaArtifact) => {
    if (!selectedId || !(artifact.status === 'ready' || (artifact.kind === 'pdf' && artifact.status === 'partial')) || downloading) return;
    setDownloading(artifact.id); setDownloadError(null);
    try {
      const blob = await tradeIdeaArtifactBlob(selectedId, artifact.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url; anchor.download = artifact.name.replace(/[\\/]/g, '_');
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { setDownloadError(errorText(error)); }
    finally { setDownloading(null); }
  };

  const reuse = () => {
    if (!detail) return;
    setTicker(detail.run.ticker); setView(detail.run.view_text ?? detail.run.pm_view ?? '');
    setViewSource('reused_run'); setSourceHint('reused'); viewTouched.current = true;
    setBudgetText(''); setDocumentSources([]); invalidatePreflight();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const originLabel = sourceHint === 'favorite' ? t('tradeidea.viewOriginFavorite') : sourceHint === 'draft' ? t('tradeidea.viewOriginDraft') : sourceHint === 'reused' ? t('tradeidea.viewOriginReused') : sourceHint === 'market' ? t('tradeidea.viewOriginMarket') : t('tradeidea.viewOriginManual');
  const budgetInvalid = budgetText.trim() && budget && !budget.ok;
  return <div className="ti-page animate-fadeIn">
    <header className="ti-hero"><div><button className="ti-back" onClick={() => navigate('/agents')}><ArrowLeft size={12} />{t('tradeidea.back')}</button><h1>{t('tradeidea.title')}</h1><p>{t('tradeidea.subtitle')}</p></div><div className="ti-hero-mark" aria-hidden="true"><span>TI</span><small>{t('tradeidea.markResearch')}</small></div></header>
    <div className="ti-main-grid">
      <section className="ti-slab ti-form" aria-labelledby="ti-target-title"><div className="ti-slab-head"><h2 id="ti-target-title">{t('tradeidea.target')}</h2><span>{t('tradeidea.markInput')}</span></div>
        <label htmlFor="ti-ticker">{t('tradeidea.ticker')}</label><div className="ti-ticker-row"><Search size={15} /><input id="ti-ticker" value={ticker} onChange={event => { setTicker(event.target.value.toUpperCase()); setDocumentSources([]); setView(''); setViewSource('manual'); setSourceHint('manual'); viewTouched.current = true; invalidatePreflight(); }} placeholder={t('tradeidea.tickerHint')} maxLength={32} autoComplete="off" spellCheck={false} /></div>
        <div className="ti-identity">{preflightCurrent && preflight?.identity ? <><strong>{preflight.identity.ticker}</strong><span>{preflight.identity.name || t('tradeidea.unknown')}{preflight.identity.exchange ? ` · ${preflight.identity.exchange}` : ''}{preflight.identity.currency ? ` · ${preflight.identity.currency}` : ''}</span><small>{t('tradeidea.identityStatus')}: {preflight.identity.status || t('tradeidea.unknown')}{preflight.identity.reason ? ` · ${preflight.identity.reason}` : ''}</small></> : <span>{t('tradeidea.identityUnknown')}</span>}</div>
        {changedIdentity && preflightCurrent && <label className="ti-check"><input type="checkbox" checked={identityConfirmed} onChange={event => setIdentityConfirmed(event.target.checked)} />{t('tradeidea.confirmIdentity', { original: ticker, resolved: resolved || '' })}</label>}
        <label htmlFor="ti-view">{t('tradeidea.view')}</label><p className="ti-field-note">{originLabel}</p><textarea id="ti-view" value={view} onChange={event => { setView(event.target.value); viewTouched.current = true; invalidatePreflight(); }} placeholder={t('tradeidea.viewHint')} rows={7} maxLength={20000} /><p className="ti-field-note">{t('tradeidea.viewRole')} {t('tradeidea.viewOptional')}</p>
        {favoriteError && <div className="ti-notice ti-error" role="alert">{t('tradeidea.favoriteLoadError', { error: favoriteError })}</div>}
        <label htmlFor="ti-budget">{t('tradeidea.budgetLimit')}</label><div className="ti-budget-row"><span>USD</span><input id="ti-budget" type="text" inputMode="decimal" value={budgetText} onChange={event => { setBudgetText(event.target.value); setBudgetLanguage(language); invalidatePreflight(); }} aria-invalid={!!budgetInvalid} aria-describedby="ti-budget-help" placeholder={language === 'it' ? 'es. 25,00' : 'e.g. 25.00'} /></div><p className="ti-field-note" id="ti-budget-help">{budgetInvalid ? t('tradeidea.budgetLimitError', { error: budget.motivo }) : t('tradeidea.budgetLimitHint')}</p>
        <TradeIdeaDocumentSources value={documentSources} disabled={launching} onChange={sources => {setDocumentSources(sources); invalidatePreflight();}} />
        {!documentsComplete && <p className="ti-field-note">{t('tradeidea.sourcesIncomplete')}</p>}
        <button className="ti-button ti-primary ti-verify" disabled={!ticker.trim() || budgetValue == null || !documentsComplete || verifying || launching} onClick={() => void verify()}><Search size={13} />{verifying ? t('tradeidea.verifying') : t('tradeidea.verify')}</button>
        {preflightError && (preflightStorage ? <TradeIdeaStorageNotice storage={preflightStorage} lead={t('tradeidea.verifyFailed', { error: t('tradeidea.storageLead') })} />
          : <div className="ti-notice ti-error" role="alert">{t('tradeidea.verifyFailed', { error: preflightError })}</div>)}
      </section>
      <section className="ti-slab ti-readiness" aria-labelledby="ti-ready-title"><div className="ti-slab-head"><h2 id="ti-ready-title">{t('tradeidea.readiness')}</h2><span>{t('tradeidea.markPreflight')}</span></div>
        {!preflightCurrent ? <p className="ti-muted">{preflight ? t('tradeidea.preflightStale') : t('tradeidea.identityUnknown')}</p> : <><div className={`ti-readiness-status ${preflight?.ok ? 'ok' : 'ko'}`}>{preflight?.ok ? t('tradeidea.preflightOk') : t('tradeidea.preflightBlocked')}</div>{preflightReadyStorage && <TradeIdeaStorageNotice storage={preflightReadyStorage} lead={t('tradeidea.storageLead')} />}{preflight?.reasons?.length ? <ul className="ti-reasons">{preflight.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : !preflight?.ok && <p className="ti-muted">{t('tradeidea.preflightNoReason')}</p>}
          <div className="ti-gates"><div><span>{t('tradeidea.authorization')}</span><strong>{preflight?.authorization?.status || t('tradeidea.gateUnknown')}</strong><small>{preflight?.authorization?.reason}</small></div><div><span>{t('tradeidea.budget')}</span><strong>{preflight?.budget?.status || t('tradeidea.gateUnknown')}</strong><small>{preflight?.budget?.reason}</small></div></div>
          <TradeIdeaSourceReadiness preflight={preflight}/>
          {preflight?.source_qualification?.coverage?.sources?.catalog_warnings?.map((warning, index) => <p className="ti-field-note" key={`catalog-warning-${index}`}>{warning.source === 'catalog' && warning.reason === 'document limit reached; coverage partial' ? t('tradeidea.historicalCatalogLimited') : warning.reason}</p>)}
          {observedQuote && <div className="ti-catalog ti-observed-quote">
            <h3>{t('tradeidea.observedQuote')}</h3>
            <p>{observedQuote.status === 'verified_observed_price' && typeof observedQuote.price === 'number' && Number.isFinite(observedQuote.price) && observedQuote.price > 0
              ? `${new Intl.NumberFormat(locale, { maximumFractionDigits: 6 }).format(observedQuote.price)} ${observedQuote.currency || t('tradeidea.unknown')}`
              : t(researchMode ? 'tradeidea.companyResearchQuoteMissing' : 'tradeidea.observedQuoteBlocked')}</p>
            <small>{t('tradeidea.observedQuoteAt')}: {dateLabel(observedQuote.observed_at, locale, t('tradeidea.unknown'))}</small>
            <small>{t('tradeidea.observedQuoteSource')}: {observedQuote.quote_source_name || observedQuote.source_id || t('tradeidea.unknown')}{observedQuote.exchange ? ` · ${observedQuote.exchange}` : ''}</small>
            <small>{t('tradeidea.observedQuoteAcquired')}: {observedQuote.acquired_as_of || t('tradeidea.unknown')} · {t('tradeidea.observedQuoteCutoff')}: {observedQuote.information_cutoff || t('tradeidea.unknown')}</small>
            <p className="ti-field-note">{t(researchMode ? 'tradeidea.companyResearchQuoteLimit' : 'tradeidea.observedQuoteLimit')}</p>
            {observedQuote.freshness_policy === 'previous_weekday; holidays_not_modelled' && <p className="ti-field-note">{t('tradeidea.observedQuoteFreshness')}</p>}
            {!researchMode && <p className="ti-field-note">{t(observedQuote.comparison_status === 'fx_not_rolled' ? 'tradeidea.observedQuoteFxMissing' : 'tradeidea.observedQuoteComparisonPending')}</p>}
          </div>}
          {preflight?.document_sources && preflight.document_sources.status !== 'not_supplied' && <div className="ti-catalog"><h3>{t('tradeidea.addedSources')}</h3><p>{t(preflight.document_sources.status === 'verified' ? 'tradeidea.sourceIdentityVerified' : 'tradeidea.sourceNeedsVerification')}</p>{preflight.document_sources.documents.map((document,index) => <div className="ti-document-receipt" key={document.id || index}><a href={document.url} target="_blank" rel="noreferrer">{document.url}</a>{document.published_at && <p>{t('tradeidea.sourcePublished')}: {document.published_at}</p>}{document.report_date && <p>{t('tradeidea.sourceReportDate')}: {document.report_date}</p>}{document.reason && <p>{document.reason}</p>}</div>)}<p className="ti-field-note">{t(researchMode ? 'tradeidea.companyResearchQualificationSeparate' : 'tradeidea.sourceQualificationSeparate')}</p></div>}
          <h3>{t('tradeidea.models')}</h3>{preflight?.models?.length ? <div className="ti-models">{preflight.models.map((model, index) => <div key={`${model.role}-${index}`}><span>{model.role}</span><strong>{model.model}</strong><small>{model.reasoning_effort || t('tradeidea.unknown')}{model.reason ? ` · ${model.reason}` : ''}</small></div>)}</div> : <p className="ti-muted">{t('tradeidea.modelsMissing')}</p>}
          <div className="ti-catalog"><h3>{t('tradeidea.priceCatalog')}</h3><p className="ti-muted">{t('tradeidea.costUnknown')}</p>{prices && Object.keys(prices).length ? <div className="ti-price-list">{Object.entries(prices).map(([role, price]) => <div key={role}><strong>{role}</strong><span>{t('tradeidea.priceInput')}: ${tokenPrice(price.prompt, locale, t('tradeidea.unknown'))} · {t('tradeidea.priceOutput')}: ${tokenPrice(price.completion, locale, t('tradeidea.unknown'))} {t('tradeidea.priceUnit')}</span></div>)}</div> : <p className="ti-muted">{t('tradeidea.pricesMissing')}</p>}{catalog?.checked_at && <small>{t('tradeidea.catalogChecked')}: {dateLabel(catalog.checked_at, locale, t('tradeidea.unknown'))}</small>}{catalog?.source && <small>{t('tradeidea.catalogSource')}: {catalog.source}</small>}{catalog?.account_access_verified === false && <small>{t('tradeidea.accountAccessUnverified')}</small>}</div>
          {!researchMode && preflight?.valuation && <div className="ti-catalog"><h3>{t('tradeidea.valuationGate')}</h3><p>{preflight.valuation.candidate_status || preflight.valuation.status || t('tradeidea.unknown')}</p>{(preflight.valuation.candidate_reason || preflight.valuation.reason) && <small>{preflight.valuation.candidate_reason || preflight.valuation.reason}</small>}</div>}
        </>}
        <div className="ti-launch"><p>{t('tradeidea.launchNotice')}</p><button className="ti-button ti-primary" disabled={!canLaunch} onClick={() => setAskLaunch(true)}><Play size={13} />{launching ? t('tradeidea.launching') : t('tradeidea.launch')}</button></div>
        {activeError && (activeError.storage ? <TradeIdeaStorageNotice storage={activeError.storage} lead={t('tradeidea.activeUnavailable')} />
          : <div className="ti-notice ti-error" role="alert">{t('tradeidea.activeUnavailable')}: {activeError.text}</div>)}
        {/* R14 seguito (sicurezza): /active illeggibile = avvio bloccato anche senza run nota, mai una seconda run a pagamento alla cieca */}
        {activeError && <div className="ti-notice ti-error" data-ti-launch-blocked="active-unverified"><p>{t('tradeidea.launchBlockedActiveUnknown')}</p><button className="ti-button ti-quiet" disabled={recheckingActive} onClick={() => void recheckActive()}><RefreshCw size={12} className={recheckingActive ? 'animate-spin' : ''} />{recheckingActive ? t('tradeidea.retryingActiveCheck') : t('tradeidea.retryActiveCheck')}</button></div>}
        {/* H4: con /active non leggibile la run attiva nota resta (canLaunch falso) ma non si afferma «in corso» al presente */}
        {activeId && <div className="ti-notice"><p>{activeError ? t('tradeidea.activeLastKnown', { id: activeId }) : t('tradeidea.activeElsewhere')}</p><button className="ti-button ti-quiet" onClick={() => openRun(activeId)}>{activeError ? t('tradeidea.openLastKnown') : t('tradeidea.openActive')} <ArrowRight size={12} /></button></div>}
        {launchError && (launchStorage ? <TradeIdeaStorageNotice storage={launchStorage} lead={t('tradeidea.launchFailed', { error: t('tradeidea.storageLead') })} />
          : <div className="ti-notice ti-error" role="alert">{t('tradeidea.launchFailed', { error: launchError })}</div>)}
      </section>
    </div>
    <div className="ti-history-detail">
      <aside className="ti-slab ti-history"><div className="ti-slab-head"><div><h2>{t('tradeidea.history')}</h2><p>{t('tradeidea.historySubtitle')}</p></div><span>{t('tradeidea.markArchive')}</span></div><div className="ti-filters"><label>{t('tradeidea.historyTicker')}<input value={historyTicker} onChange={event => setHistoryTicker(event.target.value.toUpperCase())} maxLength={32} /></label><label>{t('tradeidea.historyStatus')}<select value={historyStatus} onChange={event => setHistoryStatus(event.target.value)}><option value="">{t('tradeidea.allStatuses')}</option>{['accepted', 'running', 'completed', 'incomplete', 'failed', 'cancelled', 'interrupted'].map(status => <option key={status} value={status}>{statusLabel(t, status)}</option>)}</select></label><button className="ti-button ti-quiet" onClick={() => { setHistoryLimit(20); void loadHistory(historyTicker, historyStatus, 20); }}>{t('tradeidea.applyFilters')}</button></div>
        {historyError && (historyStorage ? <TradeIdeaStorageNotice storage={historyStorage} lead={t('tradeidea.historyError', { error: t('tradeidea.storageLead') })} />
          : <div className="ti-notice ti-error" role="alert">{t('tradeidea.historyError', { error: historyError })}</div>)}
        {!historyError && !history.length && !loadingHistory && <p className="ti-muted">{t('tradeidea.historyEmpty')}</p>}
        <div className="ti-history-list">{history.map(run => { const id = runId(run), status = runStatus(run); return <button key={id || `${run.ticker}-${run.created_at}`} className={`ti-history-item ${selectedId === id ? 'selected' : ''}`} onClick={() => id && openRun(id)}><span><strong>{run.ticker}</strong><small>{statusLabel(t, status)}</small></span><span>{dateLabel(run.started_at ?? run.created_at, locale, t('tradeidea.unknown'))}</span>{run.destination?.kind && <em>{run.destination.kind === 'dcn' ? t('tradeidea.destinationDcn') : run.destination.kind === 'research' ? t('tradeidea.destinationResearch') : t('tradeidea.destinationNone')}</em>}</button>; })}</div>
        {historyTotal != null && history.length < historyTotal && <button className="ti-button ti-quiet ti-more" disabled={loadingHistory} onClick={() => { const next = historyLimit + 20; setHistoryLimit(next); void loadHistory(historyTicker, historyStatus, next); }}>{t('tradeidea.historyMore')}</button>}
      </aside>
      {detail && <TradeIdeaCostReconcile key={'costi-' + (detail.run.id || detail.run.run_id)} detail={detail} onChanged={id => { void loadDetail(id); void loadHistory(historyTicker, historyStatus, historyLimit); }} />}
      {detail && <TradeIdeaRecoveryPanel key={detail.run.id || detail.run.run_id} detail={detail} onChanged={id => { if (id === selectedId) void loadDetail(id); else openRun(id); void loadHistory(historyTicker, historyStatus, historyLimit); }} />}
      <section className="ti-detail-wrap"><div className="ti-detail-toolbar"><span>{t('tradeidea.detail')}</span><button className="ti-button ti-quiet" disabled={refreshing} onClick={() => void refresh()}><RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />{refreshing ? t('tradeidea.refreshing') : t('tradeidea.refresh')}</button></div>{detailError && (detailStorage ? <TradeIdeaStorageNotice storage={detailStorage} lead={t('tradeidea.detailError', { error: t('tradeidea.storageLead') })} />
          : <div className="ti-notice ti-error" role="alert">{t('tradeidea.detailError', { error: detailError })}</div>)}{detail ? <TradeIdeaRunDetail detail={detail} now={now} onStop={() => setAskStop(true)} stopping={stopping} stopError={stopError} onRetryEmail={() => detail.email?.status === 'uncertain' ? setAskUncertainEmail(true) : void retryEmail()} retryingEmail={retryingEmail} retryError={retryError} stopStorage={stopStorage} retryStorage={retryStorage} onReuse={reuse} onDownload={artifact => void download(artifact)} downloadError={downloadError} downloading={downloading} /> : !detailError && <div className="ti-empty">{selectedId ? t('ui.loading') : t('tradeidea.detailMissing')}</div>}</section>
    </div>
    <ConfirmDialog open={askLaunch} title={t('tradeidea.confirmTitle')} intro={t('tradeidea.confirmIntro')} rows={[{ k: t('tradeidea.ticker'), v: resolved || ticker }, { k: t('tradeidea.budgetLimit'), v: budgetValue == null ? t('tradeidea.unknown') : new Intl.NumberFormat(locale, { style: 'currency', currency: 'USD' }).format(budgetValue) }, { k: t('tradeidea.models'), v: preflight?.models?.map(item => `${item.role}: ${item.model} (${item.reasoning_effort || t('tradeidea.unknown')})`).join(' / ') || t('tradeidea.modelsMissing') }]} confirmLabel={t('tradeidea.launch')} cancelLabel={t('tradeidea.confirmCancel')} onConfirm={() => void start()} onCancel={() => setAskLaunch(false)} />
    <ConfirmDialog open={askStop} tone="crimson" title={t('tradeidea.stop')} intro={t('tradeidea.stopConfirm')} confirmLabel={t('tradeidea.stop')} cancelLabel={t('tradeidea.confirmCancel')} onConfirm={() => void stop()} onCancel={() => setAskStop(false)} />
    <ConfirmDialog open={askUncertainEmail} tone="crimson" title={t('tradeidea.retryUncertainTitle')} intro={t('tradeidea.retryUncertainIntro')} confirmLabel={t('tradeidea.retryUncertainConfirm')} cancelLabel={t('tradeidea.confirmCancel')} onConfirm={() => void retryEmail(true)} onCancel={() => setAskUncertainEmail(false)} />
  </div>;
}
