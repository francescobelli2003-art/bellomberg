import { optionLabel, errorForLanguage, fieldLabel, sectionLabel, sectionStatus, coverageLabel, showNotice, type Notice } from '@/lib/mandato-presentation';
import { useLingua, useT } from '@/i18n/provider';
import { eLingua, linguaCorrente, localeDi, type Lingua } from '@/i18n/lingua';
import { t as tr } from '@/i18n/t';
import { localizePayload } from '@/lib/api-presentation';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import {
  BLOCCHI_MANDATO, eliminaBozza, salvaBozza, leggiBozza, coperturaCampo, consumaDettaglioRun, RIFIUTO_RUN_EVENT,
  dettaglioLeggibile, leggiNumeroMandato, rispostaDellaRevisione,
  type AnteprimaMandato, type CampoMandato, type StatoMandato, type ValoriMandato,
} from '@/lib/mandato';
import './mandato.css';
import JournalPage from './JournalPage';

const ETICHETTA = fieldLabel;
type Form = Record<string, any>;

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
  return <div className="mandato-workspace" data-layout="worktable">
    <header className="mandato-workspace-head"><h1>{tr(tab === 'mandato' ? 'mandate.workspace' : 'journal.title')}</h1><p>{tr(tab === 'mandato' ? 'mandate.intro' : 'journal.privacy')}</p></header>
    <nav className="mandato-tabs" role="tablist" aria-label={tr('mandate.workspace')} onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'mandato' : event.key === 'End' ? 'diario' : tab === 'mandato' ? 'diario' : 'mandato';
      selectTab(next); document.getElementById('tab-' + next)?.focus();
    }}>
      <button id="tab-mandato" role="tab" aria-selected={tab === 'mandato'} aria-controls="panel-mandato" tabIndex={tab === 'mandato' ? 0 : -1} onClick={() => selectTab('mandato')}>{tr('mandate.mandate')}</button>
      <button id="tab-diario" role="tab" aria-selected={tab === 'diario'} aria-controls="panel-diario" tabIndex={tab === 'diario' ? 0 : -1} onClick={() => selectTab('diario')}>{tr('mandate.journal')}</button>
    </nav>
    <div id="panel-mandato" role="tabpanel" aria-labelledby="tab-mandato" hidden={tab !== 'mandato'}><MandatoForm /></div>
    <div id="panel-diario" role="tabpanel" aria-labelledby="tab-diario" hidden={tab !== 'diario'}>{journalOpened && <JournalPage />}</div>
  </div>;
}

export function MandatoForm() {
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
    revisione.current += 1; setAnteprima(null); setServerError(null); setMessaggio(null); setDirty(true); setErrori(x => { const y = { ...x }; delete y[nome]; return y; });
    setForm(x => ({ ...x, [nome]: valore }));
  };
  const sostituisci = (nuovo: Form, nuoviMeta: Record<string, unknown>, sporco: boolean) => {
    revisione.current += 1; setForm(nuovo); setMetadati(nuoviMeta); setDirty(sporco); setServerError(null); setAnteprima(null); setMessaggio(null); setErrori({});
  };
  const compilato = useMemo(() => stato ? payloadDaForm(stato.campi, form, metadati, inputLanguage) : { valori: null, errori: {} }, [stato, form, metadati, inputLanguage, language]);

  const prova = async () => {
    if (!stato || !compilato.valori) { setLocalErrors(true); setErrori(compilato.errori); const first = Object.keys(compilato.errori)[0]; if (first && stato?.campi[first]) setActiveSection(stato.campi[first].blocco); setMessaggio({ key: 'mandate.correct' }); portaAlMessaggio(); return; }
    const mia = ++revisione.current; setBusy(true); setLocalErrors(false); setServerError(null); setErrori({}); setMessaggio(null);
    try { const a = await Bellomberg.mandatoAnteprima(compilato.valori); if (rispostaDellaRevisione(mia, revisione.current)) { setAnteprima(a); setMessaggio({ key: 'mandate.preview_ready' }); } }
    catch (e) { if (rispostaDellaRevisione(mia, revisione.current)) { const d = dettaglioLeggibile(e); setErrori(erroriServer(d)); setServerError(e); setMessaggio({ error: e }); portaAlMessaggio(); } }
    finally { setBusy(false); }
  };
  const salva = async () => {
    if (!stato || !compilato.valori || !anteprima) return;
    setBusy(true); setSaving(true); setServerError(null); setMessaggio(null);
    try {
      await Bellomberg.salvaMandato(compilato.valori);
      const s = await Bellomberg.mandato();
      if (s.impronta !== anteprima.impronta) throw new Error(tr('mandate.mismatch', { a: anteprima.impronta, b: s.impronta || tr('mandate.na') }));
      setStato(s); setInputLanguage(linguaCorrente()); setFormatoPresunto(false); setForm(formaDaValori(s.campi, s.valori)); setMetadati(metadatiDaValori(s.valori));
      eliminaBozza(); setBozza(null); setDirty(false); setAnteprima(null); setErrori({});
      setMessaggio({ key: 'mandate.saved' });
    } catch (e) { const d = dettaglioLeggibile(e); setErrori(erroriServer(d)); setServerError(e); setMessaggio({ error: e }); portaAlMessaggio(); }
    finally { setBusy(false); setSaving(false); }
  };

  if (!stato && !erroreCarica) return <div className="mandato-loading" role="status" aria-busy="true">{tr('mandate.loading')}</div>;
  if (!stato) return <div className="mandato-fault" role="alert"><b>{tr('mandate.unreadable')}</b><pre>{erroreCarica ? errorForLanguage(erroreCarica,language) : tr('mandate.loading')}</pre><button onClick={carica} disabled={busy}>{tr('mandate.retry')}</button></div>;
  const view = localizePayload(stato, language);
  const shownErrors = localErrors ? compilato.errori : serverError ? erroriServer(errorForLanguage(serverError,language)) : dirty ? errori : erroriServer((view.errori || []).join('\n'));
  const summaries = BLOCCHI_MANDATO.map(b => sectionStatus(stato.campi, form, shownErrors, b));
  const base = formaDaValori(stato.campi, stato.valori, inputLanguage);
  const changedSections = BLOCCHI_MANDATO.filter(b => Object.entries(stato.campi).some(([n,c]) => c.blocco === b && JSON.stringify(form[n]) !== JSON.stringify(base[n]))).length;
  return <div className="mandato-page" data-layout="worktable">
    <div className="mandato-toolbar"><p>{tr(inputLanguage === 'it' ? 'mandate.format_it' : 'mandate.format_en')}</p><button className="example" onClick={()=>{ if(stato.esempio) { setInputLanguage(language); setFormatoPresunto(false); sostituisci(formaDaValori(stato.campi,stato.esempio,language), {}, true); } }} disabled={!stato.esempio || saving}>{tr('mandate.example')}</button></div>
    {stato.origine === 'esempio' && <div className="mandato-banner amber">{tr('mandate.example_active')}</div>}
    {!stato.dichiarato && <div className="mandato-banner red">{tr(stato.causa === 'assente' ? 'mandate.absent' : 'mandate.incomplete')} · {stato.causa === 'assente' ? tr('mandate.absent_help') : tr('mandate.incomplete_help')}{view.dettaglio && <details><summary>{tr('mandate.verification_detail')}</summary>{view.dettaglio}</details>}</div>}
    {rifiutoRun && <div className="mandato-banner red" role="alert">{tr('mandate.run_refused', {a: rifiutoRun})}</div>}
    {bozza && <div className="mandato-banner draft"><div><b>{tr('mandate.recoverable')}</b> · {new Date(bozza.salvataIl).toLocaleString(localeDi(language))}{bozza.baseImpronta !== stato.impronta && <strong> {tr('mandate.disk_changed')}</strong>}{(!eLingua(bozza.inputLanguage) || bozza.formatoPresunto) && <> · {tr('mandate.legacy_format')}</>}</div><div><button disabled={saving} onClick={() => { const dichiarata = eLingua(bozza.inputLanguage) && !bozza.formatoPresunto; setInputLanguage(eLingua(bozza.inputLanguage) ? bozza.inputLanguage : 'it'); setFormatoPresunto(!dichiarata); sostituisci({ ...bozza.form }, bozza.metadati || metadati, true); }}>{tr('mandate.restore_explicit')}</button><button disabled={saving} onClick={() => { eliminaBozza(); setBozza(null); }}>{tr('mandate.discard_draft')}</button></div></div>}
    <div className="mandato-shell">
      <nav className="mandato-index" aria-label={tr('mandate.sections')}><h2>{tr('mandate.sections')}</h2>{BLOCCHI_MANDATO.map((b, i) => <a key={b} href={'#mandato-' + b} aria-current={activeSection === b ? 'step' : undefined} onClick={e => { e.preventDefault(); setActiveSection(b); setTimeout(() => document.getElementById('mandato-heading-' + b)?.focus(), 0); }}><span>{String(i + 1).padStart(2, '0')}</span><span className="mandato-section-label">{sectionLabel(b)}</span><i title={tr('mandate.section_status', { a: summaries[i].missing, b: summaries[i].errors })}>{summaries[i].errors ? '!' : summaries[i].missing ? summaries[i].missing : '✓'}</i></a>)}<p>{tr('mandate.field_count', { a: Object.keys(stato.campi).length, b: BLOCCHI_MANDATO.length })}</p><p>{tr('mandate.required_count', { a: summaries.reduce((n,s)=>n+s.required-s.missing,0), b: summaries.reduce((n,s)=>n+s.required,0) })}</p><small>{tr('mandate.local_status')}</small></nav>
      <fieldset className="mandato-form" disabled={saving} aria-busy={saving}>
        {BLOCCHI_MANDATO.map((b, bi) => <section id={'mandato-' + b} key={b} hidden={activeSection !== b}>
          <h2 id={'mandato-heading-' + b} tabIndex={-1}><span>{String(bi + 1).padStart(2, '0')}</span>{sectionLabel(b)}</h2>
          <div className="mandato-grid">{Object.entries(view.campi).filter(([, c]) => c.blocco === b).map(([nome, c]) => {
            const cov = coverageLabel(nome), v = form[nome];
            const aria = { 'aria-labelledby': 'ml-' + nome, 'aria-describedby': 'md-' + nome + (shownErrors[nome] ? ' me-' + nome : ''), 'aria-invalid': !!shownErrors[nome], 'aria-required': c.obbligatorio };
            const base = <div className="mandato-field-copy"><div id={'ml-' + nome} className="field-label">{ETICHETTA(nome)}{c.obbligatorio && <sup>*</sup>}</div><div className={'coverage ' + cov.classe} title={cov.nota}>{cov.etichetta}</div><p id={'md-' + nome}>{c.descrizione}</p><small>{tr(c.obbligatorio ? 'mandate.required' : 'mandate.optional')}{c.intervallo && ' · ' + tr('mandate.bounds', {a:c.intervallo[0],b:c.intervallo[1]})}</small></div>;
            let control: React.ReactNode;
            if (c.tipo === 'bool') control = <div className="mandato-choice" role="group" {...aria}>{[[true,tr('mandate.yes')],[false,tr('mandate.no')]].map(([x,l]) => <button key={l as string} type="button" aria-pressed={v === x} className={v === x ? 'on' : ''} onClick={() => cambia(nome, x)}>{l as string}</button>)}</div>;
            else if (c.tipo === 'scelta') control = <select id={'m-'+nome} {...aria} value={v || ''} onChange={e => cambia(nome,e.target.value)}><option value="">{tr('mandate.choose_placeholder')}</option>{c.scelte?.map(x=><option key={x} value={x}>{optionLabel(x)}</option>)}</select>;
            else if (c.tipo === 'lista') control = <div className="mandato-checks" role="group" {...aria}>{c.scelte?.map(x=><label key={x}><input type="checkbox" checked={(v||[]).includes(x)} onChange={e=>cambia(nome,e.target.checked?[...(v||[]),x]:(v||[]).filter((y:string)=>y!==x))}/>{optionLabel(x)}</label>)}</div>;
            else if (c.tipo === 'interruttori') control = <div className="mandato-switches" role="group" {...aria}>{c.scelte?.map(x=><div key={x}><span>{ETICHETTA(x)}</span>{[true,false].map(q=><button type="button" key={String(q)} aria-pressed={v?.[x]===q} className={v?.[x]===q?'on':''} onClick={()=>cambia(nome,{...v,[x]:q})}>{q?tr('mandate.yes'):tr('mandate.no')}</button>)}</div>)}</div>;
            else if (c.tipo === 'intervallo_pct' || c.tipo === 'intervallo_int') control = <div className="mandato-range" role="group" {...aria}><input id={'m-'+nome+'-min'} aria-label={tr('mandate.minimum',{a:ETICHETTA(nome)})} value={v?.[0]||''} inputMode="decimal" onChange={e=>cambia(nome,[e.target.value,v?.[1]||''])}/><span>&rarr;</span><input id={'m-'+nome+'-max'} aria-label={tr('mandate.maximum',{a:ETICHETTA(nome)})} value={v?.[1]||''} inputMode="decimal" onChange={e=>cambia(nome,[v?.[0]||'',e.target.value])}/><em>{c.unita}</em></div>;
            else if (c.tipo === 'lista_testo' || (c.tipo === 'testo' && (c.massimo_char || 0)>500)) control = <textarea id={'m-'+nome} {...aria} value={v||''} maxLength={c.massimo_char || undefined} onChange={e=>cambia(nome,e.target.value)} rows={c.tipo==='lista_testo'?4:6}/>;
            else control = <div className="mandato-input"><input id={'m-'+nome} {...aria} value={v??''} inputMode={['int','num','pct'].includes(c.tipo)?'decimal':undefined} maxLength={c.massimo_char||undefined} onChange={e=>cambia(nome,e.target.value)}/>{c.unita&&<span>{c.unita}</span>}</div>;
            return <article key={nome} className={shownErrors[nome]?'invalid':''}><>{base}<div className="mandato-field-control">{control}{c.massimo_char && <small>{tr('mandate.chars',{a:String(v||'').length,b:c.massimo_char})}</small>}</div></>{shownErrors[nome]&&<div id={'me-'+nome} className="field-error" role="alert">{shownErrors[nome]}</div>}</article>;
          })}</div>
        </section>)}
        <details className="mandato-coverage-help"><summary>{tr('mandate.coverage_help')}</summary>{['var99_1g_pct','drawdown_max_pct','broker','opzioni_abilitate'].map(name => { const c = coverageLabel(name); return <p key={name}><b>{c.etichetta}</b>: {c.nota}</p>; })}</details>
      </fieldset>
      <aside className="mandato-preview">
        <div className="preview-title"><span>{tr('mandate.preview_title')}</span><b>{anteprima ? tr('mandate.valid') : tr('mandate.needs_validation')}</b></div>
        <pre>{anteprima?.testo || tr('mandate.preview_empty')}</pre>
        {messaggio && <div ref={messaggioRef} tabIndex={-1} className="preview-message" role="status" aria-live="polite">{showNotice(messaggio, language)}</div>}
        <p className="mandato-preview-language">{anteprima && tr('mandate.preview_language',{ a: tr(anteprima.output_language === 'it' ? 'mandate.language_it' : anteprima.output_language === 'en' ? 'mandate.language_en' : 'mandate.language_unknown') })}</p>
        <div className="mandato-trace"><span>{tr('mandate.fingerprint')}</span><b>{stato.impronta?.slice(0,12) || tr('mandate.undeclared')}</b><small>{stato.dichiarato_il || tr('mandate.never_saved')} · {stato.origine === 'esempio' ? tr('mandate.example_origin') : stato.origine === 'personalizzato' ? tr('mandate.personalized') : tr('mandate.undeclared')}</small></div>
      </aside>
    </div>
    <footer className="mandato-actions"><span>{dirty ? tr(changedSections === 1 ? 'mandate.local_draft_one' : 'mandate.local_draft',{a:changedSections}) : tr('mandate.unchanged')}</span><button onClick={()=>{ eliminaBozza();setBozza(null);sostituisci(formaDaValori(stato.campi,stato.valori,inputLanguage),metadatiDaValori(stato.valori),false); }} disabled={saving}>{tr('mandate.discard')}</button><button data-action="preview" onClick={prova} disabled={busy}>{tr('mandate.preview')}</button><button data-action="save" className="primary" onClick={salva} disabled={busy || !anteprima || !compilato.valori}>{tr('mandate.save')}</button></footer>
  </div>;
}
