import { Fragment, useEffect, useMemo, useState, useCallback, useRef } from 'react';
import axios from 'axios';
import { API_BASE } from '@/lib/api';
import { RefreshCw, Radar, TrendingUp, TrendingDown, AlertTriangle, Minus } from 'lucide-react';
import {
  leggiScan, vuotoScan, copyAttesa, esitoChiamata, etaScan, rigaCopertura, durata, oraIt,
  LATENZE_MISURATE, type Segnale, type EsitoChiamata,
} from '@/lib/edge';

// #181 — F13 Edge Scanner: segnali quantitativi oggettivi del portafoglio, ranked.
// L'agente non inventa: parte da QUESTI segnali. Qui il PM li vede ogni giorno.
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

const CAT_LABEL: Record<string, string> = {
  volatility: 'Volatilità', positioning: 'Positioning', momentum: 'Momentum',
  factor: 'Fattori', insider: 'Insider', risk: 'Rischio',
};

const DIR_STYLE: Record<string, { c: string; icon: any }> = {
  bullish: { c: 'text-emerald', icon: TrendingUp },
  bearish: { c: 'text-crimson', icon: TrendingDown },
  caution: { c: 'text-amber', icon: AlertTriangle },
  neutral: { c: 'text-muted', icon: Minus },
};

function strengthColor(s: number): string {
  if (s >= 85) return '#A12B2B';
  if (s >= 65) return '#B07A1E';
  if (s >= 50) return '#5B7997';
  return '#5A6472';
}

/** La risposta buona più recente, con la soglia con cui è stata chiesta: un filtro
 *  fallito non può far passare la lista vecchia per una lista alla soglia nuova.
 *  `ricevutaAlMs` = quando è arrivata al client: l'età del registro invecchia da lì. */
interface Risposta { payload: unknown; soglia: number; ricevutaAlMs: number }

const TONO_VUOTO: Record<string, string> = {
  misurato: 'text-muted border-border',
  parziale: 'text-amber border-amber/50',
  nd: 'text-amber border-amber/50',
  filtro: 'text-muted border-border',
};

const TITOLO_GUASTO: Record<string, string> = {
  payload: 'guasto dichiarato dal backend',
  timeout: 'nessuna risposta entro il limite della pagina',
  chiamata: 'la chiamata non ha avuto risposta valida',
  forma: 'risposta fuori contratto',
};

export default function EdgeScannerPage() {
  const [risposta, setRisposta] = useState<Risposta | null>(null);
  const [loading, setLoading] = useState(true);
  const [forzata, setForzata] = useState(false);
  const [errore, setErrore] = useState<EsitoChiamata | null>(null);
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
  // memoizzato: l'orologio del registro ticka a 1 Hz e senza memo ri-parserebbe
  // l'intero payload (28-32 righe) a ogni secondo (reperto della review 25/08)
  const esito = useMemo(
    () => leggiScan(risposta?.payload, { inCorso: loading, forzata, errore }),
    [risposta, loading, forzata, errore],
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
    axios.get(`${API_BASE}/signals/edge_scan`, { params, timeout: TIMEOUT_MS })
      .then(r => { if (mia === generazione.current) setRisposta({ payload: r.data, soglia: minStrength, ricevutaAlMs: Date.now() }); })
      .catch(e => { if (mia === generazione.current) setErrore(esitoChiamata(e, TIMEOUT_MS)); })
      .finally(() => {
        inVolo.current -= 1;
        if (mia === generazione.current) setLoading(false);
      });
  }, [minStrength]);
  useEffect(() => { load(false); }, [load]);

  const segnali: Segnale[] = (viva?.segnali || []).filter(s => !cat || s.category === cat);
  const cats: string[] = Array.from(new Set((viva?.segnali || []).map(s => s.category)));
  const sogliaResa = risposta?.soglia ?? minStrength;
  const vuoto = viva ? vuotoScan(viva, sogliaResa, CAT_LABEL[cat] || cat, segnali.length) : null;
  const attesaS = loading ? Math.max(0, (adesso - partita.current) / 1000) : 0;
  // E3 · IL REGISTRO (rosa E, scelta PM 25/08): età e copertura sono la STESSA
  // dichiarazione e si leggono per intero senza hover — la nota del backend in chiaro.
  const eta = viva && risposta ? etaScan(viva.eta, risposta.ricevutaAlMs, adesso) : null;
  const copRiga = viva ? rigaCopertura(viva.copertura) : null;
  const oraScan = viva
    ? oraIt((viva.eta.dichiarata && viva.eta.scansioneDelle) || viva.generated || '')
    : null;
  const fonteEta = viva?.eta.dichiarata
    ? (viva.eta.fuoriCache ? 'diretta fuori cache'
        : viva.eta.servitaDaCache ? 'servita dalla cache' : 'fresca')
      + (viva.eta.fuoriCache ? ''
        : viva.eta.ttlS != null
          ? ` (TTL ${durata(viva.eta.ttlS)}${eta?.scaduta ? ' — scaduto: la prossima richiesta può rifare la scansione' : ''})`
          : ' (TTL non dichiarato)')
      + (viva.eta.ttlMotivo ? ` — ${viva.eta.ttlMotivo}` : '')
    : null;
  const costoForza = `${Math.round(LATENZE_MISURATE.caldoS)}-${Math.round(LATENZE_MISURATE.freddoS)} s `
    + `su ${LATENZE_MISURATE.posizioni} posizioni, misurati il ${LATENZE_MISURATE.misurateIl}`;
  const titoloRiprova = 'Richiesta normale (non forzata): se la cache del backend è calda risponde in '
    + `~0,03 s, se una scansione è in corso aspetta che finisca, altrimenti rifà la scansione (${costoForza}). `
    + 'Un guasto non entra in cache.';

  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="font-mono text-amber text-lg font-semibold uppercase tracking-[0.25em] flex items-center gap-2">
          <Radar size={18} /> Edge Scanner
        </span>
        <span className="font-mono text-3xs text-faint uppercase tracking-widest">
          / segnali quantitativi oggettivi · ranked per forza · l'agente parte da qui
        </span>
      </div>

      {/* Controls */}
      <div className="border border-border bg-bg-elev px-3 py-2 flex items-center gap-3 flex-wrap font-mono text-2xs" data-zona="comandi">
        <span className="text-faint uppercase tracking-widest">Soglia forza</span>
        {[30, 45, 60, 75].map(v => (
          <button key={v} onClick={() => setMinStrength(v)}
            title="Filtro a valle della scansione: entro il TTL della cache del backend non riscansiona; se una scansione è in corso, aspetta che finisca"
            className={`px-2.5 py-1 rounded-sm border ${minStrength === v ? 'bg-gold text-bg border-gold font-bold' : 'bg-bg text-muted border-border hover:text-gold'}`}>
            ≥{v}
          </button>
        ))}
        <span className="text-faint uppercase tracking-widest ml-3">Categoria</span>
        <button onClick={() => setCat('')}
          className={`px-2.5 py-1 rounded-sm border ${!cat ? 'bg-navy2 text-white border-steel' : 'bg-bg text-muted border-border hover:text-gold'}`}>
          tutte
        </button>
        {cats.map(c => (
          <button key={c} onClick={() => setCat(c)}
            className={`px-2.5 py-1 rounded-sm border ${cat === c ? 'bg-navy2 text-white border-steel' : 'bg-bg text-muted border-border hover:text-gold'}`}>
            {CAT_LABEL[c] || c}
          </button>
        ))}
        <button onClick={() => load(true)} disabled={loading} data-azione="rifai"
          title={`Re-interroga davvero i rilevatori (parametro force): costo ${costoForza}; se una scansione è già in corso si accoda dietro di essa. I bottoni di soglia, entro il TTL della cache, non riscansionano.`}
          // ⚠️ spento SENZA `opacity-50`: il tasto dimezzato scendeva a 3,12:1 (misurato dal
          // cancello negli stati) — a tasto spento si cambia colore, non si sbiadisce il testo
          className="ml-auto px-3 py-1 rounded-sm border font-bold flex items-center gap-1 bg-gold text-bg border-gold hover:bg-gold/90 disabled:bg-bg-elev disabled:text-muted disabled:border-border disabled:cursor-wait">
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} /> {loading ? durata(attesaS) : 'RIFAI LA SCANSIONE'}
        </button>
      </div>

      {/* E3 · Registro della scansione (impianto scelto dal PM il 25/08 sulla rosa in
          situ): sta sotto i comandi, sopra il sommario, in ogni stato viva. */}
      {viva && eta && copRiga && (
        <div className="border border-border bg-bg-elev px-3 py-2 font-mono" data-zona="registro">
          {/* ⚠️ column-gap INLINE, non `gap-x-3`: il JIT di Tailwind genera solo le
              classi presenti nei sorgenti (lezione della rosa E del 22/08) */}
          {/* il title (la frase intera dell'età) sta sugli span dell'ETÀ, non sull'intera
              riga: hover su un pezzo di copertura non deve rispondere sulla cache */}
          <div className="text-white flex flex-wrap" style={{ fontSize: '12px', columnGap: '12px', rowGap: '2px' }}>
            <span className="text-cyan" title={eta.testo}>● Scansione{oraScan ? ` delle ${oraScan}` : ''}</span>
            {eta.secondi != null && fonteEta ? (
              <>
                <span title={eta.testo}>conclusa {durata(eta.secondi)} fa</span>
                <span className={eta.tono === 'scaduta' ? 'text-amber' : 'text-muted'} title={eta.testo}>{fonteEta}</span>
              </>
            ) : (
              <span className="text-amber">{eta.testo}</span>
            )}
            <span className="text-faint">|</span>
            {copRiga.pezzi.map((p, i) => (
              <Fragment key={i}>
                {i > 0 && <span className="text-faint">·</span>}
                <span className={p.tono === 'allarme' ? 'text-amber' : undefined}>{p.testo}</span>
              </Fragment>
            ))}
          </div>
          {copRiga.nota && (
            <div className="text-2xs text-faint mt-1 leading-relaxed">{copRiga.nota}</div>
          )}
        </div>
      )}

      {/* Summary */}
      {viva && (
        <div className="grid grid-cols-3 gap-2 font-mono text-2xs" data-zona="sommario">
          <div className="border border-border bg-bg-elev px-3 py-2">
            <div className="text-3xs text-faint uppercase tracking-widest">Segnali forti ≥{sogliaResa}</div>
            <div className="text-2xl tabular-nums text-white mt-0.5">
              {viva.illeggibili > 0 ? viva.segnali.length : (viva.nForti ?? viva.segnali.length)}
            </div>
            <div className="text-3xs text-faint">
              {viva.illeggibili > 0
                ? `il backend ne dichiara ${viva.nForti ?? 'n.d.'}: ${viva.illeggibili} illeggibili`
                : (viva.nTotali != null ? `su ${viva.nTotali} totali` : 'totale non dichiarato')}
            </div>
          </div>
          <div className="border border-border bg-bg-elev px-3 py-2 col-span-2">
            <div className="text-3xs text-faint uppercase tracking-widest mb-1">Per categoria</div>
            <div className="flex gap-3 flex-wrap mt-1">
              {Object.entries(viva.perCategoria).map(([k, v]) => (
                <span key={k} className="text-cyan">{CAT_LABEL[k] || k}: <span className="text-white">{v}</span></span>
              ))}
            </div>
          </div>
        </div>
      )}

      {esito.stato === 'attesa' && (
        <div className="panel p-8 text-center text-muted font-mono text-sm flex items-center justify-center gap-2" data-stato="attesa">
          <RefreshCw size={14} className="animate-spin shrink-0" /> {copyAttesa(attesaS, TIMEOUT_MS, esito.forzata)}
        </div>
      )}

      {esito.stato === 'guasto' && (
        <div className="panel p-4 font-mono text-sm border border-crimson/50" data-stato="guasto" data-origine={esito.origine}>
          <div className="text-crimson text-2xs font-bold uppercase tracking-widest">
            {esito.origine === 'timeout' ? 'Scansione senza risposta' : 'Scansione non eseguita'} — {TITOLO_GUASTO[esito.origine]}
          </div>
          <div className="text-white mt-1">{esito.motivo}</div>
          <div className="text-faint text-2xs mt-1">
            {esito.origine === 'payload' && (
              `Il backend ha risposto con successo HTTP ma con un errore nel corpo`
              + `${esito.quando ? ` (timbro del backend: ${oraIt(esito.quando) || esito.quando})` : ''}: `
              + 'i segnali NON sono stati cercati, questo non è uno zero.')}
            {esito.origine === 'timeout' && (
              'La scansione può essere ancora in corso lato backend: questo non è uno zero. '
              + 'RIPROVA la riusa quando è finita; RIFAI si accoderebbe dietro di essa.')}
            {esito.origine === 'chiamata' && 'Nessun risultato ricevuto: questo non è uno zero, è un\'assenza di risposta.'}
            {esito.origine === 'forma' && 'La risposta non ha la forma di una scansione: non si sa se i segnali sono stati cercati.'}
          </div>
          <button onClick={() => load(false)} disabled={loading} title={titoloRiprova} data-azione="riprova"
            className={`mt-2 px-3 py-1 rounded-sm border text-2xs ${esito.origine === 'timeout'
              ? 'bg-gold text-bg border-gold font-bold hover:bg-gold/90'
              : 'border-border text-muted hover:text-gold'}`}>
            RIPROVA
          </button>
        </div>
      )}

      {viva && viva.ultimaChiamataFallita && (
        <div className="panel p-3 font-mono text-2xs border border-amber/50 text-amber flex items-start gap-3 flex-wrap" data-avviso="chiamata-fallita">
          <span className="flex-1 min-w-[240px]">
            L'ultima richiesta è fallita: {viva.ultimaChiamataFallita.motivo}
            {' '}— sotto resta la risposta precedente (soglia ≥{sogliaResa}).
          </span>
          <button onClick={() => load(false)} disabled={loading} title={titoloRiprova} data-azione="riprova"
            className="px-3 py-1 rounded-sm border border-amber/60 text-amber hover:bg-amber/10 shrink-0">
            RIPROVA
          </button>
        </div>
      )}

      {/* Signals list */}
      {viva && (
        <div className="space-y-2" data-zona="lista" data-stato="viva">
          {vuoto && (
            <div className={`panel p-4 font-mono text-sm border ${TONO_VUOTO[vuoto.tono]}`} data-vuoto={vuoto.tono}>
              {vuoto.testo}
            </div>
          )}
          {segnali.map((s, i) => {
            const dir = DIR_STYLE[s.direction] || DIR_STYLE.neutral;
            const Icon = dir.icon;
            return (
              <div key={i} className="border border-border bg-bg-elev hover:border-gold/40 flex">
                {/* strength bar */}
                <div className="w-1.5 shrink-0" style={{ backgroundColor: strengthColor(s.strength) }} />
                <div className="flex-1 px-3 py-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono text-sm font-bold text-gold">{s.ticker}</span>
                    <span className="font-mono text-3xs text-faint uppercase tracking-widest px-1.5 py-0.5 border border-border rounded-sm">
                      {CAT_LABEL[s.category] || s.category}
                    </span>
                    <span className="font-mono text-2xs text-white">{s.name}</span>
                    <span className="font-mono text-2xs text-cyan">{String(s.value)}</span>
                    <span className={`flex items-center gap-1 font-mono text-3xs ${dir.c}`}>
                      <Icon size={11} /> {s.direction}
                    </span>
                    <span className="ml-auto font-mono text-2xs tabular-nums" style={{ color: strengthColor(s.strength) }}>
                      forza {s.strength}
                    </span>
                  </div>
                  <p className="text-2xs text-white/80 mt-1 leading-relaxed">{s.reading}</p>
                  <div className="text-3xs text-faint font-mono mt-0.5">src: {s.source} · {s.context}</div>
                </div>
              </div>
            );
          })}
          {viva.illeggibili > 0 && (
            <div className="text-amber text-2xs font-mono" data-avviso="illeggibili">
              {viva.illeggibili} {viva.illeggibili === 1 ? 'riga' : 'righe'} della lista segnali senza la forma di un segnale: non mostrate (il conteggio in alto le esclude).
            </div>
          )}
        </div>
      )}
      <p className="text-faint text-3xs font-mono text-center">
        Segnali calcolati da dati di mercato reali (signal_engine). Validazione formale via backtest (#148).
        Non costituiscono consulenza finanziaria.
      </p>
    </div>
  );
}
