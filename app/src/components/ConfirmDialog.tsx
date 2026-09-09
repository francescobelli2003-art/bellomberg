import { useEffect, useRef } from 'react';

export type ConfirmRow = { k: string; v: string; tone?: 'amber' | 'crimson' | 'cyan' };

type Props = {
  open: boolean;
  title: string;
  intro?: string;
  rows?: ConfirmRow[];
  warn?: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: 'amber' | 'crimson';
  onConfirm: () => void;
  onCancel: () => void;
};

/**
 * Dialog di conferma per le azioni COSTOSE o IRREVERSIBILI (Lotto D dell'audit,
 * approvato dal PM 26/07). NON esegue nulla e non lancia niente: chiede e basta —
 * chi lo apre decide cosa fare sull'onConfirm. Regole di casa:
 *  - il focus di default sta su ANNULLA: un INVIO di troppo (es. dalla palette,
 *    dove bastava un tasto per spendere ~10-15$) ANNULLA, non conferma;
 *  - ESC annulla e NON propaga, cosi' la palette dietro resta aperta;
 *  - focus trappolato dentro il dialog finche' e' aperto, e restituito a chi l'aveva
 *    alla chiusura.
 */
export default function ConfirmDialog({
  open, title, intro, rows, warn, confirmLabel, cancelLabel = 'ANNULLA',
  tone = 'amber', onConfirm, onCancel,
}: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const prevFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    prevFocus.current = document.activeElement as HTMLElement | null;
    const t = setTimeout(() => cancelRef.current?.focus(), 10);
    return () => {
      clearTimeout(t);
      // il focus torna a chi l'aveva (bottone RUN, input della palette, form F7)
      try { prevFocus.current?.focus(); } catch { /* elemento smontato: nulla da restituire */ }
    };
  }, [open]);

  if (!open) return null;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onCancel(); return; }
    if (e.key !== 'Tab') return;
    // trappola: TAB gira solo fra i pulsanti del dialog
    const f = boxRef.current?.querySelectorAll<HTMLElement>('button:not([disabled])');
    if (!f || f.length === 0) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  return (
    <>
      <div className="cfm-overlay" onClick={onCancel} />
      <div ref={boxRef} className={'cfm-modal ' + (tone === 'crimson' ? 'is-crimson' : 'is-amber')}
           role="alertdialog" aria-modal="true" aria-labelledby="cfm-title" onKeyDown={onKeyDown}>
        <div className="cfm-head" id="cfm-title">{title}</div>
        {intro && <div className="cfm-intro">{intro}</div>}
        {rows && rows.length > 0 && (
          <div className="cfm-rows">
            {rows.map(r => (
              <div className="cfm-row" key={r.k}>
                <span className="k">{r.k}</span>
                <span className={'v' + (r.tone ? ' ' + r.tone : '')}>{r.v}</span>
              </div>
            ))}
          </div>
        )}
        {warn && <div className="cfm-warn">{warn}</div>}
        {/* ANNULLA per primo nel DOM = primo nell'ordine di TAB e primo a prendere il focus */}
        <div className="cfm-actions">
          <button ref={cancelRef} className="cfm-btn no" onClick={onCancel}>{cancelLabel}</button>
          <button className="cfm-btn go" onClick={onConfirm}>{confirmLabel}</button>
        </div>
        <div className="cfm-hint">ESC ANNULLA &nbsp;·&nbsp; TAB CAMBIA PULSANTE &nbsp;·&nbsp; INVIO ATTIVA QUELLO IN FOCUS</div>
      </div>
    </>
  );
}
