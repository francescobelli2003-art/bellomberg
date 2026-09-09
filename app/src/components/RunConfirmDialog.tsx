import { useEffect, useState } from 'react';
import ConfirmDialog, { ConfirmRow } from './ConfirmDialog';
import { Bellomberg } from '../lib/api';
import { fmtEUR } from '../lib/format';

type Props = { open: boolean; onConfirm: () => void; onCancel: () => void };

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
  const [cost, setCost] = useState('misura in corso...');
  const [costTone, setCostTone] = useState<ConfirmRow['tone']>(undefined);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setCost('misura in corso...'); setCostTone(undefined); setRunning(false);
    Bellomberg.agentsLive().then(s => {
      if (!alive) return;
      setRunning(!!s?.running);
      const u = s?.usage_total;
      if (u?.error) {
        setCost('n.d. — aggregazione costi in errore: ' + u.error); setCostTone('crimson');
      } else if (u && u.cost_eur != null) {
        setCost(fmtEUR(u.cost_eur) + (u.partial ? ' (MINIMO: round non prezzabili)' : ''));
        setCostTone('amber');
      } else {
        setCost('n.d. — nessun costo misurato nell’heartbeat');
      }
    }).catch(e => {
      if (!alive) return;
      setCost('n.d. — heartbeat non raggiungibile (' + (e?.message || 'errore') + ')');
      setCostTone('crimson');
    });
    return () => { alive = false; };
  }, [open]);

  const rows: ConfirmRow[] = [
    { k: running ? 'Costo run in corso' : 'Costo ultima run', v: cost, tone: costTone },
    { k: 'Ordine di grandezza', v: '~10-15 $ di API per run (stima storica, non una misura)' },
    { k: 'Durata', v: '25-40 min · email a fine run · avanzamento in F4' },
  ];

  return (
    <ConfirmDialog
      open={open}
      tone={running ? 'crimson' : 'amber'}
      title={running ? "Una run e' gia' in corso" : 'Lanciare la run del consigliere?'}
      intro="Avvia il comitato multi-agente sul portafoglio. Spende API a ogni lancio e fermarla a meta' da F4 perde i risultati parziali."
      rows={rows}
      warn={running
        ? "L'heartbeat dice che una run e' ATTIVA adesso: lanciarne un'altra spende una seconda volta. Controlla F4 prima di confermare."
        : undefined}
      confirmLabel={running ? 'LANCIA COMUNQUE' : 'LANCIA LA RUN'}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
