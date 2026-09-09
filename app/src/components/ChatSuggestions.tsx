import { useEffect, useRef, useState } from 'react';
import { Bellomberg } from '../lib/api';
import { AGENT_QUESTIONS, portfolioTickers, tickerPrompt } from '../lib/chat-prompts';
import './chat-suggestions.css';

export default function ChatSuggestions({ agent, name, disabled, onPrompt }: {
  agent: string; name: string; disabled: boolean; onPrompt: (prompt: string) => void;
}) {
  const [tickers, setTickers] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const request = useRef(0);
  const refresh = async () => {
    const id = ++request.current;
    setTickers(null); setError(null);
    try {
      const result = await Bellomberg.portfolio();
      const current = portfolioTickers(result.positions);
      if (id === request.current) setTickers(current);
    } catch (e: any) {
      if (id === request.current) setError(e?.response?.data?.detail || e?.message || 'Portafoglio non leggibile');
    }
  };
  useEffect(() => {
    setFilter(''); void refresh();
    return () => { ++request.current; };
  }, [agent]);
  const visible = (tickers || []).filter(ticker => ticker.toLowerCase().includes(filter.trim().toLowerCase()));
  return <div className="chat-suggestions">
    <div className="research-questions">
      {(AGENT_QUESTIONS[agent] || []).map(prompt =>
        <button key={prompt} disabled={disabled} onClick={() => onPrompt(prompt)}>{prompt}</button>)}
    </div>
    <div className="portfolio-question-header">
      <span>Analizza una posizione con <strong>{name}</strong></span>
      <button onClick={() => void refresh()} disabled={tickers === null && !error}>Aggiorna elenco</button>
    </div>
    {error ? <p role="alert" className="portfolio-question-error">Portafoglio non disponibile: {error}</p>
      : tickers === null ? <p role="status">Caricamento delle posizioni…</p>
      : tickers.length === 0 ? <p>Il portafoglio è vuoto. Registra le tue posizioni per analizzarle da qui.</p>
      : <>
        <input aria-label="Filtra ticker del portafoglio" placeholder="Filtra le tue posizioni…"
          value={filter} onChange={e => setFilter(e.target.value)} />
        <div className="portfolio-question-tickers" aria-label="Ticker dal portafoglio">
          {visible.map(ticker => <button key={ticker} disabled={disabled}
            title={`Avvia un'analisi di ${ticker} con ${name}`}
            onClick={() => onPrompt(tickerPrompt(agent, ticker))}>{ticker}</button>)}
          {!visible.length && <span>Nessuna posizione corrisponde al filtro.</span>}
        </div>
        <small>{tickers.length} ticker dal portafoglio corrente. Il clic invia la domanda al desk selezionato.</small>
      </>}
  </div>;
}
