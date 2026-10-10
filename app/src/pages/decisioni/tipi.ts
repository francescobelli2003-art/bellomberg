import type { Decision, DecisionEvent } from '@/lib/api';
import type { TradeIdeaStorage } from '@/lib/tradeIdeas';
import type { LetturaNumero } from '@/lib/cassa';
import type { Lingua } from '@/i18n/lingua';
import type { Esito, FiltroChiuse, Gruppi, TipoArchivio, Vista } from './logica';

export type Dialogo = { tipo: 'veto' | 'revoca'; id: number } | { tipo: 'hold' } | null;
/** Messaggio dell'ultima azione; il testo si compone alla resa, così segue la lingua della pagina. */
export interface Avviso { errore: boolean; testo: () => string }

export interface DatiDecisioni {
  lingua: Lingua;
  decisions: Decision[];
  gruppi: Gruppi;
  loading: boolean;
  loadErr: string | null;
  avviso: Avviso | null;
  saving: boolean;
  vista: Vista;
  filtro: FiltroChiuse;
  tipoArch: TipoArchivio;
  sel: Decision | null;
  target: number | null;
  targetMancante: boolean;
  stimate: number;
  esito: Esito;
  eseguibile: boolean;
  feedback: string;
  pct: string;
  eur: string;
  letturaPct: LetturaNumero | null;
  letturaEur: LetturaNumero | null;
  suggerimentoFormato: string;
  vetoReason: string;
  nota: string;
  holdAperte: boolean;
  dialogo: Dialogo;
  holdEsclusi: number[];
  eventi: { id: number; lista: DecisionEvent[] | null; err: string | null } | null;
  /** R14 seguito: provenienza Trade Idea non leggibile (diagnosi di GET /decisions), null = letta. */
  tradeIdeaStorage: TradeIdeaStorage | null;
}

export interface AzioniDecisioni {
  vista: (v: Vista) => void;
  filtro: (f: FiltroChiuse) => void;
  tipoArch: (t: TipoArchivio) => void;
  scegli: (id: number) => void;
  vaiA: (d: Decision) => void;
  riprova: () => void;
  esito: (e: Esito) => void;
  feedback: (v: string) => void;
  pct: (v: string) => void;
  eur: (v: string) => void;
  confermaEsito: () => void;
  riapri: () => void;
  vetoReason: (v: string) => void;
  apriDialogo: (d: Dialogo) => void;
  chiudiDialogo: () => void;
  okDialogo: () => void;
  escludiHold: (id: number) => void;
  holdAperte: () => void;
  archivia: (d: Decision, archived: boolean | null) => void;
  chiudiRicerca: (d: Decision) => void;
  riapriRicerca: (d: Decision) => void;
  nota: (v: string) => void;
  inviaNota: () => void;
  collegaTrade: (d: Decision) => void;
  divergenza: (d: Decision) => void;
  apriRicercaCollegata: (d: Decision) => void;
  apriMemo: () => void;
  chiudiAvviso: () => void;
}
