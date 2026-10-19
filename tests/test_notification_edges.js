// Exercise notification transitions and the app icon's current-state count.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const source = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));
const stamp = n => `2026-10-15T10:00:0${n}Z`;
function harness(){
  const sent = [], elements = new Map(), handlers = {}, win = {};
  class Notification {
    static permission = 'granted';
    constructor(title, options){ this.title = title; this.options = options; sent.push(this); }
    close(){}
    static async requestPermission(){ return Notification.permission; }
  }
  const node = () => ({innerHTML:'', textContent:'', value:'', dataset:{}, style:{},
    classList:{add(){}, remove(){}, toggle(){}}, querySelectorAll:()=>[], setAttribute(){}, removeAttribute(){}});
  const tasks = ['a','b','c','d'].map(id => ({id, title:id, agent:'codex', states:['running'], questions:[],
    started:stamp(0), last_activity:stamp(0), last_reply:null, reply_head:'Done'}));
  const settings = {notify:true, members:{a:true,b:true,c:true}, prio:{a:'high',b:'later',d:'high'}, initAt:stamp(0), lang:'en'};
  const ctx = {console, atob, Date, JSON, Math, Promise, Set, Map, Object, Array, String, Number, Notification,
    addEventListener(type, handler){ (win[type] ||= []).push(handler); },
    document:{visibilityState:'hidden', hasFocus:()=>false, body:node(), addEventListener(type, handler){handlers[type]=handler;},
      getElementById:id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);},
      createElement:node, querySelectorAll:()=>[]},
    navigator:{language:'en'}, window:{focus(){}}, location:{protocol:'http:'},
    sessionStorage:{getItem:()=>null,setItem(){}}, setTimeout:()=>0, clearTimeout(){}, setInterval(){},
    requestAnimationFrame(){}, cancelAnimationFrame(){}, fetch:()=>Promise.reject(new Error('Unexpected request'))};
  vm.createContext(ctx);
  for(const asset of ['vendor/markdown-it.min.js','reply-reader.js'])
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',asset),'utf8'),ctx);
  vm.runInContext(source.replace('/*DATA*/null',JSON.stringify({generated:stamp(0),tasks}))
    .replace('/*PERSIST*/null',JSON.stringify({token:'test',snapshot:{revision:0,settings}})),ctx);
  return {sent,ctx,handlers,win,run:code=>vm.runInContext(code,ctx)};
}
let failures=0;
function test(name, fn){try{fn();console.log('PASS '+name);}catch(e){failures++;console.error('FAIL '+name+': '+e.message);}}

test('only on-board Priority sessions notify, once per update',()=>{
  const h=harness();assert.equal(h.sent.length,0);
  h.run(`for(const t of DATA.tasks){ t.states=['reply']; t.last_reply='${stamp(1)}'; } checkNotifications()`);
  assert.deepEqual(h.sent.map(n=>n.title),['a']);
  h.run('checkNotifications(); render(); render()');assert.equal(h.sent.length,1);
  h.run(`store.prio.b='high'; checkNotifications()`);assert.equal(h.sent.length,1,'Moving an old reply must not alert');
  h.run(`DATA.tasks[1].last_reply='${stamp(2)}'; checkNotifications()`);assert.equal(h.sent.length,2);
});
test('final text observed before turn completion still notifies on completion',()=>{
  const h=harness();
  h.run(`DATA.tasks[0].last_reply='${stamp(1)}'; checkNotifications()`);assert.equal(h.sent.length,0);
  h.run(`DATA.tasks[0].states=['reply']; checkNotifications()`);
  assert.equal(h.sent.length,1,'The early final record must not consume the completion alert');
});
test('identical question wording in a later request notifies again',()=>{
  const h=harness();
  h.run(`Object.assign(DATA.tasks[0],{states:['running','question'],questions:['Continue?'],waiting_since:'${stamp(1)}'}); checkNotifications()`);
  assert.equal(h.sent.length,1);
  h.run(`DATA.tasks[0].waiting_since='${stamp(2)}'; checkNotifications()`);
  assert.equal(h.sent.length,2,'A distinct question is not a repeated refresh');
});
test('confirmed approval only; historical approval records stay silent',()=>{
  const h=harness();
  h.run(`DATA.tasks[0].approval_request={at:'${stamp(1)}',detail:'Historical request'};checkNotifications()`);
  assert.equal(h.sent.length,0);
  h.run(`Object.assign(DATA.tasks[0],{agent:'claude',states:['running','approval']});checkNotifications()`);
  assert.equal(h.sent.length,1);
  h.run('checkNotifications()');assert.equal(h.sent.length,1);
});
test('disabled, denied, and already-read replies do not notify',()=>{
  const h=harness();
  h.run(`store.notify=false;Object.assign(DATA.tasks[0],{states:['reply'],last_reply:'${stamp(1)}'});checkNotifications();store.notify=true;checkNotifications()`);
  assert.equal(h.sent.length,0);
  h.run(`Notification.permission='denied';DATA.tasks[0].last_reply='${stamp(2)}';checkNotifications()`);
  assert.equal(h.sent.length,0);
  h.run(`Notification.permission='granted';DATA.tasks[0].last_reply='${stamp(3)}';store.seen.a='${stamp(3)}';checkNotifications()`);
  assert.equal(h.sent.length,0);
});
test('a visible, focused board still alerts, once',()=>{
  const h=harness();h.ctx.document.visibilityState='visible';h.ctx.document.hasFocus=()=>true;
  h.run(`Object.assign(DATA.tasks[0],{states:['reply'],last_reply:'${stamp(1)}'});checkNotifications()`);
  assert.equal(h.sent.length,1);
  h.ctx.document.visibilityState='hidden';h.ctx.document.hasFocus=()=>false;
  h.run('checkNotifications()');assert.equal(h.sent.length,1);
});
test('opt-in mute applies only when both visible and focused, without delayed alerts',()=>{
  const h=harness();h.run('store.muteWhenFocused=true');
  h.ctx.document.visibilityState='visible';h.ctx.document.hasFocus=()=>true;
  h.run(`Object.assign(DATA.tasks[0],{states:['reply'],last_reply:'${stamp(1)}'});checkNotifications()`);
  assert.equal(h.sent.length,0);
  h.ctx.document.hasFocus=()=>false;
  h.run('checkNotifications()');assert.equal(h.sent.length,0,'Do not replay the muted reply');
  h.run(`DATA.tasks[0].last_reply='${stamp(2)}';checkNotifications()`);
  assert.equal(h.sent.length,1,'Visible board on another screen must not mute while focus is elsewhere');
  h.ctx.document.visibilityState='hidden';h.ctx.document.hasFocus=()=>true;
  h.run(`DATA.tasks[0].last_reply='${stamp(3)}';checkNotifications()`);
  assert.equal(h.sent.length,2,'Both visibility and focus are required');
});
test('a notification click opens the card without marking its reply read',()=>{
  const h=harness();
  h.run(`Object.assign(DATA.tasks[0],{states:['reply'],last_reply:'${stamp(1)}'});checkNotifications()`);
  h.sent[0].onclick();
  assert.equal(h.run("opened.has('a')"),true);
  assert.equal(h.run('store.seen.a'),undefined);
});
test('test alert does not change session or notification baselines',()=>{
  const h=harness();h.ctx.document.visibilityState='visible';h.ctx.document.hasFocus=()=>true;
  const before=h.run('JSON.stringify([store,Array.from(notifySeen)])');
  h.run('testNotification()');
  assert.equal(h.sent.length,1);
  assert.equal(h.sent[0].options.tag,'switch-test');
  assert.equal(h.run('JSON.stringify([store,Array.from(notifySeen)])'),before);
  assert.equal(h.run('notifyFeedback'),'notifyTestSent');
  h.sent[0].onshow();assert.equal(h.run('notifyFeedback'),'notifyTestShown');
  h.sent[0].onclick();assert.equal(h.run('opened.size'),0);
});
test('notification errors are visible, including errors raised after construction',()=>{
  const h=harness();
  h.run('testNotification()');h.sent[0].onerror();
  assert.equal(h.run('notifyFeedback'),'notifyFailed');
  assert.equal(h.ctx.document.getElementById('notificationFeedback').hidden,false);
  h.ctx.Notification=class { static permission='granted'; constructor(){throw new Error('delivery unavailable');} };
  h.run('testNotification()');assert.equal(h.run('notifyFeedback'),'notifyFailed');
});
test('a shown test with alerts on explains duplicates with client notifications',()=>{
  const h=harness(),note=()=>h.ctx.document.getElementById('notificationFeedback').textContent;
  h.run('testNotification()');assert.ok(!note().includes('duplicate'),'Not before the browser reports showing it');
  h.sent[0].onshow();
  assert.ok(note().includes('Getting duplicate alerts?')&&note().includes('other sessions and some confirmation requests still rely on the clients'));
  h.run(`store.lang='zh';renderNotifyFeedback()`);assert.ok(note().includes('收到重复提醒？'));
  h.run('store.notify=false;renderNotifyFeedback()');
  assert.ok(!note().includes('收到重复提醒')&&note().includes('“重要”栏提醒目前未开启'),'With alerts off, only the off note applies');
});
test('Install app explains why first, then offers install now or manual steps',()=>{
  const h=harness(),el=id=>h.ctx.document.getElementById(id),offer=e=>h.win.beforeinstallprompt.forEach(f=>f(e));
  h.run('render()');
  assert.equal(el('installNow').hidden,true,'No offer: no install-now button');assert.equal(el('installSteps').hidden,false,'No offer: manual steps');
  assert.ok(html.includes('data-i18n="installWhyReturn"')&&html.includes('data-i18n="installWhyBadge"')&&html.includes('data-i18n="installWhyLight"'));
  let prompted=0,offered=0;
  offer({preventDefault(){offered++;},prompt(){prompted++;return Promise.resolve();}});
  assert.equal(offered,1);assert.equal(el('installNow').hidden,false);assert.equal(el('installSteps').hidden,true);
  h.handlers.click({target:{id:'installNow',dataset:{},closest:sel=>sel==='#installHelp'?{}:null},preventDefault(){}});
  assert.equal(prompted,1,'Install now opens the browser dialog');
  assert.equal(el('installNow').hidden,true,'One offer opens the dialog once');assert.equal(el('installSteps').hidden,false);
  h.run('installApp()');assert.equal(prompted,1);
  offer({preventDefault(){},prompt(){prompted++;}});h.win.appinstalled.forEach(f=>f({}));
  assert.equal(el('installNow').hidden,true,'Installed: no install-now button');
});
test('test alert is always shown and says why it cannot send',()=>{
  assert.ok(!/id="testNotification"[^>]*hidden/.test(html)&&!source.includes("getElementById('testNotification').hidden"));
  const h=harness(),note=()=>h.ctx.document.getElementById('notificationFeedback').textContent;
  h.run('store.notify=false;render();testNotification()');
  assert.equal(h.sent.length,1,'With the switch off, the test still checks the browser and system path');
  assert.ok(note().includes('Priority alerts are currently off'));
  assert.equal(h.run('store.notify'),false,'Testing must not turn alerts on');
  h.run("Notification.permission='denied';testNotification()");
  assert.equal(h.sent.length,1);assert.equal(h.run('notifyFeedback'),'notifyTestDenied');
  h.ctx.Notification=undefined;h.run('testNotification()');
  assert.equal(h.run('notifyFeedback'),'notifyTestUnsupported');
});
test('unavailable or blocked notifications are not presented as an enable action',()=>{
  const h=harness();
  h.run("Notification.permission='denied'");
  assert.ok(h.run('notifyButton(L())').includes('Alerts blocked by browser'));
  assert.ok(h.run('notifyButton(L())').includes('disabled'));
  h.ctx.Notification=undefined;
  assert.ok(h.run('notifyButton(L())').includes('Alerts unavailable in this browser'));
  assert.ok(h.run('notifyButton(L())').includes('disabled'));
});
test('the mute option sits inside the Priority header, where partial updates reach it',()=>{
  // renderBoard 只同步栏头和栏体；放在两者之外的内容切换语言、关掉提醒后都不会更新
  const h=harness();h.run('render()');
  const html=h.ctx.document.getElementById('board').innerHTML;
  const high=html.match(/<section class="col" data-zone="high">([^]*?)<\/section>/)[1];
  const head=high.match(/<div class="col-head">([^]*?)<\/div>/)[1];
  assert.ok(head.includes('id="muteWhenFocused"')&&head.includes('Mute while board has focus'));
  assert.ok(!high.replace(/<div class="col-head">[^]*?<\/div>/,'').includes('muteWhenFocused'));
  h.run(`store.lang='zh';render()`);
  assert.ok(h.ctx.document.getElementById('board').innerHTML.includes('看板获得焦点时静音'));
});
test('only the Priority header carries the switch and the explanation',()=>{
  const h=harness();h.run('render()');
  const heads=[...h.ctx.document.getElementById('board').innerHTML.matchAll(/<div class="col-head">([^]*?)<\/div>/g)].map(m=>m[1]);
  assert.equal(heads.length,3);
  const withBell=heads.filter(x=>x.includes('data-notify-toggle'));
  assert.equal(withBell.length,1);
  assert.ok(withBell[0].includes('<h2>Priority</h2>')&&withBell[0].includes('Only sessions in Priority send desktop alerts'));
});
function withBadge(h){
  const counts=[];
  h.ctx.navigator.setAppBadge=count=>{counts.push(count);return Promise.resolve();};
  h.ctx.navigator.clearAppBadge=()=>{counts.push(0);return Promise.resolve();};
  return counts;
}
test('a Codex mark-unread reopens a reply until a later board read, without hiding newer replies',()=>{
  const h=harness(),reply=()=>h.run("status(DATA.tasks[0]).flags.some(f=>f.k==='reply')");
  const counts=withBadge(h);
  h.run(`Object.assign(DATA.tasks[0],{states:['reply'],last_reply:'${stamp(1)}',desktop_seen_reply:'${stamp(1)}'});
    store.seen.a='${stamp(1)}';store.seenAt.a='${stamp(2)}';render()`);
  assert.equal(reply(),false,'Read on the board');
  assert.equal(counts.at(-1),0);
  h.run(`Object.assign(DATA.tasks[0],{desktop_unread_reply:'${stamp(1)}',desktop_unread_at:'${stamp(3)}'});render()`);
  assert.equal(reply(),true,'Marked unread in Codex later: shown again');
  assert.equal(counts.at(-1),1,'Priority unread also restores the app badge');
  assert.equal(h.sent.length,0,'Marking the same reply unread is not a new completion');
  h.ctx.Date=class extends Date {constructor(...args){super(...(args.length ? args : [stamp(4)]));}};
  h.run(`replyViews.set('a',{display:{timestamp:'${stamp(1)}'}});markReplyRead('a')`);
  assert.equal(h.run('store.seen.a'),stamp(1),'Read cutoff stays bound to the displayed reply');
  assert.equal(h.run('store.seenAt.a'),new Date(stamp(4)).toISOString());
  assert.equal(reply(),false);
  assert.equal(counts.at(-1),0);
  h.run(`DATA.tasks[0].last_reply='${stamp(5)}';markReplyRead('a')`);
  assert.equal(h.run('store.seen.a'),stamp(1),'Reading an older body never advances the cutoff to a newer reply');
  assert.equal(reply(),true);
  h.run(`store.seen.a='${stamp(5)}';store.seenAt.a='${stamp(6)}';
    Object.assign(DATA.tasks[0],{desktop_unread_reply:'${stamp(5)}',desktop_unread_at:'${stamp(7)}'});markReplyRead('a')`);
  assert.equal(h.run('store.seenAt.a'),stamp(6),'Reading an older body cannot acknowledge a newer native mark');
  assert.equal(reply(),true);
});
test('legacy local read marks are superseded by a newer native unread action',()=>{
  const h=harness();
  h.run(`Object.assign(DATA.tasks[0],{states:['reply'],last_reply:'${stamp(1)}',desktop_unread_reply:'${stamp(1)}',desktop_unread_at:'${stamp(3)}'});
    store.seen.a='${stamp(2)}'`);
  assert.equal(h.run("status(DATA.tasks[0]).flags.some(f=>f.k==='reply')"),true);
  h.run(`store.seen.a='${stamp(4)}'`);
  assert.equal(h.run("status(DATA.tasks[0]).flags.some(f=>f.k==='reply')"),false);
});
test('app badge counts only Priority sessions, once each, independently of alerts',()=>{
  const h=harness(),counts=withBadge(h);
  h.run(`store.notify=false;
    Object.assign(DATA.tasks[0],{states:['reply','question','approval'],last_reply:'${stamp(2)}',questions:['Proceed?']});
    Object.assign(DATA.tasks[1],{states:['reply'],last_reply:'${stamp(2)}'});
    Object.assign(DATA.tasks[2],{states:['approval']});
    Object.assign(DATA.tasks[3],{states:['reply'],last_reply:'${stamp(2)}'});render()`);
  assert.equal(counts.at(-1),1,'Later, Unsorted, unselected sessions and extra flags must not count');
  assert.equal(h.sent.length,0);
  h.run(`store.prio.b='high';render()`);assert.equal(counts.at(-1),2,'Moving a session into Priority adds it');
  h.run(`store.prio.a='later';render()`);assert.equal(counts.at(-1),1,'Moving a session out of Priority removes it');
  h.run(`delete store.prio.b;render()`);assert.equal(counts.at(-1),0,'Unsorted does not count either');
});
test('moving all pending sessions to Later clears the badge without clearing their flags',()=>{
  const h=harness(),counts=withBadge(h);
  h.run(`store.notify=false;
    Object.assign(DATA.tasks[0],{states:['reply','question','approval'],last_reply:'${stamp(2)}',questions:['Proceed?']});render()`);
  assert.equal(counts.at(-1),1);
  h.run(`store.prio.a='later';render()`);assert.equal(counts.at(-1),0);
  assert.equal(h.run('status(DATA.tasks[0]).pending'),true,'Deferring does not mark a reply read or resolve a request');
  assert.equal(h.run('status(DATA.tasks[0]).flags.length'),3);
  h.run('render()');assert.equal(counts.at(-1),0,'Automatic rendering keeps Later excluded');
});
test('app badge follows reply read marks and clears when no current flags remain',()=>{
  const h=harness(),counts=withBadge(h);
  h.run(`store.notify=false;
    Object.assign(DATA.tasks[0],{states:['reply'],last_reply:'${stamp(3)}'});
    DATA.tasks[1].states=['running','stale'];DATA.tasks[2].states=['aborted'];
    store.seen.a='${stamp(2)}';render()`);
  assert.equal(counts.at(-1),1,'Reading the previous reply must not clear a newer one');
  h.run(`store.seen.a='${stamp(3)}';render()`);assert.equal(counts.at(-1),0);
  h.run(`DATA.tasks[0].states.push('question');DATA.tasks[0].questions=['Continue?'];render()`);
  assert.equal(counts.at(-1),1,'Reading a reply does not resolve a question');
  h.run(`DATA.tasks[0].states=['running'];render()`);assert.equal(counts.at(-1),0);
});
test('partial or unsupported badge APIs leave the board usable',()=>{
  const h=harness();h.ctx.navigator.setAppBadge=()=>{throw new Error('Should not call without clear support');};
  assert.doesNotThrow(()=>h.run('render()'));
  assert.ok(h.ctx.document.getElementById('board').innerHTML.includes('<h2>Priority</h2>'));
});
// 开关：第一次打开要经用户点击向浏览器申请权限；拒绝后显示被阻止，不写入设置
async function testAsync(name, fn){try{await fn();console.log('PASS '+name);}catch(e){failures++;console.error('FAIL '+name+': '+e.message);}}
function withSaves(h){
  const saved=[];h.ctx.fetch=(url,opts)=>{const body=JSON.parse(opts.body);saved.push(body.settings);
    return Promise.resolve({ok:true,json:async()=>({revision:body.revision+1,settings:body.settings})});};
  return saved;
}
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
  await testAsync('badge rejection or a synchronous browser error cannot stop updates',async()=>{
    const h=harness();
    h.ctx.navigator.clearAppBadge=()=>Promise.reject(new Error('Unavailable'));
    h.ctx.navigator.setAppBadge=()=>{throw new Error('Browser error');};
    h.run('render()');await flush();
    h.run(`DATA.tasks[0].states=['approval'];render()`);await flush();
    assert.ok(h.ctx.document.getElementById('board').innerHTML.includes('Needs approval'));
  });
  await testAsync('focused-mute option saves its checked state and disabling it restores alerts',async()=>{
    const h=harness(),saved=withSaves(h);
    assert.equal(h.run('store.muteWhenFocused'),false);
    h.handlers.change({target:{id:'muteWhenFocused',checked:true,dataset:{}}});await flush();
    assert.equal(saved.at(-1).muteWhenFocused,true);
    assert.ok(h.run('notifyOptions(L())').includes(' checked'));
    h.handlers.change({target:{id:'muteWhenFocused',checked:false,dataset:{}}});await flush();
    assert.equal(saved.at(-1).muteWhenFocused,false);
    h.ctx.document.visibilityState='visible';h.ctx.document.hasFocus=()=>true;
    h.run(`Object.assign(DATA.tasks[0],{states:['reply'],last_reply:'${stamp(4)}'});checkNotifications()`);
    assert.equal(h.sent.length,1);
  });
  await testAsync('first enable asks for permission, then turns off without asking',async()=>{
    const h=harness(),saved=withSaves(h);let asked=0;
    h.run(`store.notify=false;Notification.permission='default'`);
    h.ctx.Notification.requestPermission=async()=>{asked++;h.ctx.Notification.permission='granted';return 'granted';};
    h.run('toggleNotify()');await flush();
    assert.equal(asked,1);assert.equal(h.run('store.notify'),true);assert.equal(saved.at(-1).notify,true);
    assert.ok(h.run('notifyButton(L())').includes('aria-pressed="true"'));
    h.run('toggleNotify()');await flush();
    assert.equal(asked,1);assert.equal(h.run('store.notify'),false);assert.equal(saved.at(-1).notify,false);
  });
  await testAsync('a denied permission keeps alerts off and says the browser blocked them',async()=>{
    const h=harness(),saved=withSaves(h);
    h.run(`store.notify=false;Notification.permission='default'`);
    h.ctx.Notification.requestPermission=async()=>{h.ctx.Notification.permission='denied';return 'denied';};
    h.run('toggleNotify()');await flush();
    assert.equal(h.run('store.notify'),false);assert.equal(saved.length,0);
    assert.ok(h.run('notifyButton(L())').includes('Alerts blocked by browser'));
  });
  await testAsync('test alert asks for permission when needed without turning alerts on',async()=>{
    const h=harness();let asked=0;
    h.run(`store.notify=false;Notification.permission='default'`);
    h.ctx.Notification.requestPermission=async()=>{asked++;h.ctx.Notification.permission='granted';return 'granted';};
    h.run('testNotification()');await flush();
    assert.equal(asked,1);assert.equal(h.sent.length,1);assert.equal(h.run('store.notify'),false);
    h.ctx.Notification.permission='default';h.ctx.Notification.requestPermission=async()=>'default';
    h.run('testNotification()');await flush();
    assert.equal(h.sent.length,1);assert.equal(h.run('notifyFeedback'),'notifyTestDismissed');
  });
  process.exitCode=failures ? 1 : 0;
})();
