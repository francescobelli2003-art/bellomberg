import React from 'react';
import { AlertOctagon, RefreshCw } from 'lucide-react';
import { t } from '@/i18n/t';
import { sottoscriviLingua } from '@/i18n/lingua';

interface State {
  hasError: boolean;
  error: Error | null;
  info: React.ErrorInfo | null;
}

export default class ErrorBoundary extends React.Component<
  { children: React.ReactNode; label?: string },
  State
> {
  state: State = { hasError: false, error: null, info: null };
  private stopLanguage?: () => void;
  componentDidMount() { this.stopLanguage = sottoscriviLingua(() => this.forceUpdate()); }
  componentWillUnmount() { this.stopLanguage?.(); }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, info: null };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] caught:', error, info);
    this.setState({ info });
  }

  reset = () => this.setState({ hasError: false, error: null, info: null });

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="min-h-[50vh] flex items-center justify-center p-4">
        <div className="panel max-w-2xl border-crimson p-5 font-mono text-xs">
          <div className="flex items-center gap-2 mb-3 text-crimson">
            <AlertOctagon size={16} />
            <span className="text-base font-bold uppercase tracking-wider">
              {t('shell.runtime_error')} // {this.props.label || t('shell.component')}
            </span>
          </div>
          <div className="text-text-dim mb-2">
            {t('shell.runtime_note')}
          </div>
          <div className="bg-bg-elev border border-border p-2 mb-3 text-crimson break-words">
            {this.state.error?.message || t('shell.unknown')}
          </div>
          {this.state.error?.stack && (
            <details className="text-faint text-3xs">
              <summary className="cursor-pointer hover:text-text-dim">{t('shell.error_trace')}</summary>
              <pre className="mt-2 overflow-auto max-h-60 whitespace-pre-wrap">
                {this.state.error.stack}
              </pre>
            </details>
          )}
          {this.state.info?.componentStack && (
            <details className="text-faint text-3xs mt-2">
              <summary className="cursor-pointer hover:text-text-dim">{t('shell.component_trace')}</summary>
              <pre className="mt-2 overflow-auto max-h-60 whitespace-pre-wrap">
                {this.state.info.componentStack}
              </pre>
            </details>
          )}
          <button onClick={this.reset} className="btn btn-amber mt-4">
            <RefreshCw size={11} /> {t('shell.retry')}
          </button>
        </div>
      </div>
    );
  }
}
