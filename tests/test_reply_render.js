// 全文阅读的渲染安全用例。回复内容来自 Agent，一律当作不可信输入：
// 只渲染 Markdown；原始 HTML 当文本显示；链接只放行 http(s) 并在新窗口打开；不加载任何图片或外部资源。
// 实际接口：SwitchReply.renderMarkdown(text) -> {html, plain}。运行：node tests/test_reply_render.js
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const web = path.join(__dirname, '..', 'web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');

const ctx = {console, atob};
vm.createContext(ctx);
// 使用页面实际引用的本地资源；URL 与文件的对应和服务端白名单一致。
const assets = {'assets/markdown-it.min.js':'vendor/markdown-it.min.js', 'assets/reply-reader.js':'reply-reader.js'};
for (const m of html.matchAll(/<script[^>]*\ssrc="([^"]+)"[^>]*><\/script>/g)){
  assert.ok(assets[m[1]], `页面不能引用未列出的脚本：${m[1]}`);
  vm.runInContext(fs.readFileSync(path.join(web, assets[m[1]]), 'utf8'), ctx, {filename:m[1]});
}
assert.strictEqual(typeof ctx.SwitchReply?.renderMarkdown, 'function', '全文渲染模块必须加载');
const render = text => ctx.SwitchReply.renderMarkdown(text).html;
const tags = out => [...out.matchAll(/<([a-zA-Z][\w-]*)\b([^>]*)>/g)].map(m => ({name:m[1].toLowerCase(), attrs:m[2]}));
const FORBIDDEN = ['script', 'img', 'iframe', 'object', 'embed', 'style', 'link', 'meta', 'form', 'input', 'button', 'svg', 'math', 'base', 'video', 'audio', 'source'];

function assertSafe(out, label){
  for (const t of tags(out)){
    assert.ok(!FORBIDDEN.includes(t.name), `${label}：出现了 <${t.name}>`);
    assert.ok(!/\son[a-z]+\s*=/i.test(t.attrs), `${label}：<${t.name}> 带了事件属性`);
    assert.ok(!/\s(style|srcset|src|formaction|xlink:href)\s*=/i.test(t.attrs), `${label}：<${t.name}> 带了 ${t.attrs.trim()}`);
    const href = t.attrs.match(/\shref\s*=\s*"([^"]*)"/i);
    if (href){
      assert.ok(/^https?:\/\//i.test(href[1]), `${label}：链接协议不是 http(s)：${href[1]}`);
      assert.ok(/\starget="_blank"/.test(t.attrs), `${label}：外部链接应在新窗口打开`);
      assert.ok(/\srel="[^"]*noopener[^"]*"/.test(t.attrs) && /noreferrer/.test(t.attrs), `${label}：外部链接缺少 rel="noopener noreferrer"`);
    }
  }
}

const cases = {
  'script 标签': '前文 <script>alert(1)</script> 后文',
  '图片事件': '<img src=x onerror=alert(1)>',
  '内联 HTML 链接': '<a href="javascript:alert(1)">点我</a>',
  'javascript 链接': '[点我](javascript:alert(1))',
  '大小写混合': '[点我](JaVaScRiPt:alert(1))',
  '实体编码': '[点我](jav&#x61;script:alert(1))',
  '空白绕过': '[点我]( javascript:alert(1))',
  'data 链接': '[点我](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)',
  'vbscript 链接': '[点我](vbscript:msgbox(1))',
  'file 链接': '[点我](file:///etc/passwd)',
  '自动链接': '<javascript:alert(1)>',
  '引用式链接': '[点我][x]\n\n[x]: javascript:alert(1)',
  'Markdown 图片': '![追踪像素](https://example.com/pixel.png)',
  'svg': '<svg onload=alert(1)><circle r=1></svg>',
  'iframe': '<iframe src="https://example.com"></iframe>',
  'style 注入': '<style>body{display:none}</style><div style="position:fixed">x</div>',
};
for (const [label, text] of Object.entries(cases)) assertSafe(render(text), label);

// 原始 HTML 当文本显示，而不是悄悄吞掉：用户要能看到 Agent 原本写了什么
assert.ok(render('<script>alert(1)</script>').includes('&lt;script&gt;'), '原始 HTML 应原样显示为文本');
// 正常的 Markdown 照常排版
const md = render('## 结论\n\n**验证完成**，见 [PR](https://github.com/x/y/pull/1)。\n\n- 第一项\n  - 嵌套项\n\n```html\n<b>不是粗体</b>\n```\n\n行内 `a < b` 代码');
assertSafe(md, '正常 Markdown');
assert.ok(/<h2[^>]*>结论<\/h2>/.test(md), '标题');
assert.ok(/<strong>验证完成<\/strong>/.test(md), '粗体');
assert.ok(/<a [^>]*href="https:\/\/github\.com\/x\/y\/pull\/1"/.test(md), 'http(s) 链接保留可点');
assert.ok((md.match(/<ul/g) || []).length >= 2, '嵌套列表');
assert.ok(/<code[^>]*>[^]*&lt;b&gt;不是粗体&lt;\/b&gt;/.test(md), '代码块里的 HTML 保持为文本');
assert.ok(md.includes('a &lt; b'), '行内代码转义');
const footer = '<oai-mem-citation>\n<citation_entries>source</citation_entries>\n</oai-mem-citation>';
assert.strictEqual(render('**Done**\n\n' + footer), render('**Done**'), '显示隐藏记忆尾注');
assert.ok(!render('x'.repeat(200001) + '\n' + footer).includes('oai-mem-citation'), '长回复原文视图也隐藏尾注');
assert.ok(render('```xml\n' + footer + '\n```').includes('oai-mem-citation'), '代码中的示例保留');
assert.ok(render('`' + footer.replace(/\n/g, '') + '`').includes('oai-mem-citation'), '行内代码中的示例保留');
assert.ok(render('<oai-mem-citation>unfinished').includes('unfinished'), '不完整标签不吞掉正文');
// 超长回复可以渲染，不卡住页面
const t0 = Date.now(); render(('## 段落\n\n' + '很长的一段说明，包含 `code` 和 [链接](https://example.com)。\n'.repeat(40) + '\n').repeat(60));
assert.ok(Date.now() - t0 < 1500, `超长回复渲染过慢：${Date.now() - t0}ms`);
console.log(`reply render: ${Object.keys(cases).length} 种注入写法均被拦截，正常排版与超长回复通过`);
