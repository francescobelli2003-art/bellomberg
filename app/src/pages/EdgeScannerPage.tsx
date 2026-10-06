import { useT } from '@/i18n/provider';
import ModernPage from '@/components/ModernPage';
import { localizePayload } from '@/lib/api-presentation';
import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import axios from 'axios';
import { API_BASE, requestHeaders } from '@/lib/api';
import { caricaLoghi } from '@/lib/loghi-remoti';
import {
  leggiScan, vuotoScan, esitoChiamata, etaScan, oraIt,
} from '@/lib/edge';
import { chiaveSegnale, leggiDiagnosi, nomeCategoria } from './ricerca/calcoli';
import VistaRicerca, { type AzioniRicerca, type DatiRicerca, type Diagnosi } from './ricerca/VistaRicerca';
import '@/components/nuova/nuova.css';
import './ricerca-nuova.css';

// #181 — F13 Edge Scanner: segnali quantitativi oggettivi del portafoglio, ranked.
// L'agente non inventa: parte da QUESTI segnali. Qui il PM li vede ogni giorno.
//
// 05/10/2026 · stile Nuova (mockup approvato `outputs/ricerca-opportunita-nuova/mockup.html`):
// Mappa della forza | Copertura in alto, Segnali | Dettaglio fisso sotto. Qui restano lo stato e
// le chiamate, la vista (`ricerca/VistaRicerca.tsx`) è solo presentazione.
//
// 22/08 (F42, ponte 22/08-2/-3 della chat backend): la pagina non interpreta più il
// payload per conto suo — legge l'esito di `lib/edge.ts` (attesa / guasto / viva, e
// tre zeri diversi). Un `{"error"}` servito con HTTP 200 è un GUASTO col motivo,
// non «Nessun segnale sopra la soglia» (audit/23, ALTO, curato qui). Il timeout della
// pagina ha una voce sua: il backend continua a scansionare, e il tasto giusto dopo
// è RIPROVA (riusa la scansione quando finisce), non RIFAI (si accoderebbe).

// ⚠️ TIMEOUT: 420 s = impianto T1 scelto dal PM il 25/08 (rosa del 22/08): il freddo
// misurato 381,4 s +10% ≈ 420, e copre anche la coda sul lock (384 s). Due scansioni
// forzate in coda (~763 s) possono sforare: è la debolezza DICHIARATA di T1 — il
// pannello del timeout dice che il backend continua, e RIPROVA riusa la scansione.
const TIMEOUT_MS = 420000;
// La diagnosi rifà i rilevatori su UN titolo (Polygon, yfinance, Quiver): niente cache.
const TIMEOUT_DIAGNOSI_MS = 180000;

/** La risposta buona più recente, con la soglia con cui è stata chiesta: un filtro
 *  fallito non può far passare la lista vecchia per una lista alla soglia nuova.
 *  `ricevutaAlMs` = quando è arrivata al client: l'età del registro invecchia da lì. */
interface Risposta { payload: unknown; soglia: number; ricevutaAlMs: number }

export default function EdgeScannerPage() {
  const tr = useT();
  const [risposta, setRisposta] = useState<Risposta | null>(null);
  const [loading, setLoading] = useState(true);
  const [forzata, setForzata] = useState(false);
  const [erroreRaw, setErrore] = useState<unknown | null>(null);
  const [minStrength, setMinStrength] = useState(45);
  const [cat, setCat] = useState<string>('');
  // l'orologio della pagina: conta l'attesa E fa invecchiare l'età del registro (E3,
  // scelta PM 25/08) — gira solo quando uno dei due lo consuma
  const [adesso, setAdesso] = useState(() => Date.now());
  const partita = useRef(Date.now());
  // ⚠️ le chiamate in volo si CONTANO: un click di soglia durante una scansione fredda
  // apre una seconda GET (il backend le serializza sul lock), e la prima che esce (o va
  // in timeout) non deve spegnere l'attesa né sovrascrivere l'ultima chiesta.
  const generazione = useRef(0);
  const inVolo = useRef(0);
  // stato della vista Nuova (05/10): in coda, così gli indici degli hook di prima non cambiano
  const [selK, setSelK] = useState<string | null>(null);
  const [copertura, setCopertura] = useState(false);
  const [diagnosi, setDiagnosi] = useState<Diagnosi | null>(null);
  const genDiagnosi = useRef(0);

  const payload = useMemo(() => localizePayload(risposta?.payload), [risposta, tr]);
  const errore = useMemo(() => erroreRaw === null ? null : esitoChiamata(erroreRaw, TIMEOUT_MS), [erroreRaw, tr]);
  // memoizzato: l'orologio del registro ticka a 1 Hz e senza memo ri-parserebbe
  // l'intero payload (28-32 righe) a ogni secondo (reperto della review 25/08)
  const esito = useMemo(
    () => leggiScan(payload, { inCorso: loading, forzata, errore }),
    [payload, loading, forzata, errore, tr],
  );
  const viva = esito.stato === 'viva' ? esito : null;
  const etaInvecchia = viva?.eta.dichiarata === true;
  useEffect(() => {
    if (!loading && !etaInvecchia) return;
    const id = setInterval(() => setAdesso(Date.now()), 1000);
    return () => clearInterval(id);
  }, [loading, etaInvecchia]);

  const load = useCallback((force = false) => {
    const mia = ++generazione.current;
    if (inVolo.current === 0) partita.current = Date.now();   // l'attesa conta dalla PRIMA richiesta senza risposta
    inVolo.current += 1;
    setLoading(true); setErrore(null); setForzata(force); setAdesso(Date.now());
    const params: Record<string, unknown> = { min_strength: minStrength };
    // ⚠️ `force=true` SOLO dal tasto «RIFAI»: i bottoni di soglia sono un filtro a
    // valle della scansione (entro il TTL della cache costano ~0,03 s). Un force
    // mascherato da filtro costerebbe 165-381 s a ogni click (ponte 22/08-3, punto 3).
    if (force) params.force = true;
    axios.get(`${API_BASE}/signals/edge_scan`, { params, timeout: TIMEOUT_MS, headers: requestHeaders() })
      .then(r => { if (mia === generazione.current) setRisposta({ payload: r.data, soglia: minStrength, ricevutaAlMs: Date.now() }); })
      .catch(e => { if (mia === generazione.current) setErrore(e); })
      .finally(() => {
        inVolo.current -= 1;
        if (mia === generazione.current) setLoading(false);
      });
  }, [minStrength]);
  useEffect(() => { load(false); }, [load]);

  // Esc chiude il dialogo della copertura anche col fuoco fuori; il fuoco (entrata, giro di TAB,
  // ritorno a chi l'ha aperto) lo gestisce usaFocusPannello in VistaRicerca
  useEffect(() => {
    if (!copertura) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setCopertura(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [copertura]);

  const tutti = useMemo(() => (viva?.segnali || []).map((s, i) => ({ s, k: chiaveSegnale(s, i) })), [viva]);
  const tickerLoghi = useMemo(() => [...new Set(tutti.map(x => x.s.ticker))].sort().join(','), [tutti]);
  useEffect(() => { if (tickerLoghi) caricaLoghi(tickerLoghi.split(',')); }, [tickerLoghi]);

  const righe = tutti.filter(x => !cat || x.s.category === cat);
  const sogliaResa = risposta?.soglia ?? minStrength;
  const vuoto = viva ? vuotoScan(viva, sogliaResa, nomeCategoria(cat, k => tr(k)), righe.length) : null;
  const sel = righe.find(x => x.k === selK) ?? righe[0] ?? null;
  const attesaS = loading ? Math.max(0, (adesso - partita.current) / 1000) : 0;
  // E3 · IL REGISTRO (rosa E, scelta PM 25/08): età e copertura sono la STESSA
  // dichiarazione — l'età in chiaro nella testata, la copertura nella sua card.
  const eta = viva && risposta ? etaScan(viva.eta, risposta.ricevutaAlMs, adesso) : null;
  const oraScan = viva
    ? oraIt((viva.eta.dichiarata && viva.eta.scansioneDelle) || viva.generated || '')
    : null;

  const diagnostica = useCallback((ticker: string) => {
    const mia = ++genDiagnosi.current;
    setDiagnosi({ ticker, stato: 'attesa' });
    axios.get(`${API_BASE}/signals/position_doctor/${encodeURIComponent(ticker)}`, { timeout: TIMEOUT_DIAGNOSI_MS, headers: requestHeaders() })
      .then(r => {
        if (mia !== genDiagnosi.current) return;
        setDiagnosi(leggiDiagnosi(localizePayload(r.data), ticker));
      })
      .catch(e => { if (mia === genDiagnosi.current) setDiagnosi({ ticker, stato: 'errore', motivo: esitoChiamata(e, TIMEOUT_DIAGNOSI_MS).motivo }); });
  }, [tr]);

  const dati: DatiRicerca = {
    esito, viva, errore, loading, forzata: esito.stato === 'attesa' && esito.forzata, attesaS, timeoutMs: TIMEOUT_MS,
    minStrength, sogliaResa, cat, tutti, righe, sel, vuoto, eta, oraScan, copertura, diagnosi,
  };
  const azioni: AzioniRicerca = {
    soglia: v => setMinStrength(v),
    categoria: c => setCat(c),
    scegli: k => {
      const x = tutti.find(t => t.k === k);
      if (x && cat && x.s.category !== cat) setCat('');
      setSelK(k);
    },
    rifai: () => load(true),
    riprova: () => load(false),
    copertura: aperta => setCopertura(aperta),
    diagnosi: diagnostica,
    apriMercati: ticker => {
      try { sessionStorage.setItem('bb:mktTicker', ticker); } catch { /* il ticker resta da cercare in Mercati */ }
      window.location.hash = '#/market';
    },
  };

  return <ModernPage page="edge" render={() => <VistaRicerca d={dati} a={azioni} />} />;
}
