import type { AgentsLiveState, EsitoRun } from '@/lib/api';

/* Esito della run per la pagina (06/10/2026): lo dichiara il backend in `esito_run`.
   Titolo, pastiglia e «memo consegnato» vengono da qui, mai da `running` + `memo_id`:
   il memo_id esiste dal primo secondo della run, anche su una run che poi si ferma.
   Senza `esito_run` (backend vecchio) una run viva resta viva — `running` e' il primo
   criterio anche del backend — e una chiusa ha esito NON dichiarato, mai «completata». */

export type Esito = {
  stato: string;
  /** true solo se il backend lo afferma; false = nessun memo; null = non misurabile */
  memo: boolean | null;
  motivo: string | null;
  ripresaDisponibile: boolean;
  fonte: string;
  /** la run e' attiva (in avvio o in corso), anche se l'heartbeat e' ancora della precedente */
  attiva: boolean;
  /** la run e' chiusa: c'e' un esito da mostrare (anche «non dichiarato») */
  chiusa: boolean;
  /** heartbeat `running: true` smentito: processo morto o fermato, oppure (fonte
   *  `processo_vivo`) heartbeat della run PRECEDENTE mentre la nuova parte */
  smentita: boolean;
  /** run dichiarata in corso ma senza pid e senza processi noti (fonte `processo_non_verificabile`):
   *  nessuno conferma che lavori, quindi nessun desk «al lavoro» e nessun orologio che corre */
  nonConfermata: boolean;
};

const ATTIVI = new Set(['in_corso', 'in_corso_senza_segnale']);

export function leggiEsito(state: AgentsLiveState | null | undefined): Esito | null {
  if (!state) return null;
  const e: EsitoRun | undefined = state.esito_run && typeof state.esito_run === 'object' ? state.esito_run : undefined;
  if (!e || typeof e.stato !== 'string') {
    const viva = state.running === true;
    return { stato: viva ? 'in_corso' : 'sconosciuta', memo: viva ? false : null, motivo: null, ripresaDisponibile: false,
      fonte: 'assente', attiva: viva, chiusa: !viva && !!state.start_time, smentita: false, nonConfermata: false };
  }
  const attiva = ATTIVI.has(e.stato);
  return {
    stato: e.stato,
    memo: e.memo_consegnato === true ? true : e.memo_consegnato === false ? false : null,
    motivo: typeof e.motivo === 'string' && e.motivo.trim() ? e.motivo.trim() : null,
    ripresaDisponibile: e.ripresa_disponibile === true,
    fonte: e.fonte || 'nessuna',
    attiva,
    chiusa: !attiva && e.stato !== 'nessuna_run',
    // review PR #19: in avvio il file e' della run precedente anche se dice running:true
    // (run precedente morta senza chiudere): quei desk non sono al lavoro
    smentita: state.running === true && (!attiva || e.fonte === 'processo_vivo'),
    nonConfermata: e.stato === 'in_corso_senza_segnale' && e.fonte === 'processo_non_verificabile',
  };
}

/** Lo stato che la pagina disegna: se il backend smentisce `running` (processo morto,
 *  run fermata) i desk non risultano piu' al lavoro. Stesso oggetto quando non serve. */
export function statoEffettivo(state: AgentsLiveState | null, esito: Esito | null): AgentsLiveState | null {
  return state && esito?.smentita ? { ...state, running: false } : state;
}
