/* Pagina Decisioni (Nuova, mockup approvato 05/10/2026: outputs/decisioni-nuova/mockup.html).
   Viste Da decidere | In ricerca | Chiuse | Archivio, elenco + dettaglio. Il comportamento resta quello
   di F10: lettura unica di /decisions (limit 500), archivio automatico con override del PM, veto eterno
   con motivo obbligatorio, note di ricerca lette dalla run, ARCHIVIA/RIAPRI delle research = status.
   Tutto lo stato sta qui (viste presentazionali; nuovi useState in coda: i test SSR indicizzano gli hook). */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useT } from '@/i18n/provider';
import { t as ora } from '@/i18n/t';
import { linguaCorrente, type Lingua } from '@/i18n/lingua';
import { leggiDetail } from '@/lib/quota';
import ModernPage from '@/components/ModernPage';
import { Bellomberg, type Decision, type DecisionsResponse } from '@/lib/api';
import { leggiNumeroConSegno } from '@/lib/cassa';
import { caricaLoghi } from '@/lib/loghi-remoti';
import { assessmentAllowsExecution } from '@/lib/trade-entry';
import { esitoPredefinito, isResearch, raggruppa, vistaDi, vociVista, type Esito, type FiltroChiuse, type TipoArchivio, type Vista } from './decisioni/logica';
import VistaDecisioni, { statoTesto } from './decisioni/Vista';
import type { AzioniDecisioni, Avviso, DatiDecisioni, Dialogo } from './decisioni/tipi';
import './decisioni-nuova.css';

// diagnosi di un numero illeggibile riletta nella lingua corrente della pagina
const motivo = (k: 'invalidPct' | 'invalidEur', raw: string, lingua: Lingua) => {
  const r = leggiNumeroConSegno(raw, lingua, linguaCorrente());
  return ora(`decisiondesk.${k}`, { detail: r && !r.ok ? r.motivo : ora('decisiondesk.errorUnknown') });
};
const detail = (error: any) => leggiDetail(error?.response?.data?.detail) || leggiDetail(error?.message) || leggiDetail(error);

export default function Decisions() {
  const tr = useT();
  const navigate = useNavigate();
  // The shell uses HashRouter. Reading the route query here also keeps the
  // decision desk renderable in the existing isolated i18n harness.
  const routeQuery = typeof window !== 'undefined' ? window.location?.hash?.split('?')[1] || '' : '';
  const linkedId = Number(new URLSearchParams(routeQuery).get('decision'));
  const target = Number.isSafeInteger(linkedId) && linkedId > 0 ? linkedId : null;

  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [avviso, setAvviso] = useState<Avviso | null>(null);
  const [vista, setVista] = useState<Vista>('todo');
  const [filtro, setFiltro] = useState<FiltroChiuse>('all');
  const [tipoArch, setTipoArch] = useState<TipoArchivio>('op');
  const [selId, setSelId] = useState<number | null>(target);
  const [esito, setEsito] = useState<Esito | null>(null);
  const [feedback, setFeedback] = useState('');
  const [outcomePct, setOutcomePct] = useState('');
  const [outcomeEur, setOutcomeEur] = useState('');
  const [inputLanguage, setInputLanguage] = useState<Lingua>(linguaCorrente);
  const [vetoReason, setVetoReason] = useState('');
  const [noteDraft, setNoteDraft] = useState<Record<number, string>>({});
  const [holdAperte, setHoldAperte] = useState(false);
  const [dialogo, setDialogo] = useState<Dialogo>(null);
  const [holdEsclusi, setHoldEsclusi] = useState<number[]>([]);
  const [eventi, setEventi] = useState<DatiDecisioni['eventi']>(null);
  const [targetAperto, setTargetAperto] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    // review 16/07: con limit=100 su 170 decisioni, 10 PENDING operative non arrivavano MAI al client.
    // Lo stato si filtra in pagina (vista Chiuse): una sola lettura per tutte le viste.
    return Bellomberg.decisions(undefined, 500)
      .then(r => {
        if (!Array.isArray(r?.decisions)) { setLoadErr(''); return; }
        setLoadErr(null); setDecisions(r.decisions);
        // R14 seguito (B2): un oggetto qualsiasi = provenienza Trade Idea NON letta (il null delle righe non è «nessuna»)
        setTiStorage(r.trade_idea_storage ?? null);
      })
      .catch((e: any) => setLoadErr(detail(e)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const gruppi = useMemo(() => raggruppa(decisions), [decisions]);
  const voci = vociVista(gruppi, vista, filtro, tipoArch);
  const sel = decisions.find(d => d.id === selId) ?? null;
  const selVisibile = !!sel && voci.some(d => d.id === sel.id);
  const selMostrata = selVisibile ? sel : voci[0] ?? null;

  // collegamento ?decision=ID: si apre la vista dove vive la decisione, una volta sola
  useEffect(() => {
    if (loading || loadErr !== null || target == null || targetAperto) return;
    const d = decisions.find(x => x.id === target);
    setTargetAperto(true);
    if (!d) return;
    const v = vistaDi(d);
    setVista(v);
    if (v === 'arch') setTipoArch(isResearch(d) ? 'res' : 'op');
    if (v === 'closed') setFiltro('all');
    if (v === 'todo' && (d.action || '').toUpperCase() === 'HOLD') setHoldAperte(true);
    setSelId(d.id);
    const timer = setTimeout(() => document.getElementById(`decision-${d.id}`)?.scrollIntoView({ block: 'center' }), 60);
    return () => clearTimeout(timer);
  }, [decisions, loading, loadErr, target, targetAperto]);

  // cronologia (registro eventi) della proposta operativa aperta: sola lettura
  const idCronologia = selMostrata && !isResearch(selMostrata) ? selMostrata.id : null;
  useEffect(() => {
    let vivo = true;
    if (idCronologia == null) return () => { vivo = false; };
    setEventi({ id: idCronologia, lista: null, err: null });
    const leggi = Bellomberg.decisionEvents;
    if (typeof leggi !== 'function') return () => { vivo = false; };
    leggi(idCronologia).then(r => { if (vivo) setEventi({ id: idCronologia, lista: Array.isArray(r?.events) ? r.events : [], err: null }); })
      .catch(e => { if (vivo) setEventi({ id: idCronologia, lista: null, err: detail(e) || tr('decisiondesk.errorUnknown') }); });
    return () => { vivo = false; };
  }, [idCronologia, decisions]);

  // loghi delle società (stesso store di Centro di comando e Mercati): una richiesta per i ticker nuovi
  const tickerLoghi = [...new Set(decisions.map(d => (d.ticker || '').toUpperCase()).filter(Boolean))].sort().join(',');
  useEffect(() => { if (tickerLoghi) caricaLoghi(tickerLoghi.split(',')); }, [tickerLoghi]);

  // ⚠ Outcome scritti a mano: l'app gira in en-GB e su <input type="number"> la virgola veniva
  // CANCELLATA senza badInput (12,5 → 125 nel DB). Lettura come F7 (lib/cassa.ts), variante col segno.
  const letturaPct = useMemo(() => leggiNumeroConSegno(outcomePct, inputLanguage, linguaCorrente()), [outcomePct, inputLanguage, tr]);
  const letturaEur = useMemo(() => leggiNumeroConSegno(outcomeEur, inputLanguage, linguaCorrente()), [outcomeEur, inputLanguage, tr]);
  // 05/10 (Opus 5.5): la bozza (esito, feedback, risultati, motivo del veto) appartiene alla decisione per cui
  // e' stata scritta. Se il dettaglio passa da solo a un'altra voce (filtro, veto, archivia) la bozza non si
  // mostra, si azzera e un Conferma che arrivasse lo stesso viene rifiutato. Hook in coda (test SSR per posizione).
  const [bozzaDi, setBozzaDi] = useState<number | null>(null);
  // R14 seguito (B2, Opus 5.5): diagnosi `trade_idea_storage` di GET /decisions. Hook in coda (test SSR per posizione).
  const [tiStorage, setTiStorage] = useState<DecisionsResponse['trade_idea_storage']>(null);
  const bozzaAltrui = bozzaDi != null && bozzaDi !== (selMostrata?.id ?? null);
  useEffect(() => { if (bozzaAltrui) { azzeraBozza(); } }, [bozzaAltrui]);
  const eseguibile = !!selMostrata && assessmentAllowsExecution(selMostrata);
  const esitoEffettivo: Esito = selMostrata
    ? (esito && !bozzaAltrui && (eseguibile || (esito !== 'EXECUTED' && esito !== 'PARTIAL')) ? esito : esitoPredefinito(selMostrata, eseguibile))
    : 'EXECUTED';

  const azzeraBozza = () => {
    setEsito(null); setFeedback(''); setOutcomePct(''); setOutcomeEur(''); setVetoReason('');
    setInputLanguage(linguaCorrente()); setBozzaDi(null);
  };
  const perBozza = <T,>(f: (v: T) => void) => (v: T) => { setBozzaDi(selMostrata ? selMostrata.id : null); f(v); };
  const bozzaDiAltri = (id: number) => {
    if (bozzaDi == null || bozzaDi === id) return false;
    azzeraBozza(); setAvviso({ errore: true, testo: () => ora('decisiondesk.draftOther', { id }) }); return true;
  };
  const scegli = (id: number) => { if (id !== selMostrata?.id) azzeraBozza(); setSelId(id); };

  // i messaggi si compongono alla resa (lingua corrente), non al momento dell'azione
  const esegui = async (fn: () => Promise<Avviso | null>, errore: (detail: string) => string) => {
    setSaving(true); setAvviso(null);
    try { const out = await fn(); if (out) setAvviso(out); await load(); }
    catch (e: any) { const why = detail(e); setAvviso({ errore: true, testo: () => errore(why || ora('decisiondesk.errorUnknown')) }); }
    finally { setSaving(false); }
  };
  const ok = (testo: () => string): Avviso => ({ errore: false, testo });

  const setStatus = (d: Decision, status: string) => {
    if (bozzaDiAltri(d.id)) return;
    // un outcome illeggibile non si scarta in silenzio: blocca e spiega
    if (letturaPct && !letturaPct.ok) { const raw = outcomePct, l = inputLanguage; setAvviso({ errore: true, testo: () => motivo('invalidPct', raw, l) }); return; }
    if (letturaEur && !letturaEur.ok) { const raw = outcomeEur, l = inputLanguage; setAvviso({ errore: true, testo: () => motivo('invalidEur', raw, l) }); return; }
    return esegui(async () => {
      const body: Record<string, unknown> = { status };
      if (feedback.trim()) body.pm_feedback = feedback.trim();
      if (letturaPct && letturaPct.ok) body.outcome_pct = letturaPct.valore;
      if (letturaEur && letturaEur.ok) body.outcome_eur = letturaEur.valore;
      await Bellomberg.updateDecision(d.id, body);
      azzeraBozza();
      return ok(() => ora('decisiondesk.updated', { id: d.id, status: statoTesto(status).toLowerCase() }));
    }, x => ora('decisiondesk.error', { detail: x }));
  };

  const azioni: AzioniDecisioni = {
    vista: v => { setVista(v); azzeraBozza(); setSelId(null); },
    filtro: f => setFiltro(f),
    tipoArch: t => { setTipoArch(t); setSelId(null); },
    scegli,
    vaiA: d => {
      const v = vistaDi(d);
      setVista(v);
      if (v === 'arch') setTipoArch(isResearch(d) ? 'res' : 'op');
      if (v === 'closed') setFiltro('all');
      if (v === 'todo' && (d.action || '').toUpperCase() === 'HOLD') setHoldAperte(true);
      scegli(d.id);
    },
    riprova: () => { load(); },
    esito: perBozza((e: Esito) => setEsito(e)),
    feedback: perBozza(setFeedback),
    pct: perBozza(setOutcomePct),
    eur: perBozza(setOutcomeEur),
    confermaEsito: () => selMostrata ? setStatus(selMostrata, esitoEffettivo) : undefined,
    riapri: () => selMostrata ? setStatus(selMostrata, 'PENDING') : undefined,
    vetoReason: perBozza(setVetoReason),
    apriDialogo: d => { setDialogo(d); if (d?.tipo === 'hold') setHoldEsclusi([]); },
    chiudiDialogo: () => setDialogo(null),
    okDialogo: () => {
      const dlg = dialogo;
      if (!dlg) return;
      setDialogo(null);
      const d = 'id' in dlg ? decisions.find(x => x.id === dlg.id) : null;
      if (dlg.tipo === 'veto' && d) {
        // F10 opzione A: veto eterno (SKIPPED + flag; il canale della run lo legge per sempre)
        if (bozzaDiAltri(d.id)) return;
        const motivo = vetoReason.trim();
        if (!motivo) return;
        return esegui(async () => { await Bellomberg.vetoDecision(d.id, motivo); setVetoReason(''); return ok(() => ora('decisiondesk.vetoActive', { id: d.id })); },
          x => ora('decisiondesk.vetoError', { detail: x }));
      }
      if (dlg.tipo === 'revoca' && d) return esegui(async () => { await Bellomberg.revokeDecisionVeto(d.id); return ok(() => ora('decisiondesk.vetoRevoked', { id: d.id })); },
        x => ora('decisiondesk.revokeError', { detail: x }));
      if (dlg.tipo === 'hold') {
        // nessuna operazione di massa nel backend: una conferma alla volta, gli errori si dichiarano tutti
        const lista = gruppi.conferme.filter(x => !holdEsclusi.includes(x.id));
        return esegui(async () => {
          const falliti: string[] = [];
          for (const x of lista) {
            try { await Bellomberg.updateDecision(x.id, { status: 'EXECUTED' }); }
            catch (e: any) { falliti.push(`${x.ticker} (${detail(e) || ora('decisiondesk.errorUnknown')})`); }
          }
          if (falliti.length) return { errore: true, testo: () => ora('decisiondesk.holdPartial', { ok: lista.length - falliti.length, n: lista.length, failed: falliti.join(', ') }) };
          return ok(() => ora(lista.length === 1 ? 'decisiondesk.holdDone_one' : 'decisiondesk.holdDone_other', { n: lista.length }));
        }, x => ora('decisiondesk.error', { detail: x }));
      }
    },
    escludiHold: id => setHoldEsclusi(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]),
    holdAperte: () => setHoldAperte(v => !v),
    // F10-C: archivia / riporta in pagina — persistente, vince sull'automatico (null = di nuovo automatico)
    archivia: (d, archived) => esegui(async () => {
      await Bellomberg.setDecisionArchive(d.id, archived);
      return ok(() => ora(archived === null ? 'decisiondesk.archiveAuto' : archived ? 'decisiondesk.archived' : 'decisiondesk.restored', { id: d.id }));
    }, x => ora('decisiondesk.archiveError', { detail: x })),
    // decisione PM 17/07 (opzione B): sulle RESEARCH ARCHIVIA chiude DAVVERO (EXPIRED: la run la molla),
    // RIPORTA riapre DAVVERO (PENDING); l'override si azzera per lasciare comandare lo status.
    chiudiRicerca: d => esegui(async () => {
      await Bellomberg.updateDecision(d.id, { status: 'EXPIRED' });
      if (d.archive_override != null) await Bellomberg.setDecisionArchive(d.id, null);
      return ok(() => ora('decisiondesk.researchClosed', { id: d.id }));
    }, x => ora('decisiondesk.error', { detail: x })),
    riapriRicerca: d => esegui(async () => {
      await Bellomberg.updateDecision(d.id, { status: 'PENDING' });
      if (d.archive_override != null) await Bellomberg.setDecisionArchive(d.id, null);
      return ok(() => ora('decisiondesk.researchReopened', { id: d.id }));
    }, x => ora('decisiondesk.error', { detail: x })),
    nota: v => { if (selMostrata) { const id = selMostrata.id; setNoteDraft(prev => ({ ...prev, [id]: v })); } },
    inviaNota: () => {
      const d = selMostrata;
      const testo = d ? (noteDraft[d.id] || '').trim() : '';
      if (!d || !testo || saving) return;
      return esegui(async () => { await Bellomberg.addDecisionNote(d.id, testo); setNoteDraft(prev => ({ ...prev, [d.id]: '' })); return null; },
        x => ora('decisiondesk.noteError', { detail: x }));
    },
    collegaTrade: d => navigate(`/trades?decision=${d.id}`),
    divergenza: d => navigate(`/trades?divergence=${d.id}`),
    apriRicercaCollegata: d => d.trade_idea && navigate(`/agents/trade-idea?run=${encodeURIComponent(d.trade_idea.run_id)}`),
    apriMemo: () => navigate('/memos'),
    chiudiAvviso: () => setAvviso(null),
  };

  const dati: DatiDecisioni = {
    lingua: linguaCorrente(), decisions, gruppi, loading, loadErr, avviso, saving, vista, filtro, tipoArch,
    sel: selMostrata, target,
    targetMancante: target != null && !loading && loadErr === null && !decisions.some(d => d.id === target),
    stimate: decisions.filter(d => d.archive_override == null && typeof d.archived !== 'boolean').length,
    esito: esitoEffettivo, eseguibile, feedback: bozzaAltrui ? '' : feedback, pct: bozzaAltrui ? '' : outcomePct, eur: bozzaAltrui ? '' : outcomeEur,
    letturaPct: bozzaAltrui ? null : letturaPct, letturaEur: bozzaAltrui ? null : letturaEur,
    suggerimentoFormato: tr(inputLanguage === 'it' ? 'decisiondesk.inputIt' : 'decisiondesk.inputEn'),
    vetoReason: bozzaAltrui ? '' : vetoReason, nota: selMostrata ? noteDraft[selMostrata.id] || '' : '', holdAperte, dialogo, holdEsclusi, eventi,
    tradeIdeaStorage: loadErr === null ? tiStorage ?? null : null,
  };
  return <ModernPage page="decisions" render={() => <VistaDecisioni d={dati} a={azioni} />} />;
}

