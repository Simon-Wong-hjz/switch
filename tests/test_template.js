const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync(process.argv[2] || require("path").join(__dirname, "../web/index.html"), "utf8");
const src = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));
const persisted = {version:1, revision:0, settings:{}};
const BOOT = JSON.stringify({token:'test-token', snapshot:persisted});
const iso = m => new Date(Date.now() - m * 60000).toISOString();
const DATA = {generated: iso(0), days: 30, tasks: [
  {id:'cx', agent:'codex', origin:'desktop', open_url:'codex://threads/cx', resume:'codex resume cx', title:'codex 任务', project:'p', states:['reply'], questions:[], questions_maybe:[], approval_request:{at:iso(3), detail:'x'},
   reply_head:'做完了', last_reply:iso(5), waiting_since:iso(5), started:iso(60), last_activity:iso(5), idle_min:5},
  {id:'cl', agent:'claude', origin:'cli', title:'claude 任务', project:'p', states:['reply'], questions:[], questions_maybe:[], approval_request:null,
   reply_head:'好了', last_reply:iso(2), waiting_since:iso(2), started:iso(30), last_activity:iso(2), idle_min:2, resume:'cd /p && claude --resume cl'},
  {id:'dk', agent:'claude', origin:'desktop', title:'desktop 任务', project:'p', states:['reply'], questions:[], questions_maybe:[], approval_request:null,
   reply_head:'好了', last_reply:iso(4), waiting_since:iso(4), started:iso(30), last_activity:iso(4), idle_min:4, resume:'claude --resume dk',
   open_url:'claude://code/continue?session=local_abc', focused_at:iso(8)},
  {id:'ap', agent:'claude', title:'等授权', project:'p', states:['running','approval'], questions:[], questions_maybe:[], approval_request:null,
   reply_head:'', last_reply:null, waiting_since:iso(1), started:iso(10), last_activity:iso(1), idle_min:1, resume:'claude --resume ap'}]};
const el = () => ({textContent:'', innerHTML:'', value:'', hidden:false, dataset:{}, style:{}, children:[],
  classList:{add(){}, remove(){}, toggle(){}, contains(){ return false; }},
  addEventListener(){}, querySelector(){ return el(); }, querySelectorAll(){ return []; }, appendChild(){}, setAttribute(){},
  removeAttribute(){}, focus(){}, showModal(){}, close(){}, scrollIntoView(){}, getBoundingClientRect(){ return {top:0,left:0,width:0,height:0}; }});
const handlers = {}, ls = {}, copied = [], fetches = [];
let timerId = 0;
const ctx = {atob, console, Date, JSON, Math, Promise, Set, Map, Object, Array, String, Number,
  document:{addEventListener:(k, f) => (handlers[k] = f), getElementById:() => el(), querySelector:() => el(), querySelectorAll:() => [], createElement:() => el(), body:el()},
  localStorage:{getItem:k => ls[k] ?? null, setItem:(k, v) => (ls[k] = v)}, sessionStorage:{getItem:() => null, setItem(){}, removeItem(){}},
  navigator:{clipboard:{writeText:s => (copied.push(s), Promise.resolve())}}, window:{prompt(){}}, location:{protocol:'http:'},
  // 短延时（重绘、复制提示）立即执行；撤销提示这类长延时不执行，方便测试撤销
  setTimeout:(f, ms) => (ms >= 1000 ? 0 : f()), clearTimeout(){}, requestAnimationFrame(){ return 1; }, cancelAnimationFrame(){}, setInterval(){}, fetch:(url, opts) => {
    fetches.push([url, opts && opts.method]);
    if (url === 'api/settings' && opts.method === 'PUT'){
      const value = JSON.parse(opts.body); persisted.settings = value.settings; persisted.revision++;
      return Promise.resolve({ok:true, json:async () => ({...persisted})});
    }
    return Promise.reject();
  },
  getComputedStyle:n => ({marginBottom:String(n.margin || 0)})};
ctx.window = Object.assign(ctx.window, ctx);
vm.createContext(ctx);
for (const asset of ['vendor/markdown-it.min.js','reply-reader.js'])
  vm.runInContext(fs.readFileSync(require('path').join(__dirname,'../web',asset),'utf8'),ctx);
vm.runInContext(src.replace('/*DATA*/null', JSON.stringify(DATA)).replace('/*PERSIST*/null', BOOT), ctx);
const run = s => vm.runInContext(s, ctx);
run(`store.initAt = '${iso(120)}'; ['cx','cl','dk','ap'].forEach(id => store.members[id] = true);`);
const flags = id => run(`status(DATA.tasks.find(t => t.id === '${id}')).flags.map(f => f.label).join(',')`);
const target = (attrs, closestHit) => ({dataset:attrs, closest:sel => closestHit && sel === closestHit.sel ? closestHit.el : null, set textContent(v){}});

assert.strictEqual(flags('cx'), '新回复', 'codex 初始有新回复');
assert.strictEqual(flags('cl'), '新回复', 'claude 初始有新回复');
// 1. 从看板点“在 Codex 中打开” -> 新回复消掉
handlers.click({target: target({}, {sel:'[data-open-session]', el:{dataset:{openSession:'cx'}}})});
assert.strictEqual(flags('cx'), '', '打开 Codex 后新回复应消掉');
assert.strictEqual(run('opened.size'), 0, '打开会话不展开卡片');
// 2. 复制 Claude 恢复命令 -> 新回复消掉，剪贴板拿到命令
handlers.click({target: target({copy:'cl'})});
assert.strictEqual(flags('cl'), '', '复制恢复命令后新回复应消掉');
assert.deepStrictEqual(copied, ['cd /p && claude --resume cl']);
assert.strictEqual(run('opened.size'), 0, '复制命令不展开卡片');
// 3. 之后又来新回复 -> 标记重新出现
run(`DATA.tasks.find(t => t.id === 'cx').last_reply = new Date(Date.now() + 60000).toISOString()`);
assert.strictEqual(flags('cx'), '新回复', '看过之后再来的新回复要重新标出');
// 4. 已读标记进入服务端保存队列
assert.ok(run('JSON.parse(pendingSettings).seen.cl'), '标记应进入保存队列');
// 5. Claude 实时确认框 -> 待授权；Codex 只有历史授权记录 -> 不显示待授权
assert.strictEqual(flags('ap'), '待授权');
assert.ok(!flags('cx').includes('待授权'));
// 6. 标签区分客户端来源
const cardHtml = (id, open = true) => run(`opened.clear(); ${open ? `opened.add('${id}');` : ''} card(DATA.tasks.find(t => t.id === '${id}'))`);
assert.ok(cardHtml('dk').includes('>Claude Desktop<') && cardHtml('cl').includes('>Claude CLI<') && cardHtml('cx').includes('>Codex Desktop<'));
// 7. Desktop 会话的按钮是唤起 Desktop；CLI 会话仍是复制命令
assert.ok(cardHtml('dk').includes('href="claude://code/continue?session=local_abc"') && !cardHtml('dk').includes('data-copy'));
assert.ok(cardHtml('cl').includes('data-copy="cl"') && !cardHtml('cl').includes('claude://'));
assert.ok(cardHtml('cx').includes('href="codex://threads/cx"') && cardHtml('cx').includes('>打开<'));
run(`DATA.tasks.push({...DATA.tasks[0], id:'cc', origin:'cli', open_url:null, resume:'codex resume cc'}); opened.add('cc')`);
assert.ok(cardHtml('cc').includes('>Codex CLI<') && cardHtml('cc').includes('data-copy="cc"') && !cardHtml('cc').includes('codex://'));
// 打开入口在收起和展开时各有一份，Desktop 与 CLI 都不需要先展开。
for (const [id, attr] of [['cx','data-open-session'],['dk','data-open-session'],['cl','data-copy'],['cc','data-copy']]){
  for (const open of [false,true]){
    const markup = cardHtml(id, open);
    assert.strictEqual(markup.split(`${attr}="${id}"`).length - 1, 1);
    assert.ok(markup.includes('class="card-open dbtns"'));
    assert.strictEqual(markup.includes('class="detail'), open);
    if (open) assert.ok(markup.indexOf(`${attr}="${id}"`) < markup.indexOf('class="detail'));
  }
}
handlers.click({target: target({copy:'cc'})});
assert.strictEqual(copied.at(-1), 'codex resume cc');
run(`DATA.tasks.find(t => t.id === 'cc').origin = 'unknown'`);
assert.ok(cardHtml('cc').includes('>Codex<') && !cardHtml('cc').includes('Codex Desktop') && !cardHtml('cc').includes('codex://'));
run(`DATA.tasks = DATA.tasks.filter(t => t.id !== 'cc'); opened.delete('cc')`);
// 8. Desktop 会话最后一次显示早于最新回复 -> 仍标新回复；晚于最新回复 -> 不用点也算看过
assert.strictEqual(flags('dk'), '新回复', '最后显示早于最新回复');
run(`DATA.tasks.find(t => t.id === 'dk').focused_at = new Date().toISOString()`);
assert.strictEqual(flags('dk'), '', '在 Desktop 里显示过之后新回复应消掉');
// 9. Codex 原生已读只消掉它对应的那条回复。
run(`delete store.seen.cx; DATA.tasks.find(t => t.id === 'cx').desktop_seen_reply = DATA.tasks.find(t => t.id === 'cx').last_reply`);
assert.strictEqual(flags('cx'), '', '原生已读清除对应回复');
// 10. 随后到来的回复不能被旧的原生已读记录消掉。
run(`DATA.tasks.find(t => t.id === 'cx').last_reply = new Date(Date.now() + 120000).toISOString()`);
assert.strictEqual(flags('cx'), '新回复', '原生已读不能清除下一条回复');
// 11. 已读不能处理提问或授权。
run(`const nativeTask = DATA.tasks.find(t => t.id === 'cx'); nativeTask.desktop_seen_reply = nativeTask.last_reply; nativeTask.states = ['reply', 'question']; nativeTask.questions = ['Choose?']`);
assert.strictEqual(flags('cx'), '待回答');
assert.strictEqual(flags('ap'), '待授权');
// 12. 旧问题仍在数据中，但不再混入当前回复详情；引用明确标注来源和时间。
run(`Object.assign(DATA.tasks.find(t => t.id === 'cx'), {
  states:['question_maybe','reply'], questions:[], questions_maybe:['OLD QUESTION MUST NOT APPEAR'],
  reply_head:'Current <reply>', last_reply:new Date().toISOString(), desktop_seen_reply:null
})`);
const currentCard = cardHtml('cx');
assert.ok(!currentCard.includes('OLD QUESTION MUST NOT APPEAR') && !currentCard.includes('可能已经答过了'));
assert.ok(currentCard.includes('最新回复摘录 · 今天 ') && currentCard.includes('<blockquote>Current &lt;reply&gt;</blockquote>'));
assert.strictEqual(run(`DATA.tasks.find(t => t.id === 'cx').questions_maybe.length`), 1);
assert.ok(currentCard.includes('授权请求原文') && currentCard.includes(`<p class="board-note">${run('T.zh.approvalUnknown')}</p>`));
// 13. 当前提问照常展示；由看板生成的授权状态说明不冒充原文。
run(`Object.assign(DATA.tasks.find(t => t.id === 'cx'), {states:['question'], questions:['Current question?']})`);
assert.ok(cardHtml('cx').includes('提问原文 · ') && cardHtml('cx').includes('<blockquote>Current question?</blockquote>'));
assert.ok(cardHtml('ap').includes(`<p class="board-note">${run('T.zh.waitingConfirm')}</p>`) && !cardHtml('ap').includes('<blockquote>'));
// 14. 旧内容显示日期，不能只剩下一个容易误认成今天的时刻。
assert.ok(run(`sourceTime(new Date(Date.now() - 86400000).toISOString())`).startsWith('昨天 '));
assert.match(run(`sourceTime('2020-01-02T12:30:00Z')`), /^2020\/1\/2 /);
// 15. 可以同时展开多张卡片；点击一张不会收起另一张
const cardEl = id => ({dataset:{}, closest:sel => sel === '.card' ? {dataset:{id}} : null});
run(`opened.clear()`);
handlers.click({target: cardEl('cl')}); handlers.click({target: cardEl('dk')});
assert.ok(run(`card(DATA.tasks.find(t => t.id === 'cl'))`).includes('class="detail') && run(`card(DATA.tasks.find(t => t.id === 'dk'))`).includes('class="detail'));
handlers.click({target: cardEl('cl')});
assert.ok(!run(`card(DATA.tasks.find(t => t.id === 'cl'))`).includes('class="detail') && run(`card(DATA.tasks.find(t => t.id === 'dk'))`).includes('class="detail'));
// 16. 中英切换：状态、按钮和时间都跟着切换，并记住选择
handlers.click({target: {id:'langToggle', dataset:{}, closest:() => null}});
assert.strictEqual(run('lang()'), 'en');
assert.strictEqual(flags('ap'), 'Needs approval');
assert.ok(cardHtml('dk').includes('>Open<') && run(`sourceTime(new Date(Date.now() - 86400000).toISOString())`).startsWith('Yesterday '));
assert.ok(cardHtml('cx').includes('>Open<'));
assert.strictEqual(run('JSON.parse(pendingSettings).lang'), 'en');
handlers.click({target: {id:'langToggle', dataset:{}, closest:() => null}});
assert.strictEqual(flags('ap'), '待授权');
// 17. 明暗切换会记住选择
const before = run('theme()');
handlers.click({target: {id:'themeToggle', dataset:{}, closest:() => null}});
assert.notStrictEqual(run('theme()'), before);
assert.strictEqual(run('JSON.parse(pendingSettings).theme'), run('theme()'));
// 18. Desktop 无文件夹会话的项目名按语言显示
run(`DATA.tasks.find(t => t.id === 'dk').no_folder = true`);
assert.ok(cardHtml('dk').includes('>无文件夹<'));
// 19. 两种语言的文案键完全一致，不会出现某个语言缺字
assert.deepStrictEqual(run('Object.keys(T.zh).sort()'), run('Object.keys(T.en).sort()'));
// 20. 浏览器拖拽事件走到分组、移出；取消拖拽不能移出卡片。
const beginDrag = id => {
  const c = el(); c.dataset.id = id;
  handlers.dragstart({target:{closest:s => s === '.card' ? c : null},
    dataTransfer:{setData(type, value){ assert.strictEqual(type, 'text/plain'); assert.strictEqual(value, id); }}});
};
const dropOn = zone => {
  const z = el(); z.dataset.zone = zone;
  let accepted = false;
  handlers.drop({target:{closest:s => s === '[data-zone]' ? z : null},
    preventDefault(){ accepted = true; }, clientY:100});
  assert.ok(accepted, '有效落点应接受 drop');
};
beginDrag('cl'); dropOn('high');
assert.strictEqual(run('store.prio.cl'), 'high');
assert.ok(run('store.members.cl'));
beginDrag('cl'); handlers.dragend();
assert.ok(run('store.members.cl'), '取消拖拽保留卡片');
beginDrag('cl'); dropOn('remove');
assert.ok(!run('store.members.cl') && run('store.removed.cl'), '移出后记住排除，不能自动加回');
assert.strictEqual(run('dragging'), null);
// 21. Codex 与 Claude 标签各有自己的颜色类，不和状态色共用
assert.ok(run(`agentTag({agent:'codex'})`).includes('class="agent codex"') && run(`agentTag({agent:'claude', origin:'cli'})`).includes('class="agent claude"'));
// 22. 每一栏带自己的分区标识，供分栏配色使用
assert.ok(['high', 'later', 'other'].every(z => run('COLS').includes(z)));
// 23. 移出后可以撤销：成员、分组和顺序原样放回
run(`store.members.dk = true; delete store.removed.dk; store.prio.dk = 'later'; store.order = ['ap', 'dk', 'cx']`);
beginDrag('dk'); dropOn('remove');
assert.ok(!run('store.members.dk') && run('store.removed.dk'));
handlers.click({target: {id:'undoRemove', dataset:{}, closest:() => null}});
assert.ok(run('store.members.dk') && !run('store.removed.dk'), '撤销后回到看板');
assert.strictEqual(run('store.prio.dk'), 'later', '撤销后回到原来的栏');
assert.strictEqual(run('JSON.stringify(store.order)'), JSON.stringify(['ap', 'dk', 'cx']), '撤销后回到原来的位置');   // 跨 vm 的数组按值比较
handlers.click({target: {id:'undoRemove', dataset:{}, closest:() => null}});   // 撤销只能用一次
assert.ok(run('store.members.dk'));
// 24. 批量移出同样可以撤销
run(`selecting = true; selected.clear(); selected.add('cx'); selected.add('dk')`);
handlers.click({target: {id:'removeSelected', dataset:{}, closest:() => null}});
assert.ok(!run('store.members.cx') && !run('store.members.dk'));
handlers.click({target: {id:'undoRemove', dataset:{}, closest:() => null}});
assert.ok(run('store.members.cx') && run('store.members.dk'));
// 25. 拖动结束后不留下拖动状态
beginDrag('cl'); handlers.dragend();
assert.strictEqual(run('dragging'), null);
assert.strictEqual(run('slot'), null);
// 26. 拖到移出区松开就移出（移出后可撤销）；掠过移出区再取消拖拽，卡片保留。
const overRemove = () => { const z = el(); z.dataset.zone = 'remove'; handlers.dragover({target:{closest:s => s === '[data-zone]' ? z : null}, preventDefault(){}, clientY:880}); };
beginDrag('dk'); overRemove(); handlers.dragend();
assert.ok(run('store.members.dk'), '取消拖拽保留卡片');
beginDrag('dk'); overRemove(); dropOn('remove');
assert.ok(!run('store.members.dk') && run('store.removed.dk'), '松开即移出');
handlers.click({target: {id:'undoRemove', dataset:{}, closest:() => null}});
assert.ok(run('store.members.dk'), '撤销后回到看板');
// 27. 占位动画中途和结束时，同一指针对应同一插入点；收起中的旧空位也要计入。
ctx.geometryBody = {children:[]};
const geometryNode = (kind, top, height, margin = 0) => ({margin, classList:{contains:s => s === kind}, getBoundingClientRect:() => ({top,height})});
const next = geometryNode('card', 120, 80);
ctx.geometryBody.children = [geometryNode('slot', 0, 20), next];
assert.strictEqual(run('slotTarget(geometryBody, 130)'), next);
const afterAnimation = geometryNode('card', 208, 80);
ctx.geometryBody.children = [geometryNode('slot', 0, 100, 8), afterAnimation];
assert.strictEqual(run('slotTarget(geometryBody, 130)'), afterAnimation);
const reversing = geometryNode('card', 158, 80);
ctx.geometryBody.children = [geometryNode('slot', 0, 20, 4), geometryNode('slot', 24, 30, 4), reversing];
assert.strictEqual(run('slotTarget(geometryBody, 130)'), reversing);
// 28. 添加会话：搜索只在当前时间范围内进行
run(`imp.days = 1; imp.q = 'desktop'; Object.assign(DATA.tasks.find(t => t.id === 'dk'), {last_activity:new Date(Date.now() - 3 * 86400e3).toISOString()}); delete store.members.dk`);
assert.ok(!run('candidates().some(t => t.id === "dk")'), '超出当前范围的会话不应被搜到');
run('imp.days = 7');
assert.ok(run('candidates().some(t => t.id === "dk")'), '放宽范围后能搜到');
run(`store.members.dk = true; imp.q = ''`);
// 29. 搜索命中的文字高亮，且照常转义
assert.strictEqual(run(`hl('Fix <Login> login bug', 'login')`), 'Fix &lt;<mark>Login</mark>&gt; <mark>login</mark> bug');
assert.strictEqual(run(`hl('a<b', '')`), 'a&lt;b');
// 30. “再显示 10 条”依赖 hidden 属性，按钮样式不能把它盖掉
assert.ok(html.includes('[hidden]{display:none!important}'));
// 31. 立即刷新：请后端马上重新扫描
handlers.click({target: {id:'refreshNow', dataset:{}, closest:() => null}});
assert.deepStrictEqual(fetches.at(-1), ['refresh', 'POST']);
// 32–33. 手动刷新的反馈：失败显示“刷新失败”，成功显示“已刷新”，2.5 秒后回到“更新于”（异步，放在最后）
(async () => {
  const settle = () => new Promise(r => setImmediate(r));
  run(`refreshing = false`);
  handlers.click({target: {id:'refreshNow', dataset:{}, closest:() => null}});   // 沙箱里 fetch 总是失败
  await settle(); await settle();
  assert.strictEqual(run('stampState().text'), '刷新失败');
  ctx.fetch = () => Promise.resolve({ok:true, json:() => Promise.resolve(Object.assign({}, run('DATA'), {generated:new Date().toISOString()}))});
  run(`refreshing = false`);
  handlers.click({target: {id:'refreshNow', dataset:{}, closest:() => null}});
  await settle(); await settle(); await settle();
  assert.ok(run('stampState().text').startsWith('已刷新 '), '成功后标“已刷新”');
  run(`refreshNoteAt = Date.now() - 3000`);
  assert.ok(run('stampState().text').startsWith('更新于 '), '2.5 秒后回到“更新于”');
  // 34–36. 刷新请求期间开始操作，完成及提示消退都不能重建看板或导入列表。
  const delayed = [], elements = new Map();
  ctx.document.getElementById = id => {
    if (!elements.has(id)) elements.set(id, el());
    return elements.get(id);
  };
  ctx.setTimeout = (f, ms) => ms >= 1000 ? delayed.push(f) : f();
  run(`let fullRenders = 0; render = () => { fullRenders++; }; dragging = 'cx'; imp.open = false;`);
  handlers.click({target: {id:'refreshNow', dataset:{}, closest:() => null}});
  await settle(); await settle();
  assert.strictEqual(run('fullRenders'), 0, '拖动期间刷新完成不重建卡片');
  assert.ok(elements.get('time').textContent.startsWith('已刷新 '), '拖动期间仍更新顶栏反馈');
  run(`dragging = null; imp.open = true;`);
  handlers.click({target: {id:'refreshNow', dataset:{}, closest:() => null}});
  await settle(); await settle();
  assert.strictEqual(run('fullRenders'), 0, '导入期间刷新完成不重建列表');
  run(`refreshNoteAt = Date.now() - 3000;`);
  delayed.forEach(f => f());
  assert.strictEqual(run('fullRenders'), 0, '提示消退只更新顶栏');
  assert.ok(elements.get('time').textContent.startsWith('更新于 '));
  // 37. 在窗口外按下并松开才关闭“添加会话”；在窗口内按下、窗口外松开（例如拖选文字）不关闭
  run('openImport(false)');
  handlers.mousedown({target:{id:'filters'}}); handlers.click({target:{id:'importMask', dataset:{}, closest:() => null}});
  assert.strictEqual(run('imp.open'), true, '从窗口内拖出不应关闭');
  handlers.mousedown({target:{id:'importMask'}}); handlers.click({target:{id:'importMask', dataset:{}, closest:() => null}});
  assert.strictEqual(run('imp.open'), false, '点击窗口外应关闭');
  // Installed version never stores persistent settings in browser storage.
  assert.deepStrictEqual(ls, {});
  console.log('all 38 interaction checks passed');
})().catch(e => { console.error(e); process.exit(1); });
