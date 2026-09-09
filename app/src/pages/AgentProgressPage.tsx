import { useEffect, useState } from 'react';
import { AlertCircle, ArrowUpRight, BookOpen, CheckCircle2, CircleDashed, RefreshCw } from 'lucide-react';
import { useBox } from '@/lib/useBox';
import {
  getAgentProgress, progressNumber as num, progressPercent as pct, QUALITY_LABELS,
  type AgentProgress, type ProgressAgent, type ProgressPoint,
} from '@/lib/agent-progress';
import './agent-progress.css';

function stamp(value: string | null | undefined) {
  if (!value) return 'n.d.';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Data non valida' : date.toLocaleString('it-IT', { dateStyle: 'medium', timeStyle: 'short' });
}
function quality(point: ProgressPoint | null | undefined) {
  return point ? point.quality.map(key => QUALITY_LABELS[key] || key).join(' · ') : 'Nessuna misura registrata';
}

function TrackChart({ agent, selected, onSelect }: { agent: ProgressAgent; selected: string; onSelect: (id: string) => void }) {
  const [ref, box] = useBox<HTMLDivElement>();
  const rows = agent.series;
  const w = Math.max(box.w, 320), h = Math.max(box.h, 280), left = 48, right = 30, top = 25, bottom = 42;
  const x = (index: number) => left + (w - left - right) * (rows.length <= 1 ? 0.5 : index / (rows.length - 1));
  const y = (value: number) => top + (100 - value) / 100 * (h - top - bottom);
  return <div className="ap-chart" ref={ref}>
    {rows.some(row => row.hit_rate_pct != null) ? <svg width={w} height={h} role="img" aria-label={`Hit rate storico di ${agent.label}, punti per run. I tratti collegano solo misure confrontabili.`}>
      {[0, 25, 50, 75, 100].map(level => <g key={level}>
        <line x1={left} x2={w - right} y1={y(level)} y2={y(level)} className={level === 50 ? 'ap-midline' : 'ap-gridline'} />
        <text x={left - 10} y={y(level) + 4} textAnchor="end">{level}%</text>
      </g>)}
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
            role="button" tabIndex={0} aria-label={`Memo ${row.memo_id}: ${pct(row.hit_rate_pct)}, ${quality(row)}`}
            onClick={() => onSelect(row.run_id!)} onKeyDown={event => {
              if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(row.run_id!); }
            }}><title>{`Memo ${row.memo_id} • ${pct(row.hit_rate_pct)} • n=${num(row.n, 0)} • ${stamp(row.computed_at)}`}</title></circle>
          {(rows.length <= 7 || index === 0 || index === rows.length - 1 || chosen) &&
            <text x={x(index)} y={h - 12} textAnchor="middle">#{row.memo_id}</text>}
        </g>;
      })}
    </svg> : <div className="ap-chart-empty"><CircleDashed size={32} /><strong>Nessun punto misurabile nello storico</strong>
      <span>Il primo punto comparirà dopo una run completata con esiti di mercato disponibili.</span></div>}
  </div>;
}

export default function AgentProgressPage() {
  const [data, setData] = useState<AgentProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [agentId, setAgentId] = useState('capo');
  const [runId, setRunId] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(null);
    getAgentProgress(controller.signal).then(setData).catch(e => {
      if (!controller.signal.aborted) { setError(e.message || 'Lettura non riuscita'); setData(null); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reload]);
  const agent = data?.agents.find(item => item.id === agentId) || data?.agents[0];
  const point = agent?.series.find(item => item.run_id === runId) || agent?.latest;
  const run = data?.runs.find(item => item.run_id === runId) || data?.runs[data.runs.length - 1];
  const delta = point?.delta || agent?.delta;
  const usage = point?.operational.usage;
  const unsupported = agent?.attribution === 'unsupported';
  return <main className="agent-progress" aria-busy={loading}>
    <header className="ap-header"><div><h1>Progressi degli agenti</h1>
      <p>Esiti delle decisioni, qualità delle misure e lezioni del comitato.</p></div>
      <button type="button" className="ap-refresh" onClick={() => setReload(value => value + 1)} disabled={loading}>
        <RefreshCw size={15} className={loading ? 'ap-spinning' : ''} /> {loading ? 'Lettura…' : 'Aggiorna dati'}
      </button></header>
    {error && <div className="ap-error" role="alert"><AlertCircle size={20} /><div><strong>Progressi non disponibili</strong>
      <p>{error}</p><p>Riprova dopo aver verificato backend e migrazione dello storico.</p></div></div>}
    {loading && !data && <div className="ap-loading" role="status">Caricamento delle misure salvate…</div>}
    {data && agent && <>
      <div className="ap-evidence-strip"><span><CheckCircle2 size={15} /> {num(data.history.count, 0)} run registrate</span>
        <span>Prima registrazione: {stamp(data.history.first_captured_at)}</span><span>Fonte: {data.source}</span></div>
      {!data.history.available && <div className="ap-notice"><strong>Lo storico parte da qui.</strong> {data.history.note}.
        {data.current_scorecard?.available ? ' Sotto trovi la misura corrente già disponibile, senza attribuirla a run passate.' : ` ${data.current_scorecard?.error || 'Nessuna misura corrente disponibile.'}`}</div>}
      <div className="ap-workspace">
        <aside className="ap-roster" aria-label="Agenti del comitato">
          <div className="ap-section-heading"><h2>Agenti e ruoli</h2><span>Ultima misura</span></div>
          {data.agents.map(item => <button type="button" key={item.id} className={`ap-agent ${item.id === agent.id ? 'is-active' : ''}`}
            aria-pressed={item.id === agent.id} onClick={() => setAgentId(item.id)}>
            <span className="ap-agent-name">{item.label}</span><span className="ap-agent-score">{item.attribution === 'unsupported' ? '—' : pct(item.latest?.hit_rate_pct)}</span>
            <span className="ap-agent-role">{item.role}</span>
            <span className="ap-agent-sample">{item.attribution === 'unsupported' ? 'Ruolo senza score direzionale' : `n=${num(item.latest?.n, 0)} · ${quality(item.latest)}`}</span>
          </button>)}
        </aside>
        <section className="ap-detail" aria-labelledby="ap-agent-title">
          <div className="ap-detail-header"><div><h2 id="ap-agent-title">{agent.label}</h2><p>{agent.role}</p></div>
            <label className="ap-run-select">Rilevazione<select value={runId} onChange={event => setRunId(event.target.value)}>
              <option value="">Ultima disponibile</option>{[...data.runs].reverse().map(item =>
                <option key={item.run_id} value={item.run_id}>Memo #{item.memo_id} · {stamp(item.completed_at)}</option>)}
            </select></label></div>
          <p className="ap-attribution">{agent.attribution === 'collective'
            ? 'Il Capo mostra il risultato collettivo delle decisioni del comitato; non una performance individuale isolata.'
            : unsupported ? 'Questo ruolo contribuisce al processo. Non esiste uno score direzionale indipendente misurato.' : data.method.attribution}</p>
          {!unsupported && <div className="ap-metrics">
            <div><span>Hit rate direzionale</span><strong>{pct(point?.hit_rate_pct)}</strong><small>{num(point?.hits, 0)} esiti corretti / {num(point?.n, 0)} call</small></div>
            <div><span>Intervallo al 95%</span><strong className="ap-medium">{point?.ci95 ? `${num(point.ci95.low_pct)}–${num(point.ci95.high_pct)}%` : 'n.d.'}</strong><small>Wilson · ampiezza = incertezza</small></div>
            <div><span>Edge medio</span><strong>{pct(point?.avg_edge_pct)}</strong><small>Rendimento nella direzione della call</small></div>
            <div><span>Δ vs rilevazione precedente</span><strong className="ap-medium">{delta?.available ? `${delta.hit_rate_pp! > 0 ? '+' : ''}${num(delta.hit_rate_pp)} pp` : 'Non confrontabile'}</strong><small>{delta?.reason}</small></div>
          </div>}
          <div className="ap-measurement"><span>{quality(point)}</span><span>Misurato: {stamp(point?.computed_at)}</span><span>Fonte: {point?.source || 'n.d.'}</span></div>
          {!unsupported && <section className="ap-trend-panel" aria-label="Evoluzione delle misure">
            <div className="ap-section-heading"><h3>Hit rate nel tempo</h3><span>Asse orizzontale: sequenza delle run</span></div>
            <TrackChart key={agent.id} agent={agent} selected={runId} onSelect={setRunId} />
            <div className="ap-chart-key"><span><i className="ap-dot" />Misura salvata</span><span><i className="ap-line" />Stessa coorte e metodo</span><span>Barra verticale: intervallo al 95% della selezione</span></div>
            <p>{data.method.timing} Le interruzioni della linea segnalano confronti non omogenei.</p>
          </section>}
          <div className="ap-support-grid">
            <section className="ap-quality"><h3>Qualità e perimetro</h3><dl>
              <div><dt>Finestra di osservazione</dt><dd>{num(point?.window_days, 0)} giorni</dd></div>
              <div><dt>Maturazione minima</dt><dd>{num(point?.maturation_days, 0)} giorni</dd></div>
              <div><dt>Candidati direzionali</dt><dd>{num(point?.n_directional_candidates, 0)}</dd></div>
              <div><dt>Call non misurabili</dt><dd>{num(point?.n_unmeasurable, 0)}</dd></div>
              <div><dt>Titoli con fetch fallito</dt><dd>{num(point?.n_fetch_fail, 0)}</dd></div>
            </dl><p>Le call escluse comprendono azioni non direzionali, titoli non quotabili e prezzi mancanti. I conteggi si riferiscono al comitato.</p>
            {run?.score_error && <p className="ap-inline-error">{run.score_error}</p>}</section>
            <section className="ap-operations"><h3>Attività nella run selezionata</h3><dl>
              <div><dt>Modelli usati</dt><dd>{point?.operational.models?.join(', ') || 'n.d.'}</dd></div>
              <div><dt>Stato report</dt><dd>{point?.operational.status || 'n.d.'}</dd></div>
              <div><dt>Stato delle chiamate</dt><dd>{usage?.status || 'n.d.'}</dd></div>
              <div><dt>Chiamate registrate</dt><dd>{num(usage?.api_calls, 0)}</dd></div>
              <div><dt>Tempo registrato</dt><dd>{num(usage?.duration_s)} s</dd></div>
              <div><dt>Costo registrato</dt><dd>{num(usage?.cost_eur, 2)} EUR{usage?.partial ? ' (minimo parziale)' : ''}</dd></div>
            </dl><p>Fonte: contabilità della run. Durata e costo misurano l'attività, non la qualità delle decisioni.</p></section>
          </div>
        </section>
      </div>
      <div className="ap-bottom-grid">
        <section className="ap-lessons"><div className="ap-section-heading"><h2><BookOpen size={18} /> Lezione della run</h2>
          <span>{run ? `Memo #${run.memo_id}` : 'Nessuna run registrata'}</span></div>
          <div className="ap-lesson-status"><span>Suggerimento documentato</span><span>Attuazione: non verificata</span><span>Effetto sulle prestazioni: non dimostrato</span></div>
          {run?.reflection?.text ? <div className="ap-lesson-text">{run.reflection.text}</div> :
            <p className="ap-empty">Nessuna reflection salvata per questa rilevazione. Stato: {run?.reflection?.status || 'n.d.'}.</p>}
          <p className="ap-explainer">{data.method.learning}. Le lezioni riguardano il comitato; il testo può nominare singoli desk.</p>
        </section>
        <section className="ap-history"><div className="ap-section-heading"><h2>Registro delle run</h2><span>Ultime {num(data.runs.length, 0)} registrate</span></div>
          <div className="ap-history-scroll"><table><thead><tr><th>Memo</th><th>Completata</th><th>Hit rate {agent.label}</th><th>Call</th></tr></thead>
            <tbody>{[...agent.series].reverse().map(item => <tr key={item.run_id} className={item.run_id === run?.run_id ? 'is-selected' : ''}>
              <td><button type="button" onClick={() => setRunId(item.run_id!)} aria-label={`Apri dettaglio memo ${item.memo_id}`}>#{item.memo_id}<ArrowUpRight size={12} /></button></td>
              <td>{stamp(item.completed_at)}</td><td>{pct(item.hit_rate_pct)}</td><td>{num(item.n, 0)}</td>
            </tr>)}</tbody></table>{!data.runs.length && <p className="ap-empty">Lo storico non viene ricostruito artificialmente dalle run precedenti.</p>}</div>
        </section>
      </div>
      <details className="ap-method"><summary>Come leggere score e miglioramenti</summary><p>{data.method.score}.</p>
        <p>{data.method.horizon}. Un campione piccolo e intervalli ampi richiedono prudenza.</p><p>{data.method.comparison}.</p>
        <p>Una variazione di hit rate può dipendere dai prezzi e dalla maturazione degli esiti. Non prova da sola che un agente abbia imparato.</p>
        <p>{data.history.note}. L'aggiornamento della pagina legge dati esistenti e non genera analisi a pagamento.</p></details>
    </>}
  </main>;
}
