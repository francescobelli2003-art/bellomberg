import { optionLabel, errorForLanguage, fieldLabel, sectionLabel, sectionStatus, coverageLabel, showNotice, type Notice } from '@/lib/mandato-presentation';
import { useLingua, useT } from '@/i18n/provider';
import { eLingua, linguaCorrente, localeDi, type Lingua } from '@/i18n/lingua';
import { t as tr } from '@/i18n/t';
import { localizePayload } from '@/lib/api-presentation';
import type { MutableRefObject, ReactNode } from 'react';
import { AlertCircle, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Cpu, FileText, HelpCircle, History, Info, Lock, MessageSquare, MoreHorizontal, Plus, ShieldCheck, Wand2, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import ModernPage from '@/components/ModernPage';
import NewInterfaceBoundary from '@/components/NewInterfaceBoundary';
import {
  BLOCCHI_MANDATO, eliminaBozza, salvaBozza, leggiBozza, coperturaCampo, consumaDettaglioRun, RIFIUTO_RUN_EVENT,
  dettaglioLeggibile, leggiNumeroMandato, rispostaDellaRevisione,
  type AnteprimaMandato, type CampoMandato, type StatoMandato, type ValoriMandato,
} from '@/lib/mandato';
import '@/components/nuova/nuova.css';
import './mandato.css';
import './operations-modern.css';
import JournalPage from './JournalPage';
import { BandaDisciplina, CATEGORIA, GraficoCassa, GraficoRischio, GraficoSizing, Payoff } from './mandato/Grafici';
import { CAMPI_PRINCIPALI, coerenze, leggi, paragrafi, sintesi } from './mandato/valori';

const ETICHETTA = fieldLabel;
type Form = Record<string, any>;

function DeferredMandatoView({ render }: { render: () => ReactNode }) {
  return render();
}

function MandatoViewBoundary({ render }: { render: () => ReactNode }) {
  const language = useLingua();
  return <NewInterfaceBoundary language={language}>
    <DeferredMandatoView render={render} />
  </NewInterfaceBoundary>;
}

export function formaDaValori(schema: Record<string, CampoMandato>, valori: ValoriMandato | null, language: Lingua = linguaCorrente()): Form {
  const out: Form = {};
  for (const [nome, c] of Object.entries(schema)) {
    const v = valori?.[c.blocco]?.[nome];
    if (c.tipo === 'intervallo_pct' || c.tipo === 'intervallo_int') out[nome] = Array.isArray(v) ? v.map(x => String(x).replace('.', language === 'it' ? ',' : '.')) : ['', ''];
    else if (c.tipo === 'num' || c.tipo === 'pct' || c.tipo === 'int') out[nome] = v == null ? '' : String(v).replace('.', language === 'it' ? ',' : '.');
    else if (c.tipo === 'lista_testo') out[nome] = Array.isArray(v) ? v.join('\n') : '';
    else if (c.tipo === 'lista') out[nome] = Array.isArray(v) ? [...v] : [];
    else if (c.tipo === 'interruttori') out[nome] = v && typeof v === 'object' ? { ...v } : Object.fromEntries((c.scelte || []).map(x => [x, null]));
    else out[nome] = v ?? (c.tipo === 'bool' ? null : '');
  }
  return out;
}

function metadatiDaValori(valori: ValoriMandato | null): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (!valori) return out;
  for (const [k, v] of Object.entries(valori)) if (k.startsWith('_')) out[k] = v;
  for (const b of BLOCCHI_MANDATO) {
    const meta = Object.fromEntries(Object.entries(valori[b] || {}).filter(([k]) => k.startsWith('_')));
    if (Object.keys(meta).length) out[b] = meta;
  }
  return out;
}

function payloadDaForm(schema: Record<string, CampoMandato>, form: Form, metadati: Record<string, unknown>, inputLanguage: Lingua = linguaCorrente()): { valori: ValoriMandato | null; errori: Record<string, string> } {
  const blocchi: any = Object.fromEntries(BLOCCHI_MANDATO.map(b => [b, {}]));
  for (const [k, v] of Object.entries(metadati)) {
    if (k.startsWith('_')) blocchi[k] = v;
    else if (BLOCCHI_MANDATO.includes(k as any) && v && typeof v === 'object') Object.assign(blocchi[k], v);
  }
  const errori: Record<string, string> = {};
  for (const [nome, c] of Object.entries(schema)) {
    const v = form[nome]; let pronto: any = v;
    if (c.tipo === 'int' || c.tipo === 'num' || c.tipo === 'pct') {
      const r = leggiNumeroMandato(v, c, inputLanguage, linguaCorrente()); if (!r.ok) errori[nome] = r.motivo; else pronto = r.valore;
    } else if (c.tipo === 'intervallo_pct' || c.tipo === 'intervallo_int') {
      const a = leggiNumeroMandato(v?.[0] || '', { ...c, obbligatorio: c.obbligatorio }, inputLanguage, linguaCorrente());
      const b = leggiNumeroMandato(v?.[1] || '', { ...c, obbligatorio: c.obbligatorio }, inputLanguage, linguaCorrente());
      if (!a.ok || !b.ok) errori[nome] = !a.ok ? tr('mandate.min_error', {a: a.motivo}) : tr('mandate.max_error', {a: (b as any).motivo});
      else if (a.valore == null && b.valore == null) pronto = null;
      else if (a.valore == null || b.valore == null) errori[nome] = tr('mandate.both_bounds');
      else if (a.valore > b.valore) errori[nome] = tr('mandate.ordered_bounds');
      else pronto = [a.valore, b.valore];
    } else if (c.tipo === 'bool') {
      if (v == null && c.obbligatorio) errori[nome] = tr('mandate.choose_boolean');
    } else if (c.tipo === 'scelta') {
      if (c.obbligatorio && !c.scelte?.includes(v)) errori[nome] = tr('mandate.choose_one');
      if (!c.obbligatorio && !v) pronto = null;
    } else if (c.tipo === 'valuta') {
      pronto = String(v || '').trim().toUpperCase();
      if (!/^[A-Z]{3}$/.test(pronto)) errori[nome] = tr('mandate.currency_error');
    } else if (c.tipo === 'lista') {
      if (c.obbligatorio && (!Array.isArray(v) || !v.length)) errori[nome] = tr('mandate.choose_some');
    } else if (c.tipo === 'lista_testo') pronto = String(v || '').split('\n').map(x => x.trim()).filter(Boolean);
    else if (c.tipo === 'interruttori') {
      if ((c.scelte || []).some(x => typeof v?.[x] !== 'boolean')) errori[nome] = tr('mandate.choose_conditions');
    } else if (c.tipo === 'testo') {
      pronto = String(v || '').trim();
      if (c.massimo_char && pronto.length > c.massimo_char) errori[nome] = tr('mandate.max_chars', {a: c.massimo_char});
      if (pronto.includes('{MANDATO:')) errori[nome] = tr('mandate.reserved');
    }
    blocchi[c.blocco][nome] = pronto;
  }
  return { valori: Object.keys(errori).length ? null : blocchi as ValoriMandato, errori };
}

function erroriServer(testo: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const riga of testo.split('\n')) { const m = /^([a-z0-9_]+):\s*(.*)$/i.exec(riga); if (m) out[m[1]] = [out[m[1]], m[2]].filter(Boolean).join(' · '); }
  return out;
}

/* ── Testata condivisa: lo stato del mandato sta nel modulo, la testata in MandatoPage ── */
export interface TestataMandato {
  stato: 'dichiarato' | 'esempio' | 'incompleto' | 'assente';
  impronta: string | null; salvatoIl: string | null; compilati: number; richiesti: number; esempio: boolean;
}
const CHIAVE_STATO = { dichiarato: 'declared', esempio: 'example', incompleto: 'incomplete', assente: 'absent' } as const;
export interface AzioniTestata { testo: () => void; esempio: () => void }

// La pagina è disegnata per l'area contenuti di un 1920×1080 e, sugli schermi più grandi, si
// ingrandisce in proporzione invece di allungare riquadri vuoti. Sotto 1180 px di larghezza o
// 640 px di altezza resta a misura naturale e scorre.
const DISEGNO_L = 1630, DISEGNO_H = 940, MAX_SCALA = 1.8;
function useAdattaSchermo() {
  const ref = useRef<HTMLDivElement>(null);
  const [misura, setMisura] = useState<{ k: number; h: number | null }>({ k: 1, h: null });
  useEffect(() => {
    const el = ref.current, box = el?.closest('main')?.querySelector<HTMLElement>(':scope > .relative') || el?.parentElement;
    if (!el || !box || typeof ResizeObserver === 'undefined') return;
    const calcola = () => {
      const stile = getComputedStyle(box), sopra = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
      const largo = el.parentElement ? el.parentElement.getBoundingClientRect().width : box.clientWidth;
      const alto = box.clientHeight - sopra - parseFloat(stile.paddingBottom || '0') - 4;
      const adatta = largo >= 1180 && alto >= 640;
      const k = adatta ? Math.max(1, Math.min(alto / DISEGNO_H, largo / DISEGNO_L, MAX_SCALA)) : 1;
      setMisura(m => { const h = adatta ? Math.floor(alto / k) : null; return m.k === k && m.h === h ? m : { k, h }; });
    };
    const ro = new ResizeObserver(calcola); ro.observe(box); calcola();
    return () => ro.disconnect();
  }, []);
  return { ref, adatta: misura.h != null, style: misura.h == null ? undefined : { zoom: misura.k, height: misura.h + 'px' } as React.CSSProperties };
}

export default function MandatoPage() {
  const tr = useT();
  const [tab, setTab] = useState<'mandato' | 'diario'>('mandato');
  const [journalOpened, setJournalOpened] = useState(false);
  const selectTab = (next: 'mandato' | 'diario') => { setTab(next); if (next === 'diario') setJournalOpened(true); };
  useEffect(() => {
    const showMandato = () => setTab('mandato');
    window.addEventListener(RIFIUTO_RUN_EVENT, showMandato);
    return () => window.removeEventListener(RIFIUTO_RUN_EVENT, showMandato);
  }, []);
  const [testata, setTestata] = useState<TestataMandato | null>(null);
  const [menu, setMenu] = useState(false);
  const azioni = useRef<AzioniTestata | null>(null);
  const schermo = useAdattaSchermo();
  useEffect(() => {
    if (!menu) return;
    const chiudi = (e: Event) => { if (!(e.target as HTMLElement)?.closest?.('.mnd-menu-wrap')) setMenu(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenu(false); };
    window.addEventListener('pointerdown', chiudi); window.addEventListener('keydown', esc);
    return () => { window.removeEventListener('pointerdown', chiudi); window.removeEventListener('keydown', esc); };
  }, [menu]);
  const language = useLingua();
  const dataSalvata = (iso: string | null) => { if (!iso) return null; const d = new Date(iso + 'T12:00:00'); return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(localeDi(language), { day: 'numeric', month: 'short', year: 'numeric' }); };
  const statoChip = testata && ({ dichiarato: 'is-good', esempio: 'is-warn', incompleto: 'is-bad', assente: 'is-bad' } as const)[testata.stato];
  return <ModernPage page="mandato" presentationBoundary={false} render={() => <div className="mandato-workspace bbn-font" data-layout="worktable" data-fit={schermo.adatta ? 'on' : 'off'} ref={schermo.ref} style={schermo.style}>
    <MandatoViewBoundary render={() => <header className="mandato-workspace-head">
      <h1>{tr('mandate.workspace')}</h1>
      <nav className="bbn-seg mandato-tabs" role="tablist" aria-label={tr('mandate.workspace')} onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 'mandato' : event.key === 'End' ? 'diario' : tab === 'mandato' ? 'diario' : 'mandato';
        selectTab(next); document.getElementById('tab-' + next)?.focus();
      }}>
        <button id="tab-mandato" role="tab" className={tab === 'mandato' ? 'is-on' : ''} aria-selected={tab === 'mandato'} aria-controls="panel-mandato" tabIndex={tab === 'mandato' ? 0 : -1} onClick={() => selectTab('mandato')}>{tr('mandate.mandate')}</button>
        <button id="tab-diario" role="tab" className={tab === 'diario' ? 'is-on' : ''} aria-selected={tab === 'diario'} aria-controls="panel-diario" tabIndex={tab === 'diario' ? 0 : -1} onClick={() => selectTab('diario')}>{tr('mandate.journal')}</button>
      </nav>
      {tab === 'mandato' && testata && <div className="mnd-chips">
        <span className={'mnd-chip ' + statoChip}><i className="mnd-chip-dot" /><b>{tr(('mandate.status_' + CHIAVE_STATO[testata.stato]) as any)}</b>{testata.stato !== 'assente' && <> {tr(('mandate.status_' + CHIAVE_STATO[testata.stato] + '_note') as any)}</>}</span>
        {testata.impronta && <span className="mnd-chip">{tr('mandate.fingerprint')} <b className="mnd-mono">{testata.impronta.slice(0, 12)}</b></span>}
        <span className="mnd-chip">{tr('mandate.saved_on')} <b>{dataSalvata(testata.salvatoIl) || tr('mandate.never_saved')}</b></span>
        <span className="mnd-chip"><span className="mnd-progress" aria-hidden="true"><i style={{ width: (testata.richiesti ? testata.compilati / testata.richiesti * 100 : 0) + '%' }} className={testata.compilati < testata.richiesti ? 'is-warn' : ''} /></span><b>{tr('mandate.required_short', { a: testata.compilati, b: testata.richiesti })}</b></span>
      </div>}
      {tab === 'diario' && <div className="mnd-chips"><span className="mnd-chip"><Lock aria-hidden="true" /><b>{tr('journal.private')}</b> {tr('journal.privacy')}</span></div>}
      <span className="mnd-grow" />
      {tab === 'mandato' && testata && <div className="mnd-head-actions">
        <button type="button" className="bbn-btn" onClick={() => azioni.current?.testo()}><FileText aria-hidden="true" />{tr('mandate.preview_title')}</button>
        <div className="mnd-menu-wrap">
          <button type="button" className="bbn-icon-btn" aria-label={tr('mandate.more_actions')} aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(x => !x)}><MoreHorizontal aria-hidden="true" /></button>
          {menu && <div className="mnd-menu" role="menu">
            <button type="button" role="menuitem" className="example" disabled={!testata.esempio} onClick={() => { setMenu(false); azioni.current?.esempio(); }}><Wand2 aria-hidden="true" /><span>{tr('mandate.example')}<small>{tr('mandate.example_menu_note')}</small></span></button>
          </div>}
        </div>
      </div>}
    </header>} />
    <div id="panel-mandato" className="mandato-panel" role="tabpanel" aria-labelledby="tab-mandato" hidden={tab !== 'mandato'}><MandatoForm onTestata={setTestata} azioni={azioni} /></div>
    <div id="panel-diario" className="mandato-panel" role="tabpanel" aria-labelledby="tab-diario" hidden={tab !== 'diario'}>{journalOpened && <JournalPage />}</div>
  </div>} />;
}

const ICONA_COPERTURA: Record<string, typeof Cpu> = { motore: Cpu, validazione: ShieldCheck, informativo: Info, prompt: MessageSquare };
const CLASSE_COPERTURA: Record<string, string> = { motore: 'engine', validazione: 'validation', informativo: 'info', prompt: 'prompt' };

export function MandatoForm({ onTestata, azioni }: { onTestata?: (t: TestataMandato | null) => void; azioni?: MutableRefObject<AzioniTestata | null> } = {}) {
  const tr = useT(), language = useLingua();
  const [inputLanguage, setInputLanguage] = useState<Lingua>(linguaCorrente);
  const [activeSection, setActiveSection] = useState<string>('profilo');
  const [localErrors, setLocalErrors] = useState(false);
  const [serverError, setServerError] = useState<unknown>(null);
  const [stato, setStato] = useState<StatoMandato | null>(null);
  const [form, setForm] = useState<Form>({});
  const [erroreCarica, setErroreCarica] = useState<string | null>(null);
  const [errori, setErrori] = useState<Record<string, string>>({});
  const [anteprima, setAnteprima] = useState<AnteprimaMandato | null>(null);
  const [messaggio, setMessaggio] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [bozza, setBozza] = useState(() => leggiBozza());
  // 13/09: a draft restored without a recognised number format is READ as Italian by assumption;
  // the assumption travels with the re-saved draft until the mandate is saved or reloaded.
  const [formatoPresunto, setFormatoPresunto] = useState(false);
  const [metadati, setMetadati] = useState<Record<string, unknown>>({});
  const [rifiutoRun, setRifiutoRun] = useState(() => consumaDettaglioRun());
  const revisione = useRef(0);
  const messaggioRef = useRef<HTMLDivElement>(null);
  const portaAlMessaggio = () => setTimeout(() => messaggioRef.current?.focus(), 0);

  const carica = async () => {
    setBusy(true); setErroreCarica(null);
    try {
      const s = await Bellomberg.mandato(); setStato(s); setInputLanguage(linguaCorrente()); setFormatoPresunto(false); setForm(formaDaValori(s.campi, s.valori)); setMetadati(metadatiDaValori(s.valori));
      setBozza(leggiBozza()); setDirty(false); setErrori(erroriServer((s.errori || []).join('\n'))); setAnteprima(null);
      caricaTesto();
    } catch (e) { setErroreCarica(e as any); }
    finally { setBusy(false); }
  };
  useEffect(() => { carica(); }, []);
  useEffect(() => {
    const ricevi = (evento: Event) => setRifiutoRun(consumaDettaglioRun() || (evento as CustomEvent<string>).detail);
    window.addEventListener(RIFIUTO_RUN_EVENT, ricevi);
    return () => window.removeEventListener(RIFIUTO_RUN_EVENT, ricevi);
  }, []);
  useEffect(() => {
    if (dirty && stato) {
      salvaBozza({ baseImpronta: stato.impronta, form, metadati, salvataIl: new Date().toISOString(), inputLanguage, ...(formatoPresunto ? { formatoPresunto: true } : {}) });
    }
  }, [dirty, form, metadati, stato, inputLanguage, formatoPresunto]);

  const cambia = (nome: string, valore: any) => {
    revisione.current += 1; setAnteprima(null); setPannello(false); setServerError(null); setMessaggio(null); setDirty(true); setErrori(x => { const y = { ...x }; delete y[nome]; return y; });
    setForm(x => ({ ...x, [nome]: valore }));
  };
  const sostituisci = (nuovo: Form, nuoviMeta: Record<string, unknown>, sporco: boolean) => {
    revisione.current += 1; setForm(nuovo); setMetadati(nuoviMeta); setDirty(sporco); setServerError(null); setAnteprima(null); setPannello(false); setMessaggio(null); setErrori({});
  };
  const compilato = useMemo(() => stato ? payloadDaForm(stato.campi, form, metadati, inputLanguage) : { valori: null, errori: {} }, [stato, form, metadati, inputLanguage, language]);

  const prova = async () => {
    if (!stato || !compilato.valori) { setLocalErrors(true); setErrori(compilato.errori); const first = Object.keys(compilato.errori)[0]; if (first && stato?.campi[first]) setActiveSection(stato.campi[first].blocco); setPannello(false); setMessaggio({ key: 'mandate.correct' }); portaAlMessaggio(); return; }
    const mia = ++revisione.current; setBusy(true); setLocalErrors(false); setServerError(null); setErrori({}); setMessaggio(null);
    try { const a = await Bellomberg.mandatoAnteprima(compilato.valori); if (rispostaDellaRevisione(mia, revisione.current)) { setAnteprima(a); setPannello(true); setMessaggio({ key: 'mandate.preview_ready' }); } }
    catch (e) { if (rispostaDellaRevisione(mia, revisione.current)) { const d = dettaglioLeggibile(e); const server = erroriServer(d); setErrori(server); setServerError(e); setPannello(false); const first = Object.keys(server)[0]; if (first && stato.campi[first]) setActiveSection(stato.campi[first].blocco); setMessaggio({ error: e }); portaAlMessaggio(); } }
    finally { setBusy(false); }
  };
  const salva = async () => {
    if (!stato || !compilato.valori || !anteprima) return;
    // Il pannello si chiude: l'esito del salvataggio si legge nella barra delle azioni.
    setPannello(false); setBusy(true); setSaving(true); setServerError(null); setMessaggio(null);
    try {
      await Bellomberg.salvaMandato(compilato.valori);
      const s = await Bellomberg.mandato();
      if (s.impronta !== anteprima.impronta) throw new Error(tr('mandate.mismatch', { a: anteprima.impronta, b: s.impronta || tr('mandate.na') }));
      setStato(s); setInputLanguage(linguaCorrente()); setFormatoPresunto(false); setForm(formaDaValori(s.campi, s.valori)); setMetadati(metadatiDaValori(s.valori));
      eliminaBozza(); setBozza(null); setDirty(false); setAnteprima(null); setPannello(false); setErrori({});
      setMessaggio({ key: 'mandate.saved' }); caricaTesto();
    } catch (e) { const d = dettaglioLeggibile(e); setErrori(erroriServer(d)); setServerError(e); setMessaggio({ error: e }); portaAlMessaggio(); }
    finally { setBusy(false); setSaving(false); }
  };

  // Stato aggiunto con la Nuova (in coda: i test SSR indicizzano gli hook per posizione).
  const [pannello, setPannello] = useState(false);
  const [testoSalvato, setTestoSalvato] = useState<string | null | undefined>(undefined);
  const testoRef = useRef<HTMLDivElement>(null);
  function caricaTesto() {
    Promise.resolve().then(() => Bellomberg.mandatoAnteprima()).then(a => setTestoSalvato(typeof a?.testo === 'string' && a.testo.trim() ? a.testo : null)).catch(() => setTestoSalvato(null));
  }
  const esempio = () => { if (stato?.esempio && !saving) { setInputLanguage(language); setFormatoPresunto(false); sostituisci(formaDaValori(stato.campi, stato.esempio, language), {}, true); } };
  if (azioni) azioni.current = { testo: () => { if (anteprima) setPannello(true); else if (compilato.valori && dirty) prova(); else setPannello(true); }, esempio };
  const testata: TestataMandato | null = stato ? {
    stato: stato.dichiarato ? (stato.origine === 'esempio' ? 'esempio' : 'dichiarato') : stato.causa === 'assente' ? 'assente' : 'incompleto',
    impronta: stato.impronta, salvatoIl: stato.dichiarato_il,
    compilati: BLOCCHI_MANDATO.reduce((n, b) => { const s = sectionStatus(stato.campi, form, {}, b); return n + s.required - s.missing; }, 0),
    richiesti: Object.values(stato.campi).filter(c => c.obbligatorio).length, esempio: !!stato.esempio && !saving,
  } : null;
  const testataChiave = JSON.stringify(testata);
  useEffect(() => { onTestata?.(testata); }, [testataChiave]);
  useEffect(() => () => onTestata?.(null), []);
  useEffect(() => {
    const box = testoRef.current, cur = box?.querySelector<HTMLElement>('.is-current');
    if (box && cur) box.scrollTop = cur.offsetTop - box.offsetTop - 8;
  }, [activeSection, testoSalvato]);

  return <MandatoViewBoundary render={() => {
  if (!stato && !erroreCarica) return <div className="mandato-loading" role="status" aria-busy="true">{tr('mandate.loading')}</div>;
  if (!stato) return <div className="mandato-fault" role="alert"><b>{tr('mandate.unreadable')}</b><pre>{erroreCarica ? errorForLanguage(erroreCarica,language) : tr('mandate.loading')}</pre><button className="bbn-btn" onClick={carica} disabled={busy}>{tr('mandate.retry')}</button></div>;
  const view = localizePayload(stato, language);
  const shownErrors = localErrors ? compilato.errori : serverError ? erroriServer(errorForLanguage(serverError,language)) : dirty ? errori : erroriServer((view.errori || []).join('\n'));
  const lettura = leggi(form, inputLanguage), avvisi = coerenze(lettura, language);
  const base = formaDaValori(stato.campi, stato.valori, inputLanguage);
  const cambiato = (n: string) => JSON.stringify(form[n]) !== JSON.stringify(base[n]);
  const sezioniCambiate = BLOCCHI_MANDATO.filter(b => Object.entries(stato.campi).some(([n,c]) => c.blocco === b && cambiato(n)));
  const stati = Object.fromEntries(BLOCCHI_MANDATO.map(b => {
    const s = sectionStatus(stato.campi, form, shownErrors, b);
    const extra = Object.keys(avvisi).filter(n => stato.campi[n]?.blocco === b && !shownErrors[n]).length;
    return [b, { ...s, issues: s.errors + extra }];
  })) as Record<string, ReturnType<typeof sectionStatus> & { issues: number }>;
  const etichettaOpzione = (x: string) => optionLabel(x, language);
  const t = (k: string, p?: Record<string, string | number>) => tr(('mandate.' + k) as any, p as any);

  const pill = (b: string) => { const s = stati[b];
    return s.issues ? <span className="bbn-pill is-giu"><AlertCircle aria-hidden="true" />{t('section_issues', { a: s.issues })}</span>
      : s.missing ? <span className="bbn-warn-pill">{t('section_missing', { a: s.missing })}</span>
      : <span className="bbn-pill is-su"><Check aria-hidden="true" />{t('section_complete')}</span>; };

  const ariaDi = (nome: string, c: CampoMandato) => ({ 'aria-labelledby': 'ml-' + nome, 'aria-describedby': 'md-' + nome + (shownErrors[nome] ? ' me-' + nome : ''), 'aria-invalid': !!shownErrors[nome], 'aria-required': c.obbligatorio });
  const sino = (nome: string, c: CampoMandato, v: any) => <div className="mandato-choice bbn-seg" role="group" {...ariaDi(nome, c)}>{([[true, tr('mandate.yes')], [false, tr('mandate.no')]] as const).map(([x, l]) => <button key={l} type="button" aria-pressed={v === x} className={v === x ? 'is-on' : ''} onClick={() => cambia(nome, x)}>{l}</button>)}</div>;

  const controllo = (nome: string, c: CampoMandato): ReactNode => {
    const v = form[nome], aria = ariaDi(nome, c);
    if (c.tipo === 'bool') return sino(nome, c, v);
    if (c.tipo === 'scelta') return <div className="mandato-scelta bbn-seg" role="group" {...aria}>{c.scelte?.map(x => <button key={x} type="button" aria-pressed={v === x} className={v === x ? 'is-on' : ''} onClick={() => cambia(nome, x)}>{etichettaOpzione(x)}</button>)}</div>;
    if (c.tipo === 'intervallo_pct' || c.tipo === 'intervallo_int') return <div className="mandato-range" role="group" {...aria}>
      <label className="mnd-input"><span className="mnd-pre">{t('from')}</span><input id={'m-'+nome+'-min'} aria-label={tr('mandate.minimum',{a:ETICHETTA(nome)})} value={v?.[0]||''} inputMode="decimal" onChange={e=>cambia(nome,[e.target.value,v?.[1]||''])}/></label>
      <span className="mnd-arrow" aria-hidden="true">→</span>
      <label className="mnd-input"><span className="mnd-pre">{t('to')}</span><input id={'m-'+nome+'-max'} aria-label={tr('mandate.maximum',{a:ETICHETTA(nome)})} value={v?.[1]||''} inputMode="decimal" onChange={e=>cambia(nome,[v?.[0]||'',e.target.value])}/>{c.unita && <em>{c.unita}</em>}</label></div>;
    if (c.tipo === 'lista_testo' || (c.tipo === 'testo' && (c.massimo_char || 0) > 500)) return <textarea id={'m-'+nome} className="mnd-textarea" {...aria} value={v||''} maxLength={c.massimo_char || undefined} placeholder={c.tipo === 'lista_testo' ? t('lines_hint') : undefined} onChange={e=>cambia(nome,e.target.value)} rows={c.tipo==='lista_testo'?4:6}/>;
    return <div className="mandato-input mnd-input"><input id={'m-'+nome} {...aria} value={v??''} inputMode={['int','num','pct'].includes(c.tipo)?'decimal':undefined} maxLength={c.massimo_char||undefined} placeholder={c.obbligatorio ? undefined : tr('mandate.optional')} style={c.tipo === 'valuta' ? { textTransform: 'uppercase' } : undefined} onChange={e=>cambia(nome,e.target.value)}/>{c.unita ? <span>{c.unita}</span> : c.massimo_char ? <span>{String(v || '').length}/{c.massimo_char}</span> : null}</div>;
  };

  const testa = (nome: string, c: CampoMandato, h3 = false) => { const cov = coverageLabel(nome, language), Icona = ICONA_COPERTURA[cov.classe] || MessageSquare;
    const Tag = h3 ? 'h3' : 'div';
    return <div className="mnd-field-head">
      <Tag id={'ml-' + nome} className="field-label">{ETICHETTA(nome)}{c.obbligatorio && <sup>*</sup>}</Tag>
      <span className={'mnd-cov is-' + (CLASSE_COPERTURA[cov.classe] || 'prompt')} title={cov.etichetta + ': ' + cov.nota} aria-label={cov.etichetta + ': ' + cov.nota} role="img"><Icona aria-hidden="true" /></span>
      <span className="mnd-help" tabIndex={0} aria-label={t('describe', { a: ETICHETTA(nome) })}><HelpCircle aria-hidden="true" /><span className="mnd-tip" id={'md-' + nome} role="tooltip">{c.descrizione}</span></span>
      <span className="mnd-grow" />
      {cambiato(nome) && <span className="mnd-changed">{t('modified')}</span>}
      <span className="mnd-range-note">{c.obbligatorio ? '' : tr('mandate.optional')}{!c.obbligatorio && c.intervallo ? ' · ' : ''}{c.intervallo ? `${c.intervallo[0]}–${c.intervallo[1]}` : ''}</span>
    </div>; };
  const piede = (nome: string) => shownErrors[nome] ? <div id={'me-' + nome} className="field-error" role="alert"><AlertCircle aria-hidden="true" />{shownErrors[nome]}</div>
    : avvisi[nome] ? <div className="mnd-hint"><AlertCircle aria-hidden="true" /><span><b>{t('hint_label')}</b> · {avvisi[nome]}</span></div> : null;
  const stato_campo = (nome: string) => shownErrors[nome] ? ' invalid' : avvisi[nome] ? ' is-hint' : cambiato(nome) ? ' is-changed' : '';

  const campoGriglia = (nome: string, c: CampoMandato) => <article key={nome} data-field={nome} className={'mnd-field' + stato_campo(nome) + (c.tipo === 'lista_testo' || (c.tipo === 'testo' && (c.massimo_char || 0) > 500) ? ' is-wide' : '')}>
    {testa(nome, c)}{controllo(nome, c)}{piede(nome)}</article>;

  const campi = (b: string) => Object.entries(view.campi).filter(([, c]) => c.blocco === b);
  const schema = (nome: string) => view.campi[nome];
  const principale = (nome: string, corpo: (c: CampoMandato) => ReactNode, extra = '') => { const c = schema(nome); if (!c) return null;
    return <div data-field={nome} className={'mnd-main-field' + stato_campo(nome) + (extra ? ' ' + extra : '')}>{testa(nome, c, true)}{corpo(c)}{piede(nome)}</div>; };

  // Profilo: orizzonte e stile come scelte grandi, poi le piazze in ordine di preferenza.
  const scelteCard = (nome: string) => principale(nome, c => <div className="mnd-choices" role="group" {...ariaDi(nome, c)}>{c.scelte?.map(x =>
    <button key={x} type="button" className={'mnd-choice' + (form[nome] === x ? ' is-on' : '')} aria-pressed={form[nome] === x} onClick={() => cambia(nome, x)}><b>{etichettaOpzione(x)}</b><span>{t('gloss_' + x)}</span></button>)}</div>, 'is-choices');
  const piazze = () => principale('mercati_accessibili', c => { const scelte: string[] = form.mercati_accessibili || [], resto = (c.scelte || []).filter(x => !scelte.includes(x));
    const sposta = (i: number, d: number) => { const n = [...scelte]; [n[i], n[i + d]] = [n[i + d], n[i]]; cambia('mercati_accessibili', n); };
    return <div className="mnd-markets" role="group" {...ariaDi('mercati_accessibili', c)}>
      <ol className="mnd-order">{scelte.length ? scelte.map((x, i) => <li key={x} className={i === 0 ? 'is-home' : ''}><span className="mnd-pos">{i + 1}</span><b>{etichettaOpzione(x)}</b>{i === 0 && <span className="mnd-home">{t('markets_home')}</span>}
        <span className="mnd-order-ctl">
          <button type="button" className="bbn-icon-btn" disabled={i === 0} aria-label={t('move_up', { a: etichettaOpzione(x) })} onClick={() => sposta(i, -1)}><ChevronUp aria-hidden="true" /></button>
          <button type="button" className="bbn-icon-btn" disabled={i === scelte.length - 1} aria-label={t('move_down', { a: etichettaOpzione(x) })} onClick={() => sposta(i, 1)}><ChevronDown aria-hidden="true" /></button>
          <button type="button" className="bbn-icon-btn" aria-label={t('remove', { a: etichettaOpzione(x) })} onClick={() => cambia('mercati_accessibili', scelte.filter(y => y !== x))}><X aria-hidden="true" /></button>
        </span></li>) : <li className="mnd-empty">{t('markets_empty')}</li>}</ol>
      <div className="mnd-add"><span className="mnd-sub">{t('markets_add')}</span><div>{resto.map(x => <button key={x} type="button" className="mnd-add-chip" aria-label={t('add', { a: etichettaOpzione(x) })} onClick={() => cambia('mercati_accessibili', [...scelte, x])}><Plus aria-hidden="true" />{etichettaOpzione(x)}</button>)}</div></div>
    </div>; }, 'is-markets');
  // Disciplina: le condizioni come interruttori grandi.
  const condizioni = () => principale('condizioni_taglio_oltre', c => { const v = form.condizioni_taglio_oltre || {};
    return <div className="mnd-conds" role="group" {...ariaDi('condizioni_taglio_oltre', c)}>{c.scelte?.map(x => { const on = v[x];
      return <button key={x} type="button" className={'mnd-cond' + (on === true ? ' is-on' : '')} aria-pressed={on === true} onClick={() => cambia('condizioni_taglio_oltre', { ...v, [x]: on !== true })}>
        <span className="mnd-box" aria-hidden="true">{on === true && <Check />}</span><b>{ETICHETTA(x)}</b><small>{on === true ? t('cond_on') : on === false ? t('cond_off') : t('cond_unset')}</small></button>; })}</div>; }, 'is-conds');
  // Opzioni: la domanda, poi le strutture come card con il loro profilo di guadagno.
  const strutture = () => principale('strumenti_ammessi', c => { const scelte: string[] = form.strumenti_ammessi || [], attive = form.opzioni_abilitate === true;
    return <div className={'mnd-strats' + (attive ? '' : ' is-off')} role="group" {...ariaDi('strumenti_ammessi', c)}>{c.scelte?.map(x => { const sel = scelte.includes(x), cat = CATEGORIA[x] || 'direzionale';
      return <button key={x} type="button" className={'mnd-strat' + (sel ? ' is-on' : '')} aria-pressed={sel} disabled={!attive && !sel} onClick={() => cambia('strumenti_ammessi', sel ? scelte.filter(y => y !== x) : [...scelte, x])}>
        <span className={'mnd-cat is-' + cat}>{t('cat_' + cat)}</span><span className="mnd-box" aria-hidden="true">{sel && <Check />}</span>
        <Payoff strumento={x} etichetta={t('payoff')} /><b>{etichettaOpzione(x)}</b><span>{t('strat_' + x)}</span></button>; })}</div>; }, 'is-strats');

  const eroe = (b: string): ReactNode => {
    if (b === 'profilo') return <><div className="mnd-hero-cols is-profile">{scelteCard('tipo_investimento')}{scelteCard('stile')}</div>{piazze()}</>;
    if (b === 'rischio') return <GraficoRischio v={lettura} avvisi={avvisi} l={language} />;
    if (b === 'sizing') return <GraficoSizing v={lettura} avvisi={avvisi} l={language} />;
    if (b === 'cassa') return <GraficoCassa v={lettura} avvisi={avvisi} l={language} politica={form.politica_impiego ? etichettaOpzione(form.politica_impiego).toLowerCase() : ''} />;
    if (b === 'disciplina') return <div className="mnd-hero-cols"><div className="mnd-col"><BandaDisciplina v={lettura} avvisi={avvisi} l={language} /></div><div className="mnd-col">{condizioni()}</div></div>;
    if (b === 'opzioni') { const c = schema('opzioni_abilitate'); return <>
      {c && <div data-field="opzioni_abilitate" className={'mnd-main-field is-question' + stato_campo('opzioni_abilitate')}>{testa('opzioni_abilitate', c, true)}
        <p className="mnd-question">{t('opt_question')}</p>{sino('opzioni_abilitate', c, form.opzioni_abilitate)}
        <span className="mnd-grow" /><span className="mnd-note">{form.opzioni_abilitate === true ? t('opt_count', { a: (form.strumenti_ammessi || []).length, b: c ? (schema('strumenti_ammessi')?.scelte || []).length : 0 }) : t('opt_enable_hint')}</span>{piede('opzioni_abilitate')}</div>}
      {strutture()}</>; }
    if (b === 'note') return <div className="mnd-hero-cols is-notes">
      {principale('note_per_il_comitato', c => <>{controllo('note_per_il_comitato', c)}<small className="mnd-count">{tr('mandate.chars', { a: String(form.note_per_il_comitato || '').length, b: c.massimo_char || 0 })}</small></>, 'is-note')}
      <div className="mnd-col">{principale('aree_gradite', c => controllo('aree_gradite', c), 'is-list is-good')}{principale('esclusioni', c => controllo('esclusioni', c), 'is-list is-bad')}</div></div>;
    return null;
  };

  const blocchi = paragrafi(testoSalvato || '');
  const messaggioEl = messaggio ? <div ref={messaggioRef} tabIndex={-1} className="preview-message" role="status" aria-live="polite">{showNotice(messaggio, language)}</div> : null;
  const testoPannello = anteprima?.testo ?? testoSalvato ?? null;

  return <div className="mandato-page" data-layout="worktable" data-fit-scope>
    {stato.origine === 'esempio' && <div className="mnd-note-bar is-warn"><AlertCircle aria-hidden="true" /><span>{tr('mandate.example_active')}</span></div>}
    {!stato.dichiarato && <div className="mnd-note-bar is-bad"><AlertCircle aria-hidden="true" /><span><b>{tr(stato.causa === 'assente' ? 'mandate.absent' : 'mandate.incomplete')}</b> · {stato.causa === 'assente' ? tr('mandate.absent_help') : tr('mandate.incomplete_help')}{view.dettaglio && <details><summary>{tr('mandate.verification_detail')}</summary>{view.dettaglio}</details>}</span></div>}
    {rifiutoRun && <div className="mnd-note-bar is-bad" role="alert"><AlertCircle aria-hidden="true" /><span>{tr('mandate.run_refused', {a: rifiutoRun})}</span></div>}
    {bozza && <div className="mnd-note-bar is-info"><History aria-hidden="true" /><span><b>{tr('mandate.recoverable')}</b> · {new Date(bozza.salvataIl).toLocaleString(localeDi(language))}{bozza.baseImpronta !== stato.impronta && <strong> {tr('mandate.disk_changed')}</strong>}{(!eLingua(bozza.inputLanguage) || bozza.formatoPresunto) && <> · {tr('mandate.legacy_format')}</>}</span>
      <span className="mnd-note-actions"><button type="button" className="bbn-link" disabled={saving} onClick={() => { const dichiarata = eLingua(bozza.inputLanguage) && !bozza.formatoPresunto; setInputLanguage(eLingua(bozza.inputLanguage) ? bozza.inputLanguage : 'it'); setFormatoPresunto(!dichiarata); sostituisci({ ...bozza.form }, bozza.metadati || metadati, true); }}>{tr('mandate.restore_explicit')}</button><button type="button" className="bbn-link is-muted" disabled={saving} onClick={() => { eliminaBozza(); setBozza(null); }}>{tr('mandate.discard_draft')}</button></span></div>}

    {pannello && <div className="mnd-drawer-root">
      <div className="mnd-scrim" onClick={() => setPannello(false)} />
      <aside className="mandato-preview mnd-drawer" role="dialog" aria-modal="true" aria-labelledby="mnd-drawer-title" onKeyDown={e => { if (e.key === 'Escape') setPannello(false); }}>
        <div className="mnd-drawer-head"><h2 id="mnd-drawer-title">{tr('mandate.preview_title')}</h2>
          {anteprima ? <span className="bbn-pill is-su"><Check aria-hidden="true" />{tr('mandate.valid')}</span> : <span className="bbn-chip">{tr('mandate.status_declared')}</span>}
          <span className="mnd-grow" /><button type="button" className="bbn-icon-btn" aria-label={t('close')} onClick={() => setPannello(false)}><X aria-hidden="true" /></button></div>
        <div className="mnd-chips">
          {anteprima ? <span className="mnd-chip">{t('new_fingerprint')} <b className="mnd-mono">{anteprima.impronta.slice(0, 12)}</b></span> : stato.impronta && <span className="mnd-chip">{tr('mandate.fingerprint')} <b className="mnd-mono">{stato.impronta.slice(0, 12)}</b></span>}
          {anteprima && <span className="mnd-chip">{tr('mandate.preview_language', { a: tr(anteprima.output_language === 'it' ? 'mandate.language_it' : anteprima.output_language === 'en' ? 'mandate.language_en' : 'mandate.language_unknown') })}</span>}
          {anteprima && sezioniCambiate.length > 0 && <span className="mnd-chip is-accent">{t('changes')} <b>{sezioniCambiate.map(b => sectionLabel(b)).join(', ')}</b></span>}
        </div>
        <p className="mnd-drawer-note">{anteprima ? t('preview_new_note') : t('preview_saved_note')}</p>
        <pre className="mnd-drawer-text">{testoPannello || (anteprima ? '' : t('reads_unavailable'))}</pre>
        <div className="mnd-drawer-foot">{messaggioEl || <span className="mnd-note">{anteprima ? '' : t('nothing_to_save')}</span>}<span className="mnd-grow" />
          <button type="button" className="bbn-btn" onClick={() => setPannello(false)}>{t('close')}</button>
          {anteprima && <button type="button" data-action="save" className="bbn-btn is-primary" onClick={salva} disabled={busy || !anteprima || !compilato.valori}>{tr(saving ? 'ui.saving' : 'mandate.save')}</button>}</div>
      </aside>
    </div>}

    <div className="mandato-shell">
      <nav className="mandato-index bbn-card" aria-label={tr('mandate.sections')}>
        {BLOCCHI_MANDATO.map((b, i) => { const s = stati[b];
          return <a key={b} href={'#mandato-' + b} aria-current={activeSection === b ? 'step' : undefined} onClick={e => { e.preventDefault(); setActiveSection(b); setTimeout(() => document.getElementById('mandato-heading-' + b)?.focus(), 0); }}>
            <span className="mnd-n">{String(i + 1).padStart(2, '0')}</span>
            <span className="mandato-section-label">{sectionLabel(b)}{sezioniCambiate.includes(b) && <i className="mnd-mod" title={t('modified')} />}</span>
            <i className="mnd-state" title={tr('mandate.section_status', { a: s.missing, b: s.issues })}>{s.issues ? <span className="bbn-pill is-giu">!</span> : s.missing ? <span className="bbn-warn-pill">{s.missing}</span> : <Check aria-label={t('section_complete')} />}</i>
            <span className="mnd-summary">{sintesi(b, lettura, language, etichettaOpzione)}</span>
          </a>; })}
        <div className="mnd-reads">
          <div className="mnd-reads-head"><b>{t('reads_title')}</b><span className="mnd-grow" /><button type="button" className="bbn-link" onClick={() => setPannello(true)}>{t('reads_full')}</button></div>
          {sezioniCambiate.length > 0 && testoSalvato && <p className="mnd-reads-note">{t('reads_stale')}</p>}
          <div className="mnd-reads-text" ref={testoRef}>{testoSalvato ? blocchi.map((p, i) => <p key={i} className={p.blocco === activeSection ? 'is-current' : ''}>{p.testo}</p>)
            : <p className="mnd-reads-empty">{testoSalvato === undefined ? tr('mandate.loading') : t('reads_unavailable')}</p>}</div>
        </div>
      </nav>

      <div className="mnd-section-card bbn-card">
        <fieldset className="mandato-form" disabled={saving} aria-busy={saving}>
          {BLOCCHI_MANDATO.map((b, bi) => { const prima = BLOCCHI_MANDATO[bi - 1], dopo = BLOCCHI_MANDATO[bi + 1];
            const griglia = campi(b).filter(([nome]) => !(CAMPI_PRINCIPALI[b] || []).includes(nome));
            return <section id={'mandato-' + b} key={b} hidden={activeSection !== b} className="mnd-section">
              <header className="mnd-section-head">
                <span className="mnd-num">{String(bi + 1).padStart(2, '0')}</span>
                <h2 id={'mandato-heading-' + b} tabIndex={-1}>{sectionLabel(b)}</h2>{pill(b)}
                <span className="mnd-grow" />
                {prima && <button type="button" className="bbn-icon-btn" aria-label={t('prev_section', { a: sectionLabel(prima) })} title={sectionLabel(prima)} onClick={() => setActiveSection(prima)}><ChevronLeft aria-hidden="true" /></button>}
                {dopo && <button type="button" className="bbn-icon-btn" aria-label={t('next_section', { a: sectionLabel(dopo) })} title={sectionLabel(dopo)} onClick={() => setActiveSection(dopo)}><ChevronRight aria-hidden="true" /></button>}
              </header>
              <div className={'mnd-hero is-' + b}>{eroe(b)}</div>
              {griglia.length > 0 && <div className="mandato-grid">{griglia.map(([nome, c]) => campoGriglia(nome, c))}</div>}
              <div className="mnd-legend"><b>{t('coverage_legend')}</b>{(['motore', 'validazione', 'prompt', 'informativo'] as const).map(k => { const Icona = ICONA_COPERTURA[k], cl = CLASSE_COPERTURA[k];
                return <span key={k}><span className={'mnd-cov is-' + cl} aria-hidden="true"><Icona /></span><b>{tr(('mandate.coverage_' + cl) as any)}</b> {tr(('mandate.coverage_' + cl + '_note') as any)}</span>; })}</div>
            </section>; })}
        </fieldset>
        <footer className="mandato-actions">
          <div className="mnd-actions-msg">
            {!pannello && messaggioEl}
            {!(messaggio && !pannello) && <span className="mnd-note">{dirty ? <><i className="mnd-mod" />{tr(sezioniCambiate.length === 1 ? 'mandate.local_draft_one' : 'mandate.local_draft', { a: sezioniCambiate.length })}</> : <><Check aria-hidden="true" />{tr('mandate.unchanged')}</>}</span>}
            <small className="mnd-format">{tr(inputLanguage === 'it' ? 'mandate.format_it' : 'mandate.format_en')}</small>
          </div>
          <button type="button" className="bbn-btn" onClick={() => { eliminaBozza(); setBozza(null); sostituisci(formaDaValori(stato.campi, stato.valori, inputLanguage), metadatiDaValori(stato.valori), false); }} disabled={saving || (!dirty && !bozza)}>{tr('mandate.discard')}</button>
          <button type="button" data-action="preview" className={'bbn-btn' + (anteprima ? '' : ' is-primary')} onClick={prova} disabled={busy}><FileText aria-hidden="true" />{tr('mandate.preview')}</button>
          <button type="button" data-action="save" className={'bbn-btn' + (anteprima ? ' is-primary' : '')} onClick={salva} disabled={busy || !anteprima || !compilato.valori}>{tr(saving ? 'ui.saving' : 'mandate.save')}</button>
        </footer>
      </div>
    </div>
  </div>;
  }} />;
}
