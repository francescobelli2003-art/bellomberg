import { Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import ErrorBoundary from './components/ErrorBoundary';
import LoginGate from './components/LoginGate';
import LinguaGate from './components/LinguaGate';
import Dashboard from './pages/Dashboard';
import MemoArchive from './pages/MemoArchive';
import Chat from './pages/Chat';
import Decisions from './pages/Decisions';
import AgentsLive from './pages/AgentsLive';
import MonteCarloPage from './pages/MonteCarloPage';
import TradeEntryPage from './pages/TradeEntryPage';
import NewsPage from './pages/NewsPage';
import FactorsPage from './pages/FactorsPage';
import PerformancePage from './pages/PerformancePage';
import VolSurfacePage from './pages/VolSurfacePage';
import EdgeScannerPage from './pages/EdgeScannerPage';
import MovementsPage from './pages/MovementsPage';
import MarketPage from './pages/MarketPage';
import WatchlistPage from './pages/WatchlistPage';
import FundamentalsPage from './pages/FundamentalsPage';
import { useNewsAlerts } from './hooks/useNewsAlerts';
import CommandPalette from './components/CommandPalette';
import MandatoGate from './components/MandatoGate';
import MandatoPage from './pages/MandatoPage';
import AgentProgressPage from './pages/AgentProgressPage';
import { PAGE_DESTINATIONS, localizeDestination } from './lib/navigation';
import { useLingua, useT } from './i18n/provider';

const PAGES: Record<string, React.ComponentType> = {
  dashboard: Dashboard, performance: PerformancePage, watchlist: WatchlistPage,
  market: MarketPage, news: NewsPage, fundamentals: FundamentalsPage,
  factors: FactorsPage, montecarlo: MonteCarloPage, vol: VolSurfacePage, edge: EdgeScannerPage,
  chat: Chat, agents: AgentsLive, progress: AgentProgressPage, memos: MemoArchive,
  decisions: Decisions, trades: TradeEntryPage, movements: MovementsPage, mandato: MandatoPage,
};

function AlertsRunner() {
  useNewsAlerts();
  return null;
}

export default function App() {
  const language = useLingua(), t = useT();
  return (
    <ErrorBoundary label={t('shell.app')}>
      <LoginGate>
        <LinguaGate>
        <MandatoGate>
          <AlertsRunner />
          <CommandPalette />
          <Layout>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            {PAGE_DESTINATIONS.map(entry => {
              const Page = PAGES[entry.id];
              return <Route key={entry.id} path={entry.to} element={<ErrorBoundary label={localizeDestination(entry, language).label}><Page /></ErrorBoundary>} />;
            })}
            <Route path="/settings" element={<Navigate to="/dashboard" replace />} />
          </Routes>
          </Layout>
        </MandatoGate>
        </LinguaGate>
      </LoginGate>
    </ErrorBoundary>
  );
}
