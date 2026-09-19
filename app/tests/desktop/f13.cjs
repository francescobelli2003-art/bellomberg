// Built renderer and production preload, adapted from f18.cjs. Synthetic local HTTP only.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const http = require('node:http'), { spawn } = require('node:child_process');
const root = path.resolve(__dirname, '../..'), MARK = 'F13_RESULT ';
function fixture() {
  const point = (id, rate) => ({ run_id: id ? `memo:${id}` : null, memo_id: id, completed_at: '2026-09-12T12:00:00Z', computed_at: '2026-09-12T11:00:00Z',
    n: 20, hits: rate / 5, hit_rate_pct: rate, avg_edge_pct: 2.5, ci95: { low_pct: 35, high_pct: 80 }, quality: ['ok'], comparison_key: null,
    source: 'Synthetic fixture', window_days: 270, maturation_days: 7, n_fetch_fail: 0, n_unmeasurable: 3, n_directional_candidates: 23,
    delta: { available: false, hit_rate_pp: null, reason: 'Synthetic comparison unavailable' },
    operational: { status: 'done', models: ['synthetic-model'], usage: { cost_eur: 0, duration_s: 2, api_calls: 1, status: 'ok', partial: false } } });
  const points = [point(1, 50), point(2, 65)];
  return { source: 'Synthetic fixture', paid_analysis: false, history: { state: 'ok', count: 2, available: true, first_captured_at: '2026-09-01T10:00:00Z', note: 'Synthetic history' },
    trend: { available: true, reason: null }, current_scorecard: { available: true, computed_at: '2026-09-12T13:00:00Z', stato: 'ok', error: null },
    agents: [{ id: 'capo', label: 'Synthetic Committee', role: 'Ruolo sintetico', attribution: 'collective', latest: points[1], current: point(null,75), series: points, delta: points[1].delta },
      { id: 'red_team', label: 'Red Team', role: 'Synthetic control', attribution: 'unsupported', latest: null, series: [], delta: points[1].delta }],
    runs: points.map(p => ({ ...p, captured_at: p.completed_at, score_error: null, review_status: p.memo_id === 2 ? 'duplicate' : 'unmarked', review_note: p.memo_id === 2 ? 'Original duplicate evidence' : null,
      output_language: 'it', reflection: { text: 'Lezione originale sintetica 158,50. '.repeat(100), status: 'generated', kind: 'suggestion', implementation_verified: p.memo_id === 2, performance_proven: false },
      scorecard: { degraded: false, by_action: { BUY: { n: 2, hits: 1, hit_rate_pct: 50, avg_edge_pct: 1, small_sample: true } },
        by_confidence: { ALTA: { n: 1, hits: 1, hit_rate_pct: 100, avg_edge_pct: 2 } }, by_confidence_scartate: { n: 1, etichette: { 'MEDIUM-HIGH': 1 }, motivo: 'Original exclusion reason' },
        details: [{ id: 11, ticker: 'SYNTH', action: 'BUY', date: '2026-09-01', confidence: 'HIGH', confidence_bucket: 'ALTA', direction: 'long', edge_pct: 2, hit: true, horizon_used: '4w', ret_1w_pct: 1, ret_4w_pct: 2, specialists: ['synthetic'], status: 'open' }] } })),
    method: { score: 'Synthetic score method', attribution: 'Synthetic attribution', horizon: 'Synthetic horizon', comparison: 'Synthetic comparison', learning: 'Synthetic learning', timing: 'Synthetic timing' },
    _presentation_v1: { version: 1, texts: [{ path: ['agents',0,'role'], it: 'Ruolo sintetico', en: 'Synthetic role' }] } };
}
async function runner() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bellomberg-f13-'));
  const requests = []; let language='it', mode='';
  const server=http.createServer(async(req,res)=>{
    res.setHeader('Access-Control-Allow-Origin','*');res.setHeader('Access-Control-Allow-Headers','Content-Type,X-BB-Token,X-BB-Language');
    res.setHeader('Access-Control-Allow-Methods','GET,PUT,OPTIONS');res.setHeader('Content-Type','application/json');
    if(req.method==='OPTIONS'){res.end('{}');return;}
    let raw='';for await(const c of req)raw+=c;const body=raw?JSON.parse(raw):null;
    const route=new URL(req.url,'http://127.0.0.1').pathname;
    if(route==='/__fixture'){if(body?.mode)mode=body.mode;res.end(JSON.stringify({requests}));return;}
    requests.push({method:req.method,route,body,language:req.headers['x-bb-language']});let out;
    if(route==='/health')out={status:'ok',version:'synthetic'};
    else if(route==='/auth/status')out={configured:true,default_pin:false};
    else if(route==='/preferences'){if(req.method==='PUT')language=body.language;out={language,selected:true,source:'preferences'};}
    else if(route==='/portfolio')out={positions:[],cash_disponibile_eur:null,cash_source:'uninitialized'};
    else if(route==='/fx')out={rates:{EUR:1}};
    else if(route==='/tasks/scheduled')out={tasks:[]};
    else if(route==='/db/backups')out={backups:[],count:0};
    else if(route==='/agents/list')out={agents:[],engines:{}};
    else if(route==='/mandato')out={dichiarato:true,causa:null,dettaglio:null,campi_mancanti:[],valori:{},origine:'esempio',impronta:'synthetic-f13',dichiarato_il:'2026-09-12',campi:[],errori:[],esempio:{}};
    else if(route==='/agents/progress'){
      out=fixture();
      if(mode==='error'){res.statusCode=503;out={detail:[{msg:'Synthetic archive unavailable'}]};}
      if(mode==='empty'){out.runs=[];out.history={...out.history,available:false,count:0};out.agents.forEach(a=>{a.series=[];a.latest=null;});}
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
    assert.ok(requests.filter(r=>r.method!=='GET').every(r=>r.route==='/preferences'&&r.method==='PUT'));
    console.log(JSON.stringify({temporary,...result,requests:requests.length}));
  }finally{fs.writeFileSync(path.join(temporary,'output.log'),output);fs.writeFileSync(path.join(temporary,'requests.json'),JSON.stringify(requests,null,2));server.closeAllConnections();await new Promise(r=>server.close(r));}
}
async function renderer(config){
  const {app,BrowserWindow}=require('electron');app.setPath('userData',path.join(config.temporary,'userdata'));app.disableHardwareAcceleration();
  process.env.BELLOMBERG_LAUNCH_ID='synthetic-f13';process.env.BELLOMBERG_DESKTOP_API_URL=config.origin;
  let w;const scenarios=[],errors=[];
  const js=(fn,...args)=>w.webContents.executeJavaScript(`(${fn.toString()})(...${JSON.stringify(args)})`);
  const wait=async(fn)=>{const end=Date.now()+8000;while(Date.now()<end){if(await js(fn))return;await new Promise(r=>setTimeout(r,40));}throw Error('Timeout: '+await js(()=>document.body.innerText.slice(-2500)));};
  const click=selector=>js(s=>document.querySelector(s).click(),selector);
  const select=value=>js(v=>{const e=document.querySelector('.ap-run-select select');e.value=v;e.dispatchEvent(new Event('change',{bubbles:true}));},value);
  const control=body=>new Promise((resolve,reject)=>{const req=http.request(config.origin+'/__fixture',{method:'POST',headers:{'Content-Type':'application/json'}},res=>{let s='';res.on('data',c=>s+=c);res.on('end',()=>resolve(JSON.parse(s)));});req.on('error',reject);req.end(JSON.stringify(body||{}));});
  try{
    await app.whenReady();w=new BrowserWindow({show:false,width:1500,height:1000,webPreferences:{preload:path.join(root,'dist-electron/preload.mjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false,additionalArguments:['--bellomberg-launch-id=synthetic-f13','--bellomberg-api-port='+new URL(config.origin).port]}});
    w.webContents.on('preload-error',(_e,_p,error)=>errors.push(String(error)));
    w.webContents.session.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*','ws://*/*','wss://*/*']},(d,c)=>c({cancel:!d.url.startsWith(config.origin+'/')}));
    await w.loadFile(path.join(root,'dist/index.html'),{hash:'/agent-progress'});await js(()=>{localStorage.setItem('bellomberg_token_v1','synthetic');localStorage.setItem('bellomberg_unlocked_v1',JSON.stringify({ts:Date.now()}));localStorage.setItem('bellomberg_last_launch_id','synthetic-f13');});
    await new Promise(r=>{w.webContents.once('did-finish-load',r);w.webContents.reload();});await wait(()=>document.querySelector('.ap-metrics'));
    assert.equal(await js(()=>document.querySelector('.ap-header h1').textContent),'Risultati con evidenza');
    assert.match(await js(()=>document.body.innerText),new RegExp('v'+require(path.join(root,'package.json')).version.replaceAll('.','\\.')+' OBSIDIAN'));
    assert.equal(await js(()=>document.querySelector('.ap-metrics strong').textContent),'65,0%');
    assert.equal(await js(()=>document.querySelectorAll('.ap-tabs [role=tab]').length),5);
    w.setSize(1501,1000);await new Promise(r=>setTimeout(r,60));w.setSize(1500,1000);await new Promise(r=>setTimeout(r,150));await js(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});fs.writeFileSync(path.join(config.temporary,'f13-it.png'),(await w.capturePage(undefined,{stayHidden:true})).toPNG());
    await click('.ap-lessons summary');assert.match(await js(()=>document.querySelector('.ap-lesson-text').textContent),/Lezione originale sintetica/);
    assert.equal(await js(()=>document.querySelector('.ap-lesson-text').scrollHeight>document.querySelector('.ap-lesson-text').clientHeight),true);
    const before=(await control()).requests.filter(r=>r.route==='/agents/progress').length;
    await js(()=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'F19',bubbles:true})));await wait(()=>document.querySelector('#language-title')&&document.querySelector('input[name=language][value=en]:not(:disabled)'));
    await click('input[name=language][value=en]');await js(()=>[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='SALVA LINGUA').click());
    await wait(()=>document.documentElement.lang==='en');await js(()=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
    await wait(()=>document.querySelector('.ap-header h1')?.textContent==='Results with evidence');
    assert.equal((await control()).requests.filter(r=>r.route==='/agents/progress').length,before);
    assert.match(await js(()=>document.querySelector('.ap-detail-header p').textContent),/Synthetic role/);
    assert.match(await js(()=>document.querySelector('.ap-lesson-text').textContent),/Lezione originale sintetica/);
    assert.equal(await js(()=>document.querySelector('.ap-metrics strong').textContent),'65.0%');
    scenarios.push('IT/EN controls and presentation metadata, unchanged archived lesson, no extra progress GET');
    await select('current');assert.equal(await js(()=>document.querySelector('.ap-metrics strong').textContent),'75.0%');
    await click('#ap-tab-action');assert.match(await js(()=>document.querySelector('#ap-panel-action').innerText),/Breakdown unavailable/);
    await select('memo:2');assert.match(await js(()=>document.querySelector('#ap-panel-action').innerText),/Small sample/);
    assert.match(await js(()=>document.querySelector('.ap-detail').innerText),/Original duplicate evidence/);
    await js(()=>{const t=document.querySelector('#ap-tab-action');t.focus();t.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));});
    assert.equal(await js(()=>document.activeElement.id),'ap-tab-confidence');assert.match(await js(()=>document.querySelector('#ap-panel-confidence').innerText),/MEDIUM-HIGH/);
    await click('#ap-tab-calls');assert.match(await js(()=>document.querySelector('#ap-panel-calls').innerText),/HIGH/);assert.match(await js(()=>document.querySelector('#ap-panel-calls').innerText),/SYNTH/);
    await click('#ap-tab-activity');assert.match(await js(()=>document.querySelector('#ap-panel-activity').innerText),/synthetic-model/);
    await click('#ap-tab-measurements');await click('.ap-trend-panel summary');
    await js(()=>{const c=document.querySelector('.ap-chart circle[role=button]');c.focus();c.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));});
    assert.equal(await js(()=>document.querySelector('.ap-run-select select').value),'memo:1');
    assert.equal(await js(()=>document.querySelectorAll('.ap-chart .ap-series').length),0);
    scenarios.push('current separate; duplicate preserved; five tabs, scarti/raw confidence, keyboard focus and exact chart run selection');
    // Lettura esatta sul grafico: focusin esplicito perche' la finestra nascosta non garantisce l'evento di focus.
    const chart=()=>js(()=>{const s=document.querySelector('.ap-chart svg'),c=[...s.querySelectorAll('circle[role=button]')],x=s.querySelector(':scope > line.ap-midline');
      return {reading:document.querySelector('.ap-chart-reading')?.textContent??null,cross:x&&x.getAttribute('x1')===x.getAttribute('x2')?x.getAttribute('x1'):null,cx:c.map(e=>e.getAttribute('cx')),active:c.indexOf(document.activeElement),selected:document.querySelector('.ap-run-select select').value};});
    await js(()=>{const c=document.querySelector('.ap-chart circle[role=button]');c.focus();c.dispatchEvent(new FocusEvent('focusin',{bubbles:true}));});
    let reading=await chart();assert.match(String(reading.reading),/^Memo 1 · 50\.0% · n=20 · measured .+ · 95% interval 35\.0–80\.0% · Measurement available$/,JSON.stringify(reading));assert.equal(reading.cross,reading.cx[0]);
    await js(()=>document.querySelector('.ap-chart circle[role=button]').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true})));
    reading=await chart();assert.match(String(reading.reading),/^Memo 2 · 65\.0% · n=20 /,JSON.stringify(reading));assert.equal(reading.cross,reading.cx[1]);assert.equal(reading.active,1);assert.equal(reading.selected,'memo:1');
    await js(()=>{const s=document.querySelector('.ap-chart svg'),r=s.getBoundingClientRect(),c=s.querySelector('circle[role=button]').getBoundingClientRect();s.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:c.left+c.width/2,clientY:r.top+r.height/2}));});
    await wait(()=>document.querySelector('.ap-chart-reading')?.textContent.startsWith('Memo 1 '));reading=await chart();assert.match(String(reading.reading),/^Memo 1 · 50\.0% /,JSON.stringify(reading));assert.equal(reading.cross,reading.cx[0]);assert.equal(reading.selected,'memo:1');
    await js(()=>document.querySelector('.ap-chart svg').dispatchEvent(new PointerEvent('pointerout',{bubbles:true,relatedTarget:document.querySelector('.ap-chart-key')})));
    await wait(()=>!document.querySelector('.ap-chart-reading'));reading=await chart();assert.equal(reading.reading,null);assert.equal(reading.cross,null);
    scenarios.push('chart reading: exact rate, n, date, interval and quality on focus, arrows and pointer; crosshair on the read run; cleared on leave; selection unchanged');
    for(const width of [1500,900,720]){w.setSize(width,1000);await new Promise(r=>setTimeout(r,100));assert.equal(await js(()=>document.documentElement.scrollWidth<=innerWidth),true);}
    w.setSize(1500,1000);await new Promise(r=>setTimeout(r,150));await js(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});fs.writeFileSync(path.join(config.temporary,'f13-en.png'),(await w.capturePage(undefined,{stayHidden:true})).toPNG());
    // 13/09: scatto con la lettura accesa (puntatore sul secondo punto), per la revisione visiva e le catture delle guide
    await js(()=>{const s=document.querySelector('.ap-chart svg'),r=s.getBoundingClientRect(),c=s.querySelectorAll('circle[role=button]')[1].getBoundingClientRect();s.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:c.left+c.width/2,clientY:r.top+r.height/2}));});
    await wait(()=>document.querySelector('.ap-chart-reading')?.textContent.startsWith('Memo 2 '));
    await js(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});
    fs.writeFileSync(path.join(config.temporary,'f13-reading-en.png'),(await w.capturePage(undefined,{stayHidden:true})).toPNG());
    await js(()=>document.querySelector('.ap-chart svg').dispatchEvent(new PointerEvent('pointerout',{bubbles:true,relatedTarget:document.querySelector('.ap-chart-key')})));await wait(()=>!document.querySelector('.ap-chart-reading'));
    scenarios.push('long archived lesson scrolls; no page overflow at 1500/900/720px; captured IT and EN PNG');
    await control({mode:'error'});await click('.ap-refresh');await wait(()=>document.querySelector('.ap-error'));
    assert.match(await js(()=>document.querySelector('.ap-error').innerText),/Synthetic archive unavailable/);
    await control({mode:'empty'});await click('.ap-refresh');await wait(()=>document.querySelector('.ap-evidence-strip'));
    assert.match(await js(()=>document.querySelector('.agent-progress').innerText),/History begins here/);
    scenarios.push('structured HTTP error and empty history are explicit');
    assert.deepEqual(errors,[]);const p=w.webContents.getLastWebPreferences();assert.ok(p.sandbox&&p.contextIsolation&&!p.nodeIntegration);
    console.log(MARK+JSON.stringify({ok:true,scenarios}));app.exit(0);
  }catch(e){console.log(MARK+JSON.stringify({ok:false,error:String(e),scenarios,errors,page:w?await js(()=>document.body.innerText.slice(-2500)).catch(String):null}));app.exit(1);}
}
if(process.argv.includes('--renderer'))renderer(JSON.parse(process.argv[process.argv.indexOf('--renderer')+1]));else runner().catch(e=>{console.error(e);process.exitCode=1;});
