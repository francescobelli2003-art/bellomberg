// Built app + production preload, hidden Electron. Synthetic HTTP state only.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const http = require('node:http'), { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '../..'), MARK = 'F18_RESULT ';
async function runner() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-f18-'));
  const fixture = JSON.parse(fs.readFileSync(path.join(root,'../src/bellomberg/resources/examples/mandato_pm.example.json'),'utf8'));
  const requests = []; let language='it', mode='', serial=1, entries=[], history=[];
  let values=fixture._esempio, fingerprint='synthetic-fingerprint-1';
  const state=()=>({ dichiarato:true, causa:null, dettaglio:null, campi_mancanti:[], valori:values,
    origine:'esempio', impronta:fingerprint, dichiarato_il:'2026-09-12', campi:fixture._campi, errori:[], esempio:fixture._esempio });
  const server=http.createServer(async(req,res)=>{
    res.setHeader('Access-Control-Allow-Origin','*');res.setHeader('Access-Control-Allow-Headers','Content-Type,X-BB-Token,X-BB-Language');
    res.setHeader('Access-Control-Allow-Methods','GET,POST,PUT,OPTIONS');res.setHeader('Content-Type','application/json');
    if(req.method==='OPTIONS'){res.end('{}');return;}
    let raw='';for await(const c of req)raw+=c;const body=raw?JSON.parse(raw):null;
    const url=new URL(req.url,'http://127.0.0.1'), route=url.pathname;
    if(route==='/__fixture'){if(body?.mode)mode=body.mode;res.end(JSON.stringify({requests,entries,history}));return;}
    requests.push({method:req.method,route,body,language:req.headers['x-bb-language']}); let out;
    if(route==='/health')out={status:'ok',version:'synthetic'};
    else if(route==='/auth/status')out={configured:true,default_pin:false};
    else if(route==='/preferences'){if(req.method==='PUT')language=body.language;out={language,selected:true,source:'preferences'};}
    else if(route==='/portfolio')out={positions:[],cash_disponibile_eur:null,cash_source:'uninitialized'};
    else if(route==='/fx')out={rates:{EUR:1}};
    else if(route==='/tasks/scheduled')out={tasks:[]};
    else if(route==='/db/backups')out={backups:[],count:0};
    else if(route==='/agents/list')out={agents:[],engines:{}};
    else if(route==='/mandato/anteprima'){fingerprint='synthetic-fingerprint-'+(++serial);out={testo:language==='it'?'Anteprima sintetica verificata':'Verified synthetic preview',impronta:fingerprint,origine:'personalizzato',output_language:language};}
    else if(route==='/mandato'){
      if(req.method==='PUT'){values=body;if(mode==='mismatch'){fingerprint='synthetic-mismatch';mode='';}}
      out=state();
    } else if(route==='/journal'&&req.method==='GET'){
      const filtered=entries.filter(e=>url.searchParams.get('status')==='all'||(url.searchParams.get('status')==='archived')===!!e.archived_at);
      out={items:filtered.map(e=>({...e,excerpt:e.body.slice(0,180)})),total:filtered.length,limit:30,offset:0};
    } else if(route==='/journal'&&req.method==='POST'){
      const e={...body,id:1,origin:'user',version:1,created_at:'2026-09-12T10:00:00Z',updated_at:'2026-09-12T10:00:00Z',archived_at:null};entries=[e];history=[{...e,entry_id:1,action:'create',saved_at:e.updated_at}];out=e;
    } else if(route==='/journal/1/versions')out={items:[...history].reverse(),total:history.length,limit:20,offset:0};
    else if(route==='/journal/1'&&req.method==='GET')out=entries[0];
    else if(route==='/journal/1'&&req.method==='PUT'||route==='/journal/1/archive'){
      const old=entries[0];
      if(mode==='conflict'){mode='';res.statusCode=409;out={detail:{code:'journal_version_conflict',message:'Synthetic version conflict',current_version:old.version}};}
      else {assert.equal(body.expected_version,old.version);const e={...old,...body,version:old.version+1,updated_at:'2026-09-12T11:00:00Z'};delete e.expected_version;
        if(route.endsWith('/archive')){e.archived_at=body.archived?'2026-09-12T11:00:00Z':null;delete e.archived;}
        entries=[e];history.push({...e,entry_id:1,action:route.endsWith('/archive')?(body.archived?'archive':'restore'):'update',saved_at:e.updated_at});out=e;}
    } else {res.statusCode=404;out={detail:'Unexpected synthetic endpoint '+route};}
    res.end(JSON.stringify(out));
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
  assert.ok(server.address().port>=8766);let output='';
  try{
    const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require('electron'),[__filename,'--renderer',JSON.stringify({temporary,origin})],{cwd:root,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.on('data',c=>output+=c);child.stderr.on('data',c=>output+=c);const timer=setTimeout(()=>child.kill(),55000);
    const code=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',resolve);});clearTimeout(timer);
    const line=output.split(/\r?\n/).find(x=>x.startsWith(MARK));assert.ok(line,output);const result=JSON.parse(line.slice(MARK.length));
    assert.equal(code,0,JSON.stringify(result));assert.equal(result.ok,true,JSON.stringify(result));
    assert.ok(requests.filter(r=>r.method!=='GET').every(r=>['/preferences','/mandato','/mandato/anteprima','/journal','/journal/1','/journal/1/archive'].includes(r.route)));
    console.log(JSON.stringify({temporary,...result,requests:requests.length}));
  }finally{fs.writeFileSync(path.join(temporary,'output.log'),output);fs.writeFileSync(path.join(temporary,'requests.json'),JSON.stringify(requests,null,2));server.closeAllConnections();await new Promise(r=>server.close(r));}
}
async function renderer(config){
  const {app,BrowserWindow}=require('electron');app.setPath('userData',path.join(config.temporary,'userdata'));app.disableHardwareAcceleration();
  process.env.BELLOMBERG_LAUNCH_ID='synthetic-f18';process.env.BELLOMBERG_DESKTOP_API_URL=config.origin;
  let w;const scenarios=[],errors=[];
  const js=(fn,...args)=>w.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const wait=async(fn)=>{const end=Date.now()+8000;while(Date.now()<end){if(await js(fn))return;await new Promise(r=>setTimeout(r,40));}throw Error('Timeout: '+await js(()=>document.body.innerText.slice(-2500)));};
  const click=selector=>js(s=>document.querySelector(s).click(),selector);
  const button=label=>js(l=>{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===l&&!b.closest('[hidden]'));if(!b)throw Error('Missing button '+l);b.click();},label);
  const field=(selector,value)=>js((s,v)=>{const e=document.querySelector(s),proto=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(e,v);e.dispatchEvent(new Event('input',{bubbles:true}));},selector,value);
  const control=body=>new Promise((resolve,reject)=>{const req=http.request(config.origin+'/__fixture',{method:'POST',headers:{'Content-Type':'application/json'}},res=>{let s='';res.on('data',c=>s+=c);res.on('end',()=>resolve(JSON.parse(s)));});req.on('error',reject);req.end(JSON.stringify(body||{}));});
  try{
    await app.whenReady();w=new BrowserWindow({show:false,width:1500,height:1000,webPreferences:{preload:path.join(root,'dist-electron/preload.mjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false,additionalArguments:['--bellomberg-launch-id=synthetic-f18','--bellomberg-api-port='+new URL(config.origin).port]}});
    w.webContents.on('preload-error',(_e,_p,error)=>errors.push(String(error)));
    w.webContents.session.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*','ws://*/*','wss://*/*']},(d,c)=>c({cancel:!d.url.startsWith(config.origin+'/')}));
    await w.loadFile(path.join(root,'dist/index.html'),{hash:'/mandato'});await js(()=>{localStorage.setItem('bellomberg_token_v1','synthetic');localStorage.setItem('bellomberg_unlocked_v1',JSON.stringify({ts:Date.now()}));localStorage.setItem('bellomberg_last_launch_id','synthetic-f18');});
    await new Promise(r=>{w.webContents.once('did-finish-load',r);w.webContents.reload();});await wait(()=>document.querySelector('#m-orizzonte_anni'));
    assert.equal(await js(()=>document.querySelectorAll('.mandato-grid article').length),47);
    assert.equal(await js(()=>document.querySelectorAll('.mandato-form section:not([hidden])').length),1);
    await field('#m-orizzonte_anni','7');await click('#tab-diario');await wait(()=>document.querySelector('.journal-title-input'));await click('#tab-mandato');assert.equal(await js(()=>document.querySelector('#m-orizzonte_anni').value),'7');
    scenarios.push('47 fields, one active section, tab draft preserved');
    await click('.mandato-index a[href="#mandato-rischio"]');await wait(()=>document.activeElement?.id==='mandato-heading-rischio');
    assert.equal(await js(()=>location.hash),'#/mandato');assert.equal(await js(()=>document.querySelectorAll('.mandato-choice button[aria-pressed]').length>0),true);
    scenarios.push('keyboard focus and section navigation preserve route; boolean pressed state exposed');
    await click('[data-action="preview"]');await wait(()=>!document.querySelector('[data-action="save"]').disabled);await click('[data-action="save"]');await wait(()=>document.querySelector('.preview-message')?.textContent.includes('salvato e riletto'));
    const saved=await control();assert.equal(saved.requests.filter(r=>r.route==='/mandato'&&r.method==='PUT').length,1);assert.equal(saved.requests.find(r=>r.route==='/mandato'&&r.method==='PUT').body.profilo.orizzonte_anni,7);
    scenarios.push('mandatory preview, frozen values, one PUT and matching fingerprint readback');
    await field('#m-orizzonte_anni','8');await click('[data-action="preview"]');await wait(()=>!document.querySelector('[data-action="save"]').disabled);await control({mode:'mismatch'});await click('[data-action="save"]');await wait(()=>document.querySelector('.preview-message')?.textContent.includes('non confermato'));assert.equal(await js(()=>document.querySelector('#m-orizzonte_anni').value),'8');
    await new Promise(r=>setTimeout(r,150));await js(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});fs.writeFileSync(path.join(config.temporary,'f18-mandato-it.png'),(await w.capturePage(undefined,{stayHidden:true})).toPNG());
    scenarios.push('mismatched readback remains an error and preserves draft');
    await click('#tab-diario');await field('.journal-title-input','Originale α 1,25');await field('.journal-body-label textarea','Testo originale\nEvidenza sintetica');await button('Salva nota');await wait(()=>document.querySelector('.journal-message')?.textContent.includes('Versione 1'));
    await field('.journal-body-label textarea','Seconda versione originale');await control({mode:'conflict'});await button('Salva nuova versione');await wait(()=>document.querySelector('.journal-warning')?.textContent.includes('Confrontala'));
    assert.equal(await js(()=>document.querySelector('.journal-body-label textarea').value),'Seconda versione originale');await button('Confronta versione corrente');await wait(()=>document.querySelector('.journal-conflict'));await button('Mantieni la mia bozza sulla versione corrente');await button('Salva nuova versione');await wait(()=>document.querySelector('.journal-message')?.textContent.includes('Versione 2'));
    assert.equal((await control()).history.length,2);await button('Archivia nota');await wait(()=>[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='Ripristina dall’archivio'));assert.equal(await js(()=>document.querySelector('.journal-fields').disabled),true);await button('Ripristina dall’archivio');await wait(()=>!document.querySelector('.journal-fields').disabled);
    await new Promise(r=>setTimeout(r,150));await js(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});fs.writeFileSync(path.join(config.temporary,'f18-diario-it.png'),(await w.capturePage(undefined,{stayHidden:true})).toPNG());
    scenarios.push('journal create/revision, 409 keeps original draft, comparison, reversible archive and immutable history');
    await js(()=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'F19',bubbles:true})));await wait(()=>document.querySelector('#language-title')&&document.querySelector('input[name=language][value=en]:not(:disabled)'));
    await click('input[name=language][value=en]');await button('SALVA LINGUA');await wait(()=>document.documentElement.lang==='en');await js(()=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
    await wait(()=>document.querySelector('.journal-library h2')?.textContent==='Your notes');assert.equal(await js(()=>document.querySelector('.journal-title-input').value),'Originale α 1,25');
    await new Promise(r=>setTimeout(r,150));await js(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});fs.writeFileSync(path.join(config.temporary,'f18-diario-en.png'),(await w.capturePage(undefined,{stayHidden:true})).toPNG());
    scenarios.push('saved language changes controls while original note remains unchanged');
    for(const width of [1500,900,720]){w.setSize(width,1000);await new Promise(r=>setTimeout(r,60));assert.equal(await js(()=>document.documentElement.scrollWidth<=innerWidth),true);}
    scenarios.push('no document horizontal overflow at 1500/900/720px');
    w.setSize(1500,1000);await click('#tab-mandato');await wait(()=>document.querySelector('#m-orizzonte_anni'));await new Promise(r=>setTimeout(r,200));await js(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});fs.writeFileSync(path.join(config.temporary,'f18-mandato-en.png'),(await w.capturePage(undefined,{stayHidden:true})).toPNG());
    scenarios.push('captured Mandato and Diario in IT and EN');
    assert.deepEqual(errors,[]);const p=w.webContents.getLastWebPreferences();assert.ok(p.sandbox&&p.contextIsolation&&!p.nodeIntegration);
    console.log(MARK+JSON.stringify({ok:true,scenarios}));app.exit(0);
  }catch(e){console.log(MARK+JSON.stringify({ok:false,error:String(e),scenarios,errors,page:w?await js(()=>document.body.innerText.slice(-2500)).catch(String):null}));app.exit(1);}
}
if(process.argv.includes('--renderer'))renderer(JSON.parse(process.argv[process.argv.indexOf('--renderer')+1]));else runner().catch(e=>{console.error(e);process.exitCode=1;});
