const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync(require('path').join(__dirname, '../web/index.html'), 'utf8');
const source = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));
const tick = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));
const defaults = () => ({prio:{}, seen:{}, seenAt:{}, members:{}, removed:{}, order:[], initAt:null, lang:null, theme:null, notify:false, muteWhenFocused:false});

function harness(initial = {}, missing = false){
  const elements = new Map(), handlers = {}, unload = {}, requests = [], downloads = [];
  const node = () => ({textContent:'', innerHTML:'', value:'', hidden:false, dataset:{}, style:{},
    classList:{add(){}, remove(){}, toggle(){}, contains(){return false;}},
    setAttribute(){}, removeAttribute(){}, querySelectorAll(){return [];}, focus(){}, click(){},
    querySelector(){return null;}, getBoundingClientRect(){return {top:0,height:0};}});
  const envelope = {version:1, revision:0, settings:{...defaults(), initAt:'2026-10-14T00:00:00Z', ...initial}};
  const state = {envelope, fail:null, confirm:true, pending:null};
  const ctx = {atob, console, Date, Promise, JSON, Math, Set, Map, Object, Array, String, Number, Blob,
    document:{documentElement:{dataset:{}}, title:'',
      getElementById:id => {if (!elements.has(id)) elements.set(id,node()); return elements.get(id);},
      querySelector:() => null, querySelectorAll:() => [], createElement:() => node(),
      addEventListener:(type, fn) => handlers[type]=fn, body:node()},
    localStorage:{getItem(){throw new Error('Must not read browser settings');}, setItem(){throw new Error('Must not save browser settings');}},
    sessionStorage:{getItem:() => null, setItem(){}, removeItem(){}},
    navigator:{language:'en',clipboard:{writeText:async()=>{}}},
    window:{confirm:() => state.confirm}, URL:{createObjectURL:blob => {downloads.push(blob); return 'blob:test';}, revokeObjectURL(){}},
    location:{protocol:'http:'}, setInterval(){}, setTimeout:(f,ms) => ms < 1000 ? f() : 0, clearTimeout(){},
    requestAnimationFrame(){}, cancelAnimationFrame(){}, addEventListener:(type,fn) => unload[type]=fn,
    fetch:async(url, opts={}) => {
      requests.push({url,...opts});
      if (url !== 'api/settings') return {ok:false,status:500};
      if (state.pending){ const wait=state.pending; state.pending=null; await wait; }
      if (state.fail) return {ok:false,status:state.fail};
      if (opts.method === 'PUT'){
        assert.strictEqual(opts.headers['X-Taskboard-Token'], 'test-key');
        const input=JSON.parse(opts.body);
        if (input.revision !== state.envelope.revision) return {ok:false,status:409};
        let settings = input.settings;
        if (settings.settings){
          if (settings.version !== 1) return {ok:false,status:400};
          settings = settings.settings;
        }
        state.envelope={version:1,revision:input.revision+1,settings:{...defaults(),...settings}};
      }
      const result=plain(state.envelope);
      return {ok:true,status:200,json:async()=>result};
    }};
  const boot={token:'test-key',snapshot:missing ? null : plain(envelope)};
  if (missing) state.fail=500;
  vm.createContext(ctx);
  for (const asset of ['vendor/markdown-it.min.js','reply-reader.js'])
    vm.runInContext(fs.readFileSync(require('path').join(__dirname,'../web',asset),'utf8'),ctx);
  vm.runInContext(source.replace('/*DATA*/null', JSON.stringify({generated:new Date().toISOString(),tasks:[]}))
    .replace('/*PERSIST*/null',JSON.stringify(boot)),ctx);
  const run = code => vm.runInContext(code,ctx);
  return {ctx,run,state,requests,downloads,elements,handlers,unload};
}

(async()=>{
  // Opening a client queues only a read mark. It must not create a leave-page
  // prompt, even while that save is pending or has failed.
  const opened=harness({members:{one:true}});
  opened.run("DATA.tasks.push({id:'one', agent:'codex', states:['reply'], last_reply:'2026-10-14T01:00:00Z'})");
  let finishReadSave;
  opened.state.pending=new Promise(resolve=>finishReadSave=resolve);
  const link={dataset:{openSession:'one'}};
  opened.handlers.click({target:{id:'',closest:selector=>selector==='[data-open-session]' ? link : null}});
  let readPrompt=false;
  opened.unload.beforeunload({preventDefault(){readPrompt=true;}});
  assert.strictEqual(readPrompt,false,'Opening a client must not warn about its own read mark');
  opened.state.fail=500; finishReadSave(); await opened.run('settingsPromise');
  assert.strictEqual(opened.run('store.seen.one'),'2026-10-14T01:00:00Z');
  assert.ok(opened.run('store.seenAt.one'),'Read action time is separate from the reply cutoff');
  opened.unload.beforeunload({preventDefault(){readPrompt=true;}});
  assert.strictEqual(readPrompt,false,'A failed read mark must not block opening a client');
  opened.run("store.prio.one='high'; save()");
  let boardPrompt=false;
  opened.unload.beforeunload({preventDefault(){boardPrompt=true;}});
  assert.ok(boardPrompt,'Opening a client must not suppress protection for unsaved grouping changes');

  const h=harness({lang:'zh',theme:'light',prio:{one:'high'},members:{one:true},order:['one'],seen:{one:'2026-10-14T01:00:00Z'}});
  assert.strictEqual(h.run('theme()'),'light');
  assert.strictEqual(h.run('lang()'),'zh');
  assert.deepStrictEqual(plain(h.run('store.order')),['one']);
  assert.ok(h.run('store.seen.one'));
  assert.deepStrictEqual(plain(h.run('store.seenAt')),{},'Old settings require no migration step');
  h.run("markSeen('one','2026-10-14T01:00:00Z'); save()");
  await h.run('settingsPromise');
  const readAt=h.run('store.seenAt.one');
  assert.strictEqual(h.state.envelope.settings.seenAt.one,readAt);
  assert.strictEqual(harness(h.state.envelope.settings).run('store.seenAt.one'),readAt);
  h.requests.length=0;

  // Serialize rapid edits. A response for the first edit must not overwrite a later one.
  let release;
  h.state.pending=new Promise(resolve=>release=resolve);
  h.run("store.prio.one='later'; save()");
  h.run("store.theme='dark'; save()");
  release(); await h.run('settingsPromise');
  const puts=h.requests.filter(r=>r.method==='PUT').map(r=>JSON.parse(r.body));
  assert.deepStrictEqual(puts.map(p=>p.revision),[1,2]);
  assert.strictEqual(h.state.envelope.settings.theme,'dark');
  assert.strictEqual(h.state.envelope.settings.prio.one,'later');
  assert.strictEqual(h.run('pendingSettings'),null);

  // Failure is visible; queued edits survive and retry saves them.
  h.state.fail=500;
  h.run("store.lang='en'; save()"); await h.run('settingsPromise');
  assert.strictEqual(h.run('settingsProblem'),'save');
  assert.strictEqual(h.state.envelope.settings.lang,'zh');
  assert.strictEqual(h.run('store.lang'),'en');
  let prevented=false;
  h.unload.beforeunload({preventDefault(){prevented=true;}});
  assert.ok(prevented,'Unsaved changes must warn before closing');
  h.state.fail=null; await h.run('retrySettings()');
  assert.strictEqual(h.state.envelope.settings.lang,'en');

  // An old page cannot replace settings saved by another page.
  h.state.envelope.revision++;
  h.state.envelope.settings.lang='zh';
  h.run("store.theme='light'; save()"); await h.run('settingsPromise');
  assert.strictEqual(h.run('settingsProblem'),'conflict');
  assert.strictEqual(h.run('store.theme'),'light','Keep unsaved state available for export');
  const calls=h.requests.length;
  h.run("store.lang='en'; save()"); await tick();
  assert.strictEqual(h.requests.length,calls,'Conflict must not be blindly retried');
  await h.run('retrySettings()');
  assert.strictEqual(h.run('store.lang'),'zh');
  assert.strictEqual(h.run('settingsProblem'),null);

  // Export preserves the entire current settings object.
  h.run('exportSettings()');
  assert.deepStrictEqual(JSON.parse(await h.downloads[0].text()).settings,plain(h.run('store')));
  const file = value => ({size:100,text:async()=>JSON.stringify(value)});
  h.ctx.importFile=file({version:1,settings:{members:{migrated:true},prio:{migrated:'later'},order:['migrated'],removed:{old:true},lang:'en',theme:'light',initAt:'2026-10-13T00:00:00Z'}});
  h.state.confirm=false;
  const beforeCancel=plain(h.state.envelope);
  await h.run('importSettingsFile(importFile)');
  assert.deepStrictEqual(h.state.envelope,beforeCancel);
  h.state.confirm=true; await h.run('importSettingsFile(importFile)');
  assert.deepStrictEqual(plain(h.run('store.members')),{migrated:true});
  assert.deepStrictEqual(h.state.envelope.settings,plain(h.run('store')));
  h.ctx.importFile=file({version:99,settings:{members:{wrong:true}}});
  const beforeInvalid=plain(h.run('store'));
  await h.run('importSettingsFile(importFile)');
  assert.strictEqual(h.run('settingsProblem'),'import');
  assert.deepStrictEqual(plain(h.run('store')),beforeInvalid);
  h.run("store.theme='dark'; save()"); await h.run('settingsPromise');
  assert.strictEqual(h.state.envelope.settings.theme,'dark','An invalid import must not disable later saves');
  h.run("store.theme='light'; save()"); await h.run('settingsPromise');

  // A failed initial load never initializes an empty replacement board.
  const failed=harness({},true); await tick();
  assert.strictEqual(failed.run('settingsReady'),false);
  failed.handlers.click({target:{id:'langToggle',dataset:{},closest(){return null;}}});
  assert.strictEqual(failed.requests.filter(r=>r.method==='PUT').length,0);
  failed.state.fail=null; await failed.run('retrySettings()');
  assert.strictEqual(failed.run('settingsReady'),true);

  // Reload obtains persisted settings without reading localStorage.
  const restarted=harness(h.state.envelope.settings);
  assert.deepStrictEqual(plain(restarted.run('store.members')),{migrated:true});
  assert.strictEqual(restarted.run('theme()'),'light');
  {
    // 保存进行中点“在 Codex 中打开”：跳到应用不算离开页面，不弹“是否离开”；真正关闭且有未保存内容时照常提醒
    const a=harness({members:{one:true}});
    let releaseAppSave; a.state.pending=new Promise(r => { releaseAppSave=r; });
    a.handlers.click({target:{dataset:{}, closest:sel => sel === '[data-open-session]' ? {dataset:{openSession:'one'}} : null}});
    let promptedOnAppLink=false;
    a.unload.beforeunload({preventDefault(){promptedOnAppLink=true;}});
    assert.ok(!promptedOnAppLink, 'Opening an app link must not ask to leave the site');
    releaseAppSave(); await a.run('settingsPromise');
    a.state.fail=500; a.run("store.theme='light'; save()"); await a.run('settingsPromise');
    let promptedOnClose=false;
    a.unload.beforeunload({preventDefault(){promptedOnClose=true;}});
    assert.ok(promptedOnClose, 'Closing with unsaved changes still warns');
  }

  console.log('settings UI: bootstrap, save ordering, retry, conflict, migration, export and restart passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
