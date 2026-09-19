import { useT } from '@/i18n/provider';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { localizePayload } from '@/lib/api-presentation';
import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, ArrowUpRight, BookOpen, CheckCircle2, CircleDashed, RefreshCw } from 'lucide-react';
import { useBox } from '@/lib/useBox';
import {
  getAgentProgress, progressNumber as num, progressPercent as pct, qualityLabels,
  type AgentProgress, type ProgressAgent, type ProgressPoint, type ProgressRun,
} from '@/lib/agent-progress';
import './agent-progress.css';

function stamp(value: string | null | undefined) {
  if (!value) return tr('progress.na');
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? tr('progress.badDate') : date.toLocaleString(localeDi(linguaCorrente()), { dateStyle: 'medium', timeStyle: 'short' });
}
function quality(point: ProgressPoint | null | undefined) {
  return point ? point.quality.map(key => qualityLabels()[key] || key).join(' · ') : tr('progress.noMeasure');
}

function EvidenceTable({ run, kind }: { run?: ProgressRun; kind: 'action' | 'confidence' | 'calls' }) {
  const tr = useT();
  const groups = kind === 'action' ? run?.scorecard?.by_action : run?.scorecard?.by_confidence;
  const calls = run?.scorecard?.details;
  const excluded = run?.scorecard?.by_confidence_scartate;
  const confidence = (value?: string) => value === 'ALTA' ? tr('progress.high') : value === 'MEDIA' ? tr('progress.medium') : value === 'BASSA' ? tr('progress.low') : value || tr('progress.na');
  return <div className="ap-evidence-table"><p>{tr('progress.committeeBreakdown')}</p>
    {run?.scorecard?.degraded === true ? <p className="ap-inline-error" role="status">{tr('progress.quality_degraded')}</p>
      : (kind === 'calls' ? !calls?.length : !groups || !Object.keys(groups).length) ? <p className="ap-empty">{tr('progress.breakdownMissing')}</p>
      : kind === 'calls' ? <div className="ap-history-scroll"><table><caption>{tr('progress.tabCalls')}</caption><thead><tr>
        <th>ID</th><th>Ticker</th><th>{tr('progress.action')}</th><th>{tr('progress.date')}</th><th>{tr('progress.confidenceOriginal')}</th><th>{tr('progress.confidenceLabel')}</th><th>{tr('progress.horizon')}</th>
        <th>{tr('progress.direction')}</th><th>1w</th><th>4w</th><th>Edge</th><th>{tr('progress.outcome')}</th><th>Desk</th><th>{tr('progress.reportState')}</th>
      </tr></thead><tbody>{calls!.map((call, index) => <tr key={call.id ?? index}><td>{call.id ?? tr('progress.na')}</td><td>{call.ticker || tr('progress.na')}</td>
        <td>{call.action || tr('progress.na')}</td><td>{stamp(call.date)}</td><td>{call.confidence || tr('progress.na')}</td><td>{confidence(call.confidence_bucket)}</td><td>{call.horizon_used || tr('progress.na')}</td>
        <td>{call.direction || tr('progress.na')}</td><td>{pct(call.ret_1w_pct)}</td><td>{pct(call.ret_4w_pct)}</td><td>{pct(call.edge_pct)}</td>
        <td>{call.hit === true ? tr('progress.hitYes') : call.hit === false ? tr('progress.hitNo') : tr('progress.na')}</td><td>{call.specialists?.join(', ') || tr('progress.na')}</td><td>{call.status || tr('progress.na')}</td>
      </tr>)}</tbody></table></div> : <div className="ap-history-scroll"><table><caption>{tr(kind === 'action' ? 'progress.tabAction' : 'progress.tabConfidence')}</caption>
        <thead><tr><th>{tr('progress.category')}</th><th>Call</th><th>{tr('progress.correct')}</th><th>Hit rate</th><th>{tr('progress.edge')}</th><th>{tr('progress.qualityScope')}</th></tr></thead>
        <tbody>{Object.entries(groups!).map(([name, value]) => <tr key={name}><td>{kind === 'confidence' ? confidence(name) : name}</td>
          <td>{num(value.n, 0)}</td><td>{num(value.hits, 0)}</td><td>{pct(value.hit_rate_pct)}</td><td>{pct(value.avg_edge_pct)}</td><td>{value.small_sample === true ? tr('progress.quality_small_sample') : tr('progress.na')}</td></tr>)}</tbody></table></div>}
    {kind === 'confidence' && <div className="ap-confidence-excluded"><h3>{tr('progress.excludedConfidence')}</h3>
      <p>{num(excluded?.n, 0)} · {excluded?.motivo || tr('progress.na')}</p>
      {Object.entries(excluded?.etichette || {}).map(([label, count]) => <p key={label}>{label}: {num(count, 0)}</p>)}
    </div>}
  </div>;
}

function TrackChart({ agent, selected, onSelect }: { agent: ProgressAgent; selected: string; onSelect: (id: string) => void }) {
  const tr = useT();
  const [ref, box] = useBox<HTMLDivElement>();
  // Run letta dal puntatore o dalla tastiera. E' un INDICE: si rilegge su `rows` a ogni render,
  // cosi' una serie accorciata da un aggiornamento spegne la lettura invece di mostrarne un'altra.
  const [active, setActive] = useState<number | null>(null);
  const rows = agent.series;
  const w = Math.max(box.w, 320), h = Math.max(box.h, 280), left = 48, right = 30, top = 25, bottom = 42;
  const x = (index: number) => left + (w - left - right) * (rows.length <= 1 ? 0.5 : index / (rows.length - 1));
  const y = (value: number) => top + (100 - value) / 100 * (h - top - bottom);
  const reading = active == null ? undefined : rows[active];
  // Lettura esatta: gli stessi numeri del <title> piu' intervallo e qualita'; ogni buco resta n.d. dichiarato.
  const readingText = reading ? [
    tr('progress.pointTitle', {a: reading.memo_id ?? tr('progress.na'), b: pct(reading.hit_rate_pct), c: num(reading.n, 0), d: stamp(reading.computed_at)}),
    `${tr('progress.confidence')} ${reading.ci95 ? `${num(reading.ci95.low_pct)}–${num(reading.ci95.high_pct)}%` : tr('progress.na')}`,
    quality(reading) || tr('progress.na'),
  ].join(' · ') : '';
  const readAt = (event: React.PointerEvent<SVGSVGElement>) => {
    const px = event.clientX - event.currentTarget.getBoundingClientRect().left;
    setActive(rows.length <= 1 ? 0 : Math.max(0, Math.min(rows.length - 1, Math.round((px - left) / (w - left - right) * (rows.length - 1)))));
  };
  return <div className="ap-chart" ref={ref}>
    {rows.some(row => row.hit_rate_pct != null) ? <><svg width={w} height={h} role="img" aria-label={tr('progress.chartAria', {a: agent.label})}
      onPointerMove={readAt} onPointerLeave={() => setActive(null)}>
      {[0, 25, 50, 75, 100].map(level => <g key={level}>
        <line x1={left} x2={w - right} y1={y(level)} y2={y(level)} className={level === 50 ? 'ap-midline' : 'ap-gridline'} />
        <text x={left - 10} y={y(level) + 4} textAnchor="end">{level}%</text>
      </g>)}
      {reading && active != null && <line x1={x(active)} x2={x(active)} y1={top} y2={h - bottom} className="ap-midline" pointerEvents="none" />}
      {rows.map((row, index) => {
        const prev = rows[index - 1];
        const chosen = row.run_id === selected || (!selected && index === rows.length - 1);
        const valid = row.hit_rate_pct != null;
        return <g key={row.run_id || index}>
          {index > 0 && valid && prev.hit_rate_pct != null && row.delta?.available &&
            <line x1={x(index - 1)} y1={y(prev.hit_rate_pct)} x2={x(index)} y2={y(row.hit_rate_pct!)} className="ap-series" />}
          {chosen && valid && row.ci95 && <g className="ap-interval">
            <line x1={x(index)} x2={x(index)} y1={y(row.ci95.low_pct)} y2={y(row.ci95.high_pct)} />
            <line x1={x(index) - 7} x2={x(index) + 7} y1={y(row.ci95.low_pct)} y2={y(row.ci95.low_pct)} />
            <line x1={x(index) - 7} x2={x(index) + 7} y1={y(row.ci95.high_pct)} y2={y(row.ci95.high_pct)} />
          </g>}
          <circle cx={x(index)} cy={valid ? y(row.hit_rate_pct!) : h - bottom + 8} r={chosen ? 6 : 4}
            className={`${valid ? 'ap-point' : 'ap-point-missing'} ${chosen ? 'ap-selected' : ''}`}
            role="button" tabIndex={0} aria-label={tr('progress.pointAria', {a: row.memo_id ?? tr('progress.na'), b: pct(row.hit_rate_pct), c: quality(row)})}
            onClick={() => onSelect(row.run_id!)} onFocus={() => setActive(index)} onBlur={() => setActive(null)} onKeyDown={event => {
              if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(row.run_id!); }
              if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                // Le frecce spostano la LETTURA sulla run vicina, non la selezione (quella resta a Invio/Spazio).
                event.preventDefault();
                const next = Math.max(0, Math.min(rows.length - 1, index + (event.key === 'ArrowLeft' ? -1 : 1)));
                setActive(next);
                event.currentTarget.ownerSVGElement?.querySelectorAll<SVGCircleElement>('circle[role=button]')[next]?.focus();
              }
            }}><title>{tr('progress.pointTitle', {a: row.memo_id ?? tr('progress.na'), b: pct(row.hit_rate_pct), c: num(row.n, 0), d: stamp(row.computed_at)})}</title></circle>
          {(rows.length <= 7 || index === 0 || index === rows.length - 1 || chosen) &&
            <text x={x(index)} y={h - 12} textAnchor="middle">#{row.memo_id}</text>}
        </g>;
      })}
    </svg>{reading && <div className="ap-chart-reading">{readingText}</div>}</> : <div className="ap-chart-empty"><CircleDashed size={32} /><strong>{tr('progress.noPoints')}</strong>
      <span>{tr('progress.firstPoint')}</span></div>}
  </div>;
}

export default function AgentProgressPage() {
  const tr = useT();
  const [rawData, setData] = useState<AgentProgress | null>(null);
  const data = useMemo(() => rawData ? localizePayload(rawData) : null, [rawData, tr]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [agentId, setAgentId] = useState('capo');
  const [runId, setRunId] = useState('');
  const [tab, setTab] = useState<'measurements' | 'action' | 'confidence' | 'calls' | 'activity'>('measurements');
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    getAgentProgress(controller.signal).then(setData).catch(e => {
      if (!controller.signal.aborted) { setError(e.message || ''); setData(null); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reload]);
  const agent = data?.agents.find(item => item.id === agentId) || data?.agents[0];
  const current = runId === 'current';
  const point = current ? agent?.current : agent?.series.find(item => item.run_id === runId) || agent?.latest;
  const run = current ? undefined : data?.runs.find(item => item.run_id === runId) || data?.runs[data.runs.length - 1];
  const delta = current ? null : point?.delta || agent?.delta;
  const usage = point?.operational.usage;
  const unsupported = agent?.attribution === 'unsupported';
  return <section className="agent-progress" aria-busy={loading}>
    <header className="ap-header"><div><h1>{tr('progress.title')}</h1>
      <p>{tr('progress.subtitle')}</p></div>
      <button type="button" className="ap-refresh" onClick={() => setReload(value => value + 1)} disabled={loading}>
        <RefreshCw size={15} className={loading ? 'ap-spinning' : ''} /> {loading ? tr('progress.reading') : tr('progress.refresh')}
      </button></header>
    {error !== null && <div className="ap-error" role="alert"><AlertCircle size={20} /><div><strong>{tr('progress.unavailable')}</strong>
      <p>{error || tr('progress.readFailed')}</p><p>{tr('progress.retryHelp')}</p></div></div>}
    {loading && !data && <div className="ap-loading" role="status">{tr('progress.loading')}</div>}
    {data && !agent && <p role="status">{tr('progress.emptyAgents')}</p>}
    {data && agent && <>
      <div className="ap-evidence-strip"><span><CheckCircle2 size={15} /> {num(data.history.count, 0)} {tr('progress.recordedRuns')}</span>
        <span>{tr('progress.firstRecorded')} {stamp(data.history.first_captured_at)}</span><span>{tr('progress.source')} {data.source}</span></div>
      <div className="ap-notice">{data.current_scorecard?.available ? tr('progress.currentSeparate') : tr('progress.currentMissing')}
        {' · '}{stamp(data.current_scorecard?.computed_at)}{data.current_scorecard?.error && ` · ${data.current_scorecard.error}`}</div>
      {!data.history.available && <div className="ap-notice"><strong>{tr('progress.historyStarts')}</strong> {data.history.note}.
        {data.current_scorecard?.available ? tr('progress.currentAvailable') : ` ${data.current_scorecard?.error || tr('progress.currentMissing')}`}</div>}
      <div className="ap-workspace">
        <aside className="ap-roster" aria-label={tr('progress.rosterAria')}>
          <div className="ap-section-heading"><h2>{tr('progress.agentsRoles')}</h2><span>{tr('progress.latestMeasure')}</span></div>
          {data.agents.map(item => <button type="button" key={item.id} className={`ap-agent ${item.id === agent.id ? 'is-active' : ''}`}
            aria-pressed={item.id === agent.id} onClick={() => setAgentId(item.id)}>
            <span className="ap-agent-name">{item.label}</span><span className="ap-agent-score">{item.attribution === 'unsupported' ? '—' : pct(item.latest?.hit_rate_pct)}</span>
            <span className="ap-agent-role">{item.role}</span>
            <span className="ap-agent-sample">{item.attribution === 'unsupported' ? tr('progress.noScore') : `n=${num(item.latest?.n, 0)} · ${quality(item.latest)}`}</span>
          </button>)}
        </aside>
        <section className="ap-detail" aria-labelledby="ap-agent-title">
          <div className="ap-detail-header"><div><h2 id="ap-agent-title">{agent.label}</h2><p>{agent.role}</p></div>
            <label className="ap-run-select">{tr('progress.measurement')}<select value={runId} onChange={event => setRunId(event.target.value)}>
              <option value="">{tr('progress.latest')}</option>
              {agent.current && <option value="current">{tr('progress.currentChoice')}</option>}{[...data.runs].reverse().map(item =>
                <option key={item.run_id} value={item.run_id}>Memo #{item.memo_id} · {stamp(item.completed_at)}{item.review_status === 'duplicate' ? ` · ${tr('progress.duplicate')}` : ''}</option>)}
            </select></label></div>
          <p className="ap-attribution">{agent.attribution === 'collective'
            ? tr('progress.collective')
            : unsupported ? tr('progress.unsupported') : data.method.attribution}</p>
          {!unsupported && <div className="ap-metrics" aria-live="polite">
            <div><span>{tr('progress.hitRate')}</span><strong>{pct(point?.hit_rate_pct)}</strong><small>{num(point?.hits, 0)} {tr(point?.hits === 1 ? 'progress.hitsOne' : 'progress.hits')} {tr(point?.n === 1 ? 'progress.callsCountOne' : 'progress.callsCount', { a: num(point?.n, 0) })}</small></div>
            <div><span>{tr('progress.confidence')}</span><strong className="ap-medium">{point?.ci95 ? `${num(point.ci95.low_pct)}–${num(point.ci95.high_pct)}%` : tr('progress.na')}</strong><small>{tr('progress.wilson')}</small>
              {point?.ci95 && point.hit_rate_pct != null && <div className="ap-confidence-bar" role="img" aria-label={tr('progress.confidenceAria', { low: num(point.ci95.low_pct), high: num(point.ci95.high_pct), rate: num(point.hit_rate_pct) })}>
                <svg viewBox="0 0 100 15" preserveAspectRatio="none" aria-hidden="true"><line x1="0" x2="100" y1="8" y2="8" className="ap-midline" />
                  <line x1={point.ci95.low_pct} x2={point.ci95.high_pct} y1="8" y2="8" className="ap-interval-range" />
                  <line x1={point.hit_rate_pct} x2={point.hit_rate_pct} y1="4" y2="12" className="ap-interval-point" /></svg><div><span>0</span><span>50</span><span>100%</span></div>
              </div>}</div>
            <div><span>{tr('progress.edge')}</span><strong>{pct(point?.avg_edge_pct)}</strong><small>{tr('progress.directionReturn')}</small></div>
            <div><span>{tr('progress.delta')}</span><strong className="ap-medium">{delta?.available ? `${delta.hit_rate_pp! > 0 ? '+' : ''}${num(delta.hit_rate_pp)} pp` : tr('progress.notComparable')}</strong><small>{delta?.reason}</small></div>
          </div>}
          <div className="ap-measurement"><span>{quality(point)}</span><span>{tr('progress.measured')} {stamp(point?.computed_at)}</span><span>{tr('progress.source')} {point?.source || tr('progress.na')}</span></div>
          {run?.review_status === 'duplicate' && <div className="ap-notice"><strong>{tr('progress.duplicate')}</strong>{run.review_note && <p>{run.review_note}</p>}</div>}
          {run && (!run.review_status || run.review_status === 'unavailable') && <p className="ap-measurement">{tr('progress.reviewUnknown')}</p>}
          <div className="ap-tabs" role="tablist" aria-label={tr('progress.evidenceTabs')}>
            {(['measurements', 'action', 'confidence', 'calls', 'activity'] as const).map((id, index) => <button key={id} id={`ap-tab-${id}`} type="button" role="tab"
              aria-selected={tab === id} aria-controls={`ap-panel-${id}`} tabIndex={tab === id ? 0 : -1} onClick={() => setTab(id)}
              onKeyDown={event => { const ids = ['measurements', 'action', 'confidence', 'calls', 'activity'] as const;
                const next = event.key === 'ArrowRight' ? ids[(index + 1) % ids.length] : event.key === 'ArrowLeft' ? ids[(index + ids.length - 1) % ids.length] : event.key === 'Home' ? ids[0] : event.key === 'End' ? ids[4] : null;
                if (next) { event.preventDefault(); setTab(next); document.getElementById(`ap-tab-${next}`)?.focus(); }
              }}>{tr(({ measurements: 'progress.tabMeasurements', action: 'progress.tabAction', confidence: 'progress.tabConfidence', calls: 'progress.tabCalls', activity: 'progress.tabActivity' } as const)[id])}</button>)}
          </div>
          <div id="ap-panel-measurements" role="tabpanel" aria-labelledby="ap-tab-measurements" hidden={tab !== 'measurements'}>
          {!unsupported && <details className="ap-trend-panel" aria-label={tr('progress.evolution')}>
            <summary className="ap-section-heading"><h3>{tr('progress.historyChart')}</h3><span>{tr('progress.sequenceAxis')}</span></summary>
            <TrackChart key={agent.id} agent={agent} selected={runId} onSelect={setRunId} />
            <div className="ap-chart-key"><span><i className="ap-dot" />{tr('progress.savedMeasure')}</span><span><i className="ap-line" />{tr('progress.sameCohort')}</span><span>{tr('progress.verticalInterval')}</span></div>
            <p>{data.method.timing} {tr('progress.lineGaps')}</p>
          </details>}
          </div>
          {(['action', 'confidence', 'calls'] as const).map(id => <div key={id} id={`ap-panel-${id}`} role="tabpanel" aria-labelledby={`ap-tab-${id}`} hidden={tab !== id}>
            {tab === id && <EvidenceTable run={run} kind={id} />}</div>)}
          <div className="ap-support-grid" id="ap-panel-activity" role="tabpanel" aria-labelledby="ap-tab-activity" hidden={tab !== 'activity'}>
            <section className="ap-quality"><h3>{tr('progress.qualityScope')}</h3><dl>
              <div><dt>{tr('progress.window')}</dt><dd>{num(point?.window_days, 0)} {tr('progress.days')}</dd></div>
              <div><dt>{tr('progress.maturation')}</dt><dd>{num(point?.maturation_days, 0)} {tr('progress.days')}</dd></div>
              <div><dt>{tr('progress.candidates')}</dt><dd>{num(point?.n_directional_candidates, 0)}</dd></div>
              <div><dt>{tr('progress.unmeasurable')}</dt><dd>{num(point?.n_unmeasurable, 0)}</dd></div>
              <div><dt>{tr('progress.failedTickers')}</dt><dd>{num(point?.n_fetch_fail, 0)}</dd></div>
            </dl><p>{tr('progress.scopeHelp')}</p>
            {run?.score_error && <p className="ap-inline-error">{run.score_error}</p>}</section>
            <section className="ap-operations"><h3>{tr('progress.activityTitle')}</h3><dl>
              <div><dt>{tr('progress.models')}</dt><dd>{point?.operational.models?.join(', ') || tr('progress.na')}</dd></div>
              <div><dt>{tr('progress.reportState')}</dt><dd>{point?.operational.status || tr('progress.na')}</dd></div>
              <div><dt>{tr('progress.callState')}</dt><dd>{usage?.status || tr('progress.na')}</dd></div>
              <div><dt>{tr('progress.callsRecorded')}</dt><dd>{num(usage?.api_calls, 0)}</dd></div>
              <div><dt>{tr('progress.timeRecorded')}</dt><dd>{num(usage?.duration_s)} s</dd></div>
              <div><dt>{tr('progress.costRecorded')}</dt><dd>{num(usage?.cost_eur, 2)} EUR{usage?.partial ? tr('progress.partialCost') : ''}</dd></div>
            </dl><p>{tr('progress.activityHelp')}</p></section>
          </div>
      <div className="ap-bottom-grid">
        <details className="ap-lessons"><summary className="ap-section-heading"><h2><BookOpen size={18} /> {tr('progress.lesson')}</h2>
          <span>{run ? `Memo #${run.memo_id}` : tr('progress.noRuns')}</span></summary>
          <div className="ap-lesson-status"><span>{run?.reflection?.kind === 'suggestion' ? tr('progress.suggestion') : run?.reflection?.kind || tr('progress.noReflection')}</span>
            <span>{tr(run?.reflection?.implementation_verified === true ? 'progress.implementationYes' : 'progress.implementationNo')}</span>
            <span>{tr(run?.reflection?.performance_proven === true ? 'progress.performanceYes' : 'progress.performanceNo')}</span>
            <span>{tr(run?.output_language === 'it' ? 'communications.originalOutputIt' : run?.output_language === 'en' ? 'communications.originalOutputEn' : 'communications.originalOutputUnknown')}</span></div>
          {run?.reflection?.text ? <div className="ap-lesson-text">{run.reflection.text}</div> :
            <p className="ap-empty">{tr('progress.noReflection')} {run?.reflection?.status || tr('progress.na')}.</p>}
          <p className="ap-explainer">{data.method.learning}{tr('progress.lessonScope')}</p>
        </details>
        <section className="ap-history" hidden={tab !== 'measurements'}><div className="ap-section-heading"><h2>{tr('progress.runRegister')}</h2><span>{tr('progress.recent')} {num(data.runs.length, 0)} {tr('progress.recorded')}</span></div>
          <div className="ap-history-scroll"><table><thead><tr><th>Memo</th><th>{tr('progress.completed')}</th><th>Hit rate {agent.label}</th><th>Call</th></tr></thead>
            <tbody>{[...agent.series].reverse().map(item => <tr key={item.run_id} className={item.run_id === run?.run_id ? 'is-selected' : ''}>
              <td><button type="button" onClick={() => setRunId(item.run_id!)} aria-label={tr('progress.openMemo', {a: item.memo_id ?? tr('progress.na')})}>#{item.memo_id}<ArrowUpRight size={12} /></button></td>
              <td>{stamp(item.completed_at)}{data.runs.find(r => r.run_id === item.run_id)?.review_status === 'duplicate' && <span className="ap-duplicate"> · {tr('progress.duplicate')}</span>}</td><td>{pct(item.hit_rate_pct)}</td><td>{num(item.n, 0)}</td>
            </tr>)}</tbody></table>{!data.runs.length && <p className="ap-empty">{tr('progress.noBackfill')}</p>}</div>
        </section>
      </div>
      <details className="ap-method"><summary>{tr('progress.methodTitle')}</summary><p>{data.method.score}.</p>
        <p>{data.method.horizon}{tr('progress.smallSampleHelp')}</p><p>{data.method.comparison}.</p>
        <p>{tr('progress.notLearning')}</p>
        <p>{data.history.note}{tr('progress.readonly')}</p></details>
        </section>
      </div>
    </>}
  </section>;
}
