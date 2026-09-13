import { useT } from '@/i18n/provider';
import { useEffect, useState } from 'react';
import ConfirmDialog, { ConfirmRow } from './ConfirmDialog';
import { Bellomberg } from '../lib/api';
import { fmtEUR } from '../lib/format';

type Props = { open: boolean; onConfirm: () => void; onCancel: () => void };
type CostReading = { kind: 'loading' } | { kind: 'missing' }
  | { kind: 'measured'; value: number; partial: boolean }
  | { kind: 'aggregation' | 'heartbeat'; detail: string | null };

/**
 * Conferma UNICA per i 3 punti d'ingresso della run del consigliere — F1 Dashboard,
 * F4 Agents Live, palette CTRL+K (dove bastava un INVIO). Un solo testo, una sola verita'.
 *
 * Il costo NON e' inventato: all'apertura legge l'heartbeat /agents/live e mostra il
 * costo MISURATO dell'ultima run (usage_total.cost_eur, gia' in EUR dal backend).
 * Se la misura non c'e' o l'heartbeat e' irraggiungibile il buco e' DICHIARATO
 * (regola 14/07: "nessuna misura" non e' "costo zero"). L'ordine di grandezza resta
 * come riga a parte, etichettata stima.
 *
 * In piu': se una run risulta GIA' ATTIVA, il dialog cambia tono e lo dice — lanciarne
 * una seconda spende due volte.
 */
export default function RunConfirmDialog({ open, onConfirm, onCancel }: Props) {
  const tr = useT();
  const [cost, setCost] = useState<CostReading>({ kind: 'loading' });
  const [costTone, setCostTone] = useState<ConfirmRow['tone']>(undefined);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setCost({ kind: 'loading' }); setCostTone(undefined); setRunning(false);
    Bellomberg.agentsLive().then(s => {
      if (!alive) return;
      setRunning(!!s?.running);
      const u = s?.usage_total;
      if (u?.error) {
        setCost({ kind: 'aggregation', detail: String(u.error) }); setCostTone('crimson');
      } else if (u && u.cost_eur != null) {
        setCost({ kind: 'measured', value: u.cost_eur, partial: !!u.partial });
        setCostTone('amber');
      } else {
        setCost({ kind: 'missing' });
      }
    }).catch(e => {
      if (!alive) return;
      setCost({ kind: 'heartbeat', detail: e?.message ? String(e.message) : null });
      setCostTone('crimson');
    });
    return () => { alive = false; };
  }, [open]);

  const costText = cost.kind === 'loading' ? tr('communications.costLoading')
    : cost.kind === 'missing' ? tr('communications.costMissing')
    : cost.kind === 'measured' ? fmtEUR(cost.value) + (cost.partial ? tr('communications.costMinimum') : '')
    : tr(cost.kind === 'aggregation' ? 'communications.costError' : 'communications.heartbeatError',
      { a: cost.detail ?? tr('communications.unknownError') });
  const rows: ConfirmRow[] = [
    { k: running ? tr('communications.costRunning') : tr('communications.costLast'), v: costText, tone: costTone },
    { k: tr('communications.costMagnitude'), v: tr('communications.costEstimate') },
    { k: tr('communications.duration'), v: tr('communications.runDuration') },
  ];

  return (
    <ConfirmDialog
      open={open}
      tone={running ? 'crimson' : 'amber'}
      title={running ? tr('communications.runAlreadyActive') : tr('communications.runLaunchQuestion')}
      intro={tr('communications.runIntro')}
      rows={rows}
      warn={running
        ? tr('communications.runActiveWarning')
        : undefined}
      confirmLabel={running ? tr('communications.launchAnyway') : tr('communications.launchRun')}
      cancelLabel={tr('communications.cancel')}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
