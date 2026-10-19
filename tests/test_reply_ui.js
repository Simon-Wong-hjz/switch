const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
const source = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));
const tick = () => new Promise(resolve => setImmediate(resolve));
const stamp = n => `2026-10-15T09:00:0${n}Z`;
const task = id => ({id,reply_id:id+'-1',last_reply:stamp(1),started:stamp(0),last_activity:stamp(1),
  agent:'codex',title:id,project:'demo',states:['reply'],questions:[],reply_head:'preview'});
function harness(){
  const nodes = new Map(), handlers = {}, requests = [], copies = [], pending = [], intervals = [];
  const node = () => ({innerHTML:'',textContent:'',dataset:{},style:{},value:'',hidden:false,
    classList:{add(){},remove(){},toggle(){}},querySelectorAll(){return [];},setAttribute(){},removeAttribute(){},focus(){}});
  const ctx = {console,atob,Date,JSON,Math,Promise,Set,Map,Object,Array,String,Number,encodeURIComponent,
    document:{getElementById:id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);},
      createElement:node,querySelectorAll:()=>[],body:node(),addEventListener:(key,fn)=>handlers[key]=fn},
    navigator:{language:'en',clipboard:{writeText:async text=>copies.push(text)}},
    window:{},sessionStorage:{getItem:()=>null,setItem(){}},location:{protocol:'http:'},
    setTimeout:()=>0,clearTimeout(){},setInterval:fn=>intervals.push(fn),requestAnimationFrame(){},cancelAnimationFrame(){},
    fetch:(url,options={})=>{
      requests.push({url,options});
      if(url==='api/settings')return Promise.resolve({ok:true,json:async()=>({revision:1,settings:JSON.parse(options.body).settings})});
      return new Promise((resolve,reject)=>pending.push({resolve,reject,url,options}));
    }};
  vm.createContext(ctx);
  for(const asset of ['vendor/markdown-it.min.js','reply-reader.js'])
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',asset),'utf8'),ctx);
  const settings={members:{a:true,b:true},initAt:stamp(0),lang:'en'};
  vm.runInContext(source.replace('/*DATA*/null',JSON.stringify({generated:stamp(2),tasks:[task('a'),task('b')]}))
    .replace('/*PERSIST*/null',JSON.stringify({token:'test-token',snapshot:{revision:0,settings}})),ctx);
  const run=code=>vm.runInContext(code,ctx);
  const respond=(request,id,reply,n,text)=>request.resolve({ok:true,json:async()=>({session_id:id,reply_id:reply,timestamp:stamp(n),text})});
  return {ctx,run,nodes,handlers,requests,copies,pending,respond,intervals};
}
(async()=>{
  const h=harness();
  assert.equal(h.requests.length,0,'Collapsed cards must not request full replies');
  h.run("opened.add('a'); opened.add('b'); render()");
  assert.equal(h.pending.length,2,'Multiple expanded cards fetch independently');
  for(const request of h.pending)assert.equal(request.options.headers['X-Taskboard-Token'],'test-token');
  assert.ok(h.pending[0].url.includes('session=a&reply=a-1'));
  h.run('render(); render()');assert.equal(h.pending.length,2,'Poll redraws must not refetch');

  const original='  # Original\n\n**Keep** `2 ** 3` and [docs](https://example.org/a).\n\n<oai-mem-citation>\n<citation_entries>source</citation_entries>\n</oai-mem-citation>\n';
  h.respond(h.pending[0],'a','a-1',1,original);
  h.respond(h.pending[1],'b','b-1',1,'Second card');
  await tick();
  assert.equal(h.run("replyViews.get('a').display.text"),original);
  assert.ok(h.run('replySection(DATA.tasks[0])').includes('<h1>Original</h1>'));
  assert.ok(!h.run('replySection(DATA.tasks[0])').includes('oai-mem-citation'),'Memory footer is hidden from display');
  assert.equal(h.run("replyViews.get('b').display.text"),'Second card');
  await h.run("copyReply('a')");assert.equal(h.copies[0],original,'Copy preserves full raw whitespace');

  // The regular five-second poll loads a new reply without another click.
  h.intervals[0]();
  const polled=JSON.parse(h.run('JSON.stringify(DATA)'));
  Object.assign(polled.tasks[0],{reply_id:'a-2',last_reply:stamp(2)});
  h.pending[2].resolve({ok:true,json:async()=>polled});await tick();
  assert.equal(h.pending.length,4,'Polling should fetch the changed reply only');
  assert.ok(h.pending[3].url.includes('session=a&reply=a-2'));
  assert.ok(h.nodes.get('board').innerHTML.includes('Loading reply'));
  assert.ok(!h.nodes.get('board').innerHTML.includes('data-reply-latest'));
  assert.equal(h.run("replyViews.get('a').display.text"),original,'Keep the old reply until the replacement has loaded');
  h.run("markReplyRead('a')");await tick();
  assert.equal(h.run('store.seen.a'),stamp(1));
  assert.equal(h.run('status(DATA.tasks[0]).canSee'),true,'Reading an older reply cannot mark the newer one read');

  h.run("Object.assign(DATA.tasks[0],{reply_id:'a-3',last_reply:'2026-10-15T09:00:03Z'}); render()");
  h.respond(h.pending[4],'a','a-3',3,'Newest');await tick();
  h.respond(h.pending[3],'a','a-2',2,'Out of order');await tick();
  assert.equal(h.run("replyViews.get('a').display.text"),'Newest','Late requests must not overwrite the latest reply');
  assert.ok(h.nodes.get('board').innerHTML.includes('<p>Newest</p>'),'The expanded card updates automatically');
  assert.equal(h.run('store.seen.a'),stamp(1),'Automatically loading a reply must not mark it read');
  h.run('render(); render()');
  assert.equal(h.pending.length,5,'An unchanged reply is not fetched again');

  // A failed reload retains the old visible reply, and retry requests current metadata.
  h.run("Object.assign(DATA.tasks[0],{reply_id:'a-4',last_reply:'2026-10-15T09:00:04Z'}); render()");
  h.pending[5].resolve({ok:false,status:503});await tick();
  assert.equal(h.run("replyViews.get('a').display.text"),'Newest');
  assert.ok(h.run('replySection(DATA.tasks[0])').includes('Could not load'));
  h.run('render()');assert.equal(h.pending.length,6,'A failed request must not trigger a render/retry loop');
  h.handlers.click({target:{dataset:{replyRetry:'a'}}});
  h.respond(h.pending[6],'wrong-session','a-4',4,'Wrong');await tick();
  assert.equal(h.run("replyViews.get('a').display.text"),'Newest','Mismatched reply must not be displayed');
  h.run('loadReply(DATA.tasks[0],true)');
  h.run("opened.delete('a'); render()");
  h.respond(h.pending[7],'a','a-4',4,'Late after close');await tick();
  assert.equal(h.run("replyViews.has('a')"),false,'Closing frees the snapshot and stale responses cannot revive it');
  assert.equal(h.run("replyViews.get('b').display.text"),'Second card');

  const early=harness();
  early.run("opened.add('a'); render(); Object.assign(DATA.tasks[0],{reply_id:'a-2',last_reply:'2026-10-15T09:00:02Z'}); render()");
  assert.equal(early.pending.length,2,'A new reply supersedes even an unfinished initial load');
  early.respond(early.pending[1],'a','a-2',2,'Current');await tick();
  early.respond(early.pending[0],'a','a-1',1,'Old');await tick();
  assert.equal(early.run("replyViews.get('a').display.text"),'Current');

  // A completed source response is rendered without HTML execution or active non-web links.
  const render = text=>{h.ctx.markdownText=text;return h.run('SwitchReply.renderMarkdown(markdownText)');};
  const malicious='<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[x](javascript:alert%281%29) [d](data:text/html,hello)';
  const safe=render(malicious).html;
  assert.ok(safe.includes('&lt;script&gt;') && safe.includes('&lt;img'));
  assert.ok(!/<(?:script|img)\b/.test(safe));
  assert.ok(!/href="(?:javascript|data):/i.test(safe));
  for(const url of ['javascript&#58;alert(1)','JaVaScRiPt:alert(1)','file:///etc/passwd','//example.org','codex://threads/a','data:image/png;base64,AA'])
    assert.ok(!render(`[x](${url})`).html.includes('<a '),url);
  const rich=render('# Heading\n\n- item\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```js\n<script>\n```\n\n[link](https://example.org)\n\n![image](https://example.org/private.png)').html;
  for(const fragment of ['<h1>','<ul>','<table>','<pre><code','target="_blank"','rel="noopener noreferrer"'])assert.ok(rich.includes(fragment),fragment);
  assert.ok(!rich.includes('<img'),'Rendering must not fetch remote images');
  const long='**long**\n'.repeat(25001)+'THE END';
  const rendered=render(long);assert.equal(rendered.plain,true);
  assert.ok(rendered.html.endsWith('THE END</pre>'),'Very long replies remain complete in source view');
  // Exercise the DOM patch boundary that once left the first fetched body empty.
  // Instrument only this test context; no test export is shipped in the application.
  const readerCode=fs.readFileSync(path.join(__dirname,'../web/reply-reader.js'),'utf8');
  vm.runInContext(readerCode.replace('root.SwitchReply = {renderMarkdown, renderBoard};',
    'root.SwitchReply = {renderMarkdown, renderBoard, patch};'),h.ctx);
  const body=(key,child)=>{
    const attrs=new Map([['class','reply-body'],['data-reply-key',key]]);
    return {get attributes(){return [...attrs].map(([name,value])=>({name,value}));},
      get dataset(){return {replyKey:attrs.get('data-reply-key')};},
      classList:{contains:name=>name==='reply-body'},getAttribute:name=>attrs.get(name),
      hasAttribute:name=>attrs.has(name),setAttribute:(name,value)=>attrs.set(name,value),removeAttribute:name=>attrs.delete(name),
      childNodes:child ? [child] : [],scrollTop:0,replaceChildren(...children){this.childNodes=children;}};
  };
  const current=body('',null), firstNode={textContent:'first full reply'};
  h.ctx.oldBody=current;h.ctx.nextBody=body('first',firstNode);h.run('SwitchReply.patch(oldBody,nextBody)');
  assert.strictEqual(current.childNodes[0],firstNode,'First fetch must insert rendered text');
  current.scrollTop=173;
  h.ctx.nextBody=body('first',{textContent:'same reply recreated by polling'});h.run('SwitchReply.patch(oldBody,nextBody)');
  assert.strictEqual(current.childNodes[0],firstNode,'Polling must retain the actual selected text node');
  assert.equal(current.scrollTop,173,'Polling must retain reader scroll');
  const secondNode={textContent:'second reply'};
  h.ctx.nextBody=body('second',secondNode);h.run('SwitchReply.patch(oldBody,nextBody)');
  assert.strictEqual(current.childNodes[0],secondNode,'An automatically loaded new reply replaces the body and metadata');
  assert.equal(current.scrollTop,0,'A new reply starts at the top');
  assert.deepEqual(h.run('Object.keys(T.zh).sort()'),h.run('Object.keys(T.en).sort()'));
  console.log('reply UI: automatic latest reply, multi-card, request races, read marks, retry, copy, Markdown safety and long-text checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
