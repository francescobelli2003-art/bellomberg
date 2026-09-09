import ReactDOM from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import App from './App';
import './index.css';

// Catch global unhandled errors per debugging
window.addEventListener('error', (e) => {
  console.error('[GLOBAL ERROR]', e.error || e.message, e.filename, e.lineno);
});
window.addEventListener('unhandledrejection', (e) => {
  console.error('[UNHANDLED PROMISE REJECTION]', e.reason);
});

// NOTE: StrictMode disabilitato perche' in dev mode esegue gli effects 2 volte
// causando duplicate fetch SSE che raddoppiano il testo nelle chat.
ReactDOM.createRoot(document.getElementById('root')!).render(
  <HashRouter>
    <App />
  </HashRouter>
);
