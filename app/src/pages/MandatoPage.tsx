import { useEffect, useMemo, useRef, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import {
  BLOCCHI_MANDATO, eliminaBozza, salvaBozza, leggiBozza, coperturaCampo, consumaDettaglioRun, RIFIUTO_RUN_EVENT,
  dettaglioLeggibile, leggiNumeroMandato, rispostaDellaRevisione,
  type AnteprimaMandato, type CampoMandato, type StatoMandato, type ValoriMandato,
} from '@/lib/mandato';
import './mandato.css';
import JournalPage from './JournalPage';

const TITOLI: Record<string, string> = {
  profilo: 'Profilo', rischio: 'Rischio', sizing: 'Sizing', cassa: 'Cassa',
  disciplina: 'Disciplina', opzioni: 'Opzioni', note: 'Note',
};
const ETICHETTE: Record<string, string> = {
  var99_1g_pct: 'VaR 99% a 1 giorno', stress_gfc_pct: 'Stress 2008',
};
const ETICHETTA = (nome: string) => (ETICHETTE[nome] || nome.replace(/_(pct|int)$/,'').replace(/_/g, ' ')).replace(/^./, x => x.toUpperCase());
type Form = Record<string, any>;

function formaDaValori(schema: Record<string, CampoMandato>, valori: ValoriMandato | null): Form {
  const out: Form = {};
  for (const [nome, c] of Object.entries(schema)) {
    const v = valori?.[c.blocco]?.[nome];
    if (c.tipo === 'intervallo_pct' || c.tipo === 'intervallo_int') out[nome] = Array.isArray(v) ? v.map(x => String(x).replace('.', ',')) : ['', ''];
    else if (c.tipo === 'num' || c.tipo === 'pct' || c.tipo === 'int') out[nome] = v == null ? '' : String(v).replace('.', ',');
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

function payloadDaForm(schema: Record<string, CampoMandato>, form: Form, metadati: Record<string, unknown>): { valori: ValoriMandato | null; errori: Record<string, string> } {
  const blocchi: any = Object.fromEntries(BLOCCHI_MANDATO.map(b => [b, {}]));
  for (const [k, v] of Object.entries(metadati)) {
    if (k.startsWith('_')) blocchi[k] = v;
    else if (BLOCCHI_MANDATO.includes(k as any) && v && typeof v === 'object') Object.assign(blocchi[k], v);
  }
  const errori: Record<string, string> = {};
  for (const [nome, c] of Object.entries(schema)) {
    const v = form[nome]; let pronto: any = v;
    if (c.tipo === 'int' || c.tipo === 'num' || c.tipo === 'pct') {
      const r = leggiNumeroMandato(v, c); if (!r.ok) errori[nome] = r.motivo; else pronto = r.valore;
    } else if (c.tipo === 'intervallo_pct' || c.tipo === 'intervallo_int') {
      const a = leggiNumeroMandato(v?.[0] || '', { ...c, obbligatorio: c.obbligatorio });
      const b = leggiNumeroMandato(v?.[1] || '', { ...c, obbligatorio: c.obbligatorio });
      if (!a.ok || !b.ok) errori[nome] = !a.ok ? `minimo: ${a.motivo}` : `massimo: ${(b as any).motivo}`;
      else if (a.valore == null && b.valore == null) pronto = null;
      else if (a.valore == null || b.valore == null) errori[nome] = 'scrivi sia minimo sia massimo';
      else if (a.valore > b.valore) errori[nome] = 'il minimo supera il massimo';
      else pronto = [a.valore, b.valore];
    } else if (c.tipo === 'bool') {
      if (v == null && c.obbligatorio) errori[nome] = 'scegli sì o no';
    } else if (c.tipo === 'scelta') {
      if (c.obbligatorio && !c.scelte?.includes(v)) errori[nome] = 'scegli una voce';
      if (!c.obbligatorio && !v) pronto = null;
    } else if (c.tipo === 'valuta') {
      pronto = String(v || '').trim().toUpperCase();
      if (!/^[A-Z]{3}$/.test(pronto)) errori[nome] = 'codice di tre lettere, per esempio EUR';
    } else if (c.tipo === 'lista') {
      if (c.obbligatorio && (!Array.isArray(v) || !v.length)) errori[nome] = 'scegli almeno una voce';
    } else if (c.tipo === 'lista_testo') pronto = String(v || '').split('\n').map(x => x.trim()).filter(Boolean);
    else if (c.tipo === 'interruttori') {
      if ((c.scelte || []).some(x => typeof v?.[x] !== 'boolean')) errori[nome] = 'scegli sì o no per ogni condizione';
    } else if (c.tipo === 'testo') {
      pronto = String(v || '').trim();
      if (c.massimo_char && pronto.length > c.massimo_char) errori[nome] = `massimo ${c.massimo_char} caratteri`;
      if (pronto.includes('{MANDATO:')) errori[nome] = 'testo riservato: rimuovi {MANDATO:';
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
  const [tab, setTab] = useState<'mandato' | 'diario'>('mandato');
  const [journalOpened, setJournalOpened] = useState(false);
  const selectTab = (next: 'mandato' | 'diario') => { setTab(next); if (next === 'diario') setJournalOpened(true); };
  useEffect(() => {
    const showMandato = () => setTab('mandato');
    window.addEventListener(RIFIUTO_RUN_EVENT, showMandato);
    return () => window.removeEventListener(RIFIUTO_RUN_EVENT, showMandato);
  }, []);
  return <div className="mandato-workspace">
    <nav className="mandato-tabs" role="tablist" aria-label="Mandato e Diario" onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'mandato' : event.key === 'End' ? 'diario' : tab === 'mandato' ? 'diario' : 'mandato';
      selectTab(next); document.getElementById('tab-' + next)?.focus();
    }}>
      <button id="tab-mandato" role="tab" aria-selected={tab === 'mandato'} aria-controls="panel-mandato" tabIndex={tab === 'mandato' ? 0 : -1} onClick={() => selectTab('mandato')}>Mandato</button>
      <button id="tab-diario" role="tab" aria-selected={tab === 'diario'} aria-controls="panel-diario" tabIndex={tab === 'diario' ? 0 : -1} onClick={() => selectTab('diario')}>Diario</button>
    </nav>
    <div id="panel-mandato" role="tabpanel" aria-labelledby="tab-mandato" hidden={tab !== 'mandato'}><MandatoForm /></div>
    <div id="panel-diario" role="tabpanel" aria-labelledby="tab-diario" hidden={tab !== 'diario'}>{journalOpened && <JournalPage />}</div>
  </div>;
}

function MandatoForm() {
  const [stato, setStato] = useState<StatoMandato | null>(null);
  const [form, setForm] = useState<Form>({});
  const [erroreCarica, setErroreCarica] = useState<string | null>(null);
  const [errori, setErrori] = useState<Record<string, string>>({});
  const [anteprima, setAnteprima] = useState<AnteprimaMandato | null>(null);
  const [messaggio, setMessaggio] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [bozza, setBozza] = useState(() => leggiBozza());
  const [metadati, setMetadati] = useState<Record<string, unknown>>({});
  const [rifiutoRun, setRifiutoRun] = useState(() => consumaDettaglioRun());
  const revisione = useRef(0);
  const messaggioRef = useRef<HTMLDivElement>(null);
  const portaAlMessaggio = () => setTimeout(() => messaggioRef.current?.focus(), 0);

  const carica = async () => {
    setBusy(true); setErroreCarica(null);
    try {
      const s = await Bellomberg.mandato(); setStato(s); setForm(formaDaValori(s.campi, s.valori)); setMetadati(metadatiDaValori(s.valori));
      setBozza(leggiBozza()); setDirty(false); setErrori(erroriServer((s.errori || []).join('\n'))); setAnteprima(null);
    } catch (e) { setErroreCarica(dettaglioLeggibile(e)); }
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
      salvaBozza({ baseImpronta: stato.impronta, form, metadati, salvataIl: new Date().toISOString() });
    }
  }, [dirty, form, metadati, stato]);

  const cambia = (nome: string, valore: any) => {
    revisione.current += 1; setAnteprima(null); setMessaggio(null); setDirty(true); setErrori(x => { const y = { ...x }; delete y[nome]; return y; });
    setForm(x => ({ ...x, [nome]: valore }));
  };
  const sostituisci = (nuovo: Form, nuoviMeta: Record<string, unknown>, sporco: boolean) => {
    revisione.current += 1; setForm(nuovo); setMetadati(nuoviMeta); setDirty(sporco); setAnteprima(null); setMessaggio(null); setErrori({});
  };
  const compilato = useMemo(() => stato ? payloadDaForm(stato.campi, form, metadati) : { valori: null, errori: {} }, [stato, form, metadati]);

  const prova = async () => {
    if (!stato || !compilato.valori) { setErrori(compilato.errori); setMessaggio('Correggi i campi evidenziati.'); portaAlMessaggio(); return; }
    const mia = ++revisione.current; setBusy(true); setErrori({}); setMessaggio(null);
    try { const a = await Bellomberg.mandatoAnteprima(compilato.valori); if (rispostaDellaRevisione(mia, revisione.current)) { setAnteprima(a); setMessaggio('Anteprima valida: ora puoi salvare questa revisione.'); } }
    catch (e) { if (rispostaDellaRevisione(mia, revisione.current)) { const d = dettaglioLeggibile(e); setErrori(erroriServer(d)); setMessaggio(d); portaAlMessaggio(); } }
    finally { setBusy(false); }
  };
  const salva = async () => {
    if (!stato || !compilato.valori || !anteprima) return;
    setBusy(true); setSaving(true); setMessaggio(null);
    try {
      await Bellomberg.salvaMandato(compilato.valori);
      const s = await Bellomberg.mandato();
      if (s.impronta !== anteprima.impronta) throw new Error(`salvataggio non confermato: impronta attesa ${anteprima.impronta}, riletta ${s.impronta || 'n.d.'}`);
      setStato(s); setForm(formaDaValori(s.campi, s.valori)); setMetadati(metadatiDaValori(s.valori));
      eliminaBozza(); setBozza(null); setDirty(false); setAnteprima(null); setErrori({});
      setMessaggio('Mandato salvato e riletto dal disco.');
    } catch (e) { const d = dettaglioLeggibile(e); setErrori(erroriServer(d)); setMessaggio(d); portaAlMessaggio(); }
    finally { setBusy(false); setSaving(false); }
  };

  if (!stato) return <div className="mandato-fault" role="alert"><b>MANDATO NON LEGGIBILE</b><pre>{erroreCarica || 'lettura in corso…'}</pre><button onClick={carica} disabled={busy}>RIPROVA</button></div>;
  return <div className="mandato-page">
    <header className="mandato-head">
      <div><span className="eyebrow">F18 // MANDATO DEL PM</span><h1>Le regole con cui lavora il comitato</h1><p>Definisci profilo, rischio e disciplina. I badge distinguono i limiti automatici dalle istruzioni al comitato.</p></div>
      <div className="mandato-trace"><span>IMPRONTA</span><b>{stato.impronta?.slice(0, 12) || 'NON DICHIARATO'}</b><small>{stato.dichiarato_il || 'mai salvato'} · {stato.origine || stato.causa || 'n.d.'}</small></div>
    </header>
    {stato.origine === 'esempio' && <div className="mandato-banner amber">PROFILO DI ESEMPIO ATTIVO · valido, ma non ancora personalizzato.</div>}
    {!stato.dichiarato && <div className="mandato-banner red">MANDATO {String(stato.causa || 'INCOMPLETO').toUpperCase()} · {stato.causa === 'assente' ? 'Compila il profilo per avviare il comitato. Puoi partire da un esempio e adattarlo.' : 'Completa o correggi i campi evidenziati; i valori presenti restano disponibili.'}{stato.dettaglio && <details><summary>Dettaglio della verifica</summary>{stato.dettaglio}</details>}</div>}
    {rifiutoRun && <div className="mandato-banner red" role="alert">AVVIO RIFIUTATO · {rifiutoRun}</div>}
    {bozza && <div className="mandato-banner draft"><div><b>BOZZA RECUPERABILE</b> · {new Date(bozza.salvataIl).toLocaleString('it-IT')}{bozza.baseImpronta !== stato.impronta && <strong> · IL MANDATO SU DISCO È CAMBIATO</strong>}</div><div><button disabled={saving} onClick={() => sostituisci({ ...bozza.form }, bozza.metadati || metadati, true)}>RIPRISTINA ESPLICITAMENTE</button><button disabled={saving} onClick={() => { eliminaBozza(); setBozza(null); }}>SCARTA</button></div></div>}
    <div className="mandato-shell">
      <nav className="mandato-index" aria-label="Sezioni del mandato">{BLOCCHI_MANDATO.map((b, i) => <a key={b} href={'#mandato-' + b} onClick={e => { e.preventDefault(); document.getElementById('mandato-' + b)?.scrollIntoView({ block: 'start' }); }}><span>{String(i + 1).padStart(2, '0')}</span>{TITOLI[b]}<i>{Object.values(stato.campi).filter(c => c.blocco === b).length}</i></a>)}</nav>
      <fieldset className="mandato-form" disabled={saving} aria-busy={saving}>
        {BLOCCHI_MANDATO.map((b, bi) => <section id={'mandato-' + b} key={b}>
          <h2><span>{String(bi + 1).padStart(2, '0')}</span>{TITOLI[b]}</h2>
          <div className="mandato-grid">{Object.entries(stato.campi).filter(([, c]) => c.blocco === b).map(([nome, c]) => {
            const cov = coperturaCampo(nome), v = form[nome];
            const aria = { 'aria-labelledby': 'ml-' + nome, 'aria-describedby': 'md-' + nome + (errori[nome] ? ' me-' + nome : ''), 'aria-invalid': !!errori[nome] };
            const base = <><div id={'ml-' + nome} className="field-label">{ETICHETTA(nome)}{c.obbligatorio && <sup>*</sup>}</div><div className={'coverage ' + cov.classe} title={cov.nota}>{cov.etichetta}</div><p id={'md-' + nome}>{c.descrizione}</p></>;
            let control: React.ReactNode;
            if (c.tipo === 'bool') control = <div className="mandato-choice" role="group" {...aria}>{[[true,'S\u00cc'],[false,'NO']].map(([x,l]) => <button key={l as string} type="button" className={v === x ? 'on' : ''} onClick={() => cambia(nome, x)}>{l as string}</button>)}</div>;
            else if (c.tipo === 'scelta') control = <select id={'m-'+nome} {...aria} value={v || ''} onChange={e => cambia(nome,e.target.value)}><option value="">-- scegli --</option>{c.scelte?.map(x=><option key={x}>{x}</option>)}</select>;
            else if (c.tipo === 'lista') control = <div className="mandato-checks" role="group" {...aria}>{c.scelte?.map(x=><label key={x}><input type="checkbox" checked={(v||[]).includes(x)} onChange={e=>cambia(nome,e.target.checked?[...(v||[]),x]:(v||[]).filter((y:string)=>y!==x))}/>{x}</label>)}</div>;
            else if (c.tipo === 'interruttori') control = <div className="mandato-switches" role="group" {...aria}>{c.scelte?.map(x=><div key={x}><span>{ETICHETTA(x)}</span>{[true,false].map(q=><button type="button" key={String(q)} className={v?.[x]===q?'on':''} onClick={()=>cambia(nome,{...v,[x]:q})}>{q?'S\u00cc':'NO'}</button>)}</div>)}</div>;
            else if (c.tipo === 'intervallo_pct' || c.tipo === 'intervallo_int') control = <div className="mandato-range" role="group" {...aria}><input aria-label={ETICHETTA(nome)+' minimo'} value={v?.[0]||''} inputMode="decimal" onChange={e=>cambia(nome,[e.target.value,v?.[1]||''])}/><span>&rarr;</span><input aria-label={ETICHETTA(nome)+' massimo'} value={v?.[1]||''} inputMode="decimal" onChange={e=>cambia(nome,[v?.[0]||'',e.target.value])}/><em>{c.unita}</em></div>;
            else if (c.tipo === 'lista_testo' || (c.tipo === 'testo' && (c.massimo_char || 0)>500)) control = <textarea id={'m-'+nome} {...aria} value={v||''} maxLength={c.massimo_char || undefined} onChange={e=>cambia(nome,e.target.value)} rows={c.tipo==='lista_testo'?4:6}/>;
            else control = <div className="mandato-input"><input id={'m-'+nome} {...aria} value={v??''} inputMode={['int','num','pct'].includes(c.tipo)?'decimal':undefined} maxLength={c.massimo_char||undefined} onChange={e=>cambia(nome,e.target.value)}/>{c.unita&&<span>{c.unita}</span>}</div>;
            return <article key={nome} className={errori[nome]?'invalid':''}>{base}{control}{errori[nome]&&<div id={'me-'+nome} className="field-error" role="alert">{errori[nome]}</div>}</article>;
          })}</div>
        </section>)}
      </fieldset>
      <aside className="mandato-preview">
        <div className="preview-title"><span>// ANTEPRIMA MODELLO</span><b>{anteprima ? 'VALIDA' : 'DA VALIDARE'}</b></div>
        <pre>{anteprima?.testo || 'Compila i sette blocchi e chiedi l’anteprima. Il testo apparirà qui solo dopo la validazione del server.'}</pre>
        {messaggio && <div ref={messaggioRef} tabIndex={-1} className="preview-message" role="status" aria-live="polite">{messaggio}</div>}
        <div className="preview-actions"><button onClick={prova} disabled={busy}>VALIDA + ANTEPRIMA</button><button className="primary" onClick={salva} disabled={busy || !anteprima || !compilato.valori}>SALVA MANDATO</button></div>
        <button className="example" onClick={()=>{ if(stato.esempio) sostituisci(formaDaValori(stato.campi,stato.esempio), {}, true); }} disabled={!stato.esempio || saving}>COMPILA CON PROFILO DI ESEMPIO</button>
        <button className="discard" onClick={()=>{ eliminaBozza();setBozza(null);sostituisci(formaDaValori(stato.campi,stato.valori),metadatiDaValori(stato.valori),false); }} disabled={saving}>SCARTA MODIFICHE</button>
      </aside>
    </div>
  </div>;
}
