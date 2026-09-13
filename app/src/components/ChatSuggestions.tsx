import { useT } from '@/i18n/provider';
import { useEffect, useRef, useState } from 'react';
import { Bellomberg } from '../lib/api';
import { AGENT_QUESTIONS, portfolioTickers, tickerPrompt } from '../lib/chat-prompts';
import { leggiDetail } from '../lib/quota';
import './chat-suggestions.css';

export default function ChatSuggestions({ agent, name, disabled, onPrompt }: {
  agent: string; name: string; disabled: boolean; onPrompt: (prompt: string) => void;
}) {
  const tr = useT();
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
      if (id === request.current) setError(leggiDetail(e?.response?.data?.detail || e?.message || String(e)));
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
      <span>{tr('communications.analysePosition')} <strong>{name}</strong></span>
      <button onClick={() => void refresh()} disabled={tickers === null && !error}>{tr('communications.refreshList')}</button>
    </div>
    {error ? <p role="alert" className="portfolio-question-error">{tr('communications.portfolioUnavailable')} {error}</p>
      : tickers === null ? <p role="status">{tr('communications.portfolioLoading')}</p>
      : tickers.length === 0 ? <p>{tr('communications.portfolioEmpty')}</p>
      : <>
        <input aria-label={tr('communications.filterTicker')} placeholder={tr('communications.filterPositions')}
          value={filter} onChange={e => setFilter(e.target.value)} />
        <div className="portfolio-question-tickers" aria-label={tr('communications.portfolioTickers')}>
          {visible.map(ticker => <button key={ticker} disabled={disabled}
            title={tr('communications.startTicker', {a: ticker, b: name})}
            onClick={() => onPrompt(tickerPrompt(agent, ticker))}>{ticker}</button>)}
          {!visible.length && <span>{tr('communications.noPositionsMatch')}</span>}
        </div>
        <small>{tr('communications.suggestionsCount', { a: tickers.length })}</small>
      </>}
  </div>;
}
