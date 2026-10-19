/* Local, read-only Markdown rendering and keyed board updates. */
(function (root) {
  'use strict';
  const md = root.markdownit({html:false, linkify:false, typographer:false});
  const escape = md.utils.escapeHtml;
  const valid = md.validateLink.bind(md);
  md.validateLink = url => /^https?:\/\//i.test(url) && valid(url);
  const linkOpen = md.renderer.rules.link_open || ((tokens, i, opts, env, self) => self.renderToken(tokens, i, opts));
  md.renderer.rules.link_open = (tokens, i, opts, env, self) => {
    tokens[i].attrSet('target', '_blank');
    tokens[i].attrSet('rel', 'noopener noreferrer');
    return linkOpen(tokens, i, opts, env, self);
  };
  // Reading a reply must not itself load external images or tracking resources.
  md.renderer.rules.image = (tokens, i, opts, env, self) => {
    const token = tokens[i], src = token.attrGet('src') || '';
    const label = self.renderInlineAsText(token.children || [], opts, env) || src;
    return md.validateLink(src)
      ? `<a href="${escape(src)}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>`
      : escape(label);
  };
  md.renderer.rules.table_open = () => '<div class="reply-table"><table>\n';
  md.renderer.rules.table_close = () => '</table></div>\n';

  function renderMarkdown(text) {
    // Match the client's treatment of its complete metadata footer. The caller
    // keeps the original text for Copy source and for reply identity checks.
    const opening = '<oai-mem-citation>', closing = '</oai-mem-citation>';
    const start = text.lastIndexOf(opening);
    if (start >= 0 && (start === 0 || /[\r\n]/.test(text[start - 1]))) {
      const end = text.indexOf(closing, start + opening.length);
      if (end >= 0 && !text.slice(end + closing.length).trim()) text = text.slice(0, start).replace(/[\r\n]+$/, '');
    }
    // Keep arbitrarily long replies complete. Large text skips expensive Markdown
    // parsing, with the caller labelling this as source view rather than truncating.
    const plain = text.length > 200000;
    return {plain, html:plain ? `<pre class="reply-source">${escape(text)}</pre>` : md.render(text)};
  }

  function patch(old, next) {
    const sameReply = old.classList.contains('reply-body') && next.classList.contains('reply-body')
      && old.dataset.replyKey === next.dataset.replyKey;
    for (const a of [...old.attributes]) if (!next.hasAttribute(a.name)) old.removeAttribute(a.name);
    for (const a of [...next.attributes]) if (old.getAttribute(a.name) !== a.value) old.setAttribute(a.name, a.value);
    // Identical reply content keeps its actual DOM nodes, scroll and selection.
    if (sameReply) return;
    if (next.classList.contains('reply-body')) {
      old.replaceChildren(...next.childNodes);
      old.scrollTop = 0;
      return;
    }
    const children = [...next.childNodes];
    for (let i = 0; i < children.length; i++) {
      const fresh = children[i], current = old.childNodes[i];
      if (!current) { old.appendChild(fresh); continue; }
      if (current.nodeType === 1 && fresh.nodeType === 1 && current.tagName === fresh.tagName) patch(current, fresh);
      else if (current.nodeType === 3 && fresh.nodeType === 3) {
        if (current.nodeValue !== fresh.nodeValue) current.nodeValue = fresh.nodeValue;
      } else if (!current.isEqualNode(fresh)) current.replaceWith(fresh);
    }
    while (old.childNodes.length > children.length) old.lastChild.remove();
  }

  function renderBoard(board, markup) {
    const template = document.createElement('template');
    // Logic-only test harnesses do not provide a DOM parser.
    if (!template.content) { board.innerHTML = markup; return; }
    template.innerHTML = markup;
    const cols = [...board.children], nextCols = [...template.content.children];
    if (cols.length !== nextCols.length) { board.replaceChildren(...nextCols); return; }
    const existing = new Map([...board.querySelectorAll('.card')].map(c => [c.dataset.id, c]));
    const wanted = new Set([...template.content.querySelectorAll('.card')].map(c => c.dataset.id));
    for (const [id, card] of existing) if (!wanted.has(id)) card.remove();
    nextCols.forEach((next, i) => {
      const old = cols[i];
      patch(old.querySelector('.col-head'), next.querySelector('.col-head'));
      const body = old.querySelector('.col-body'), incoming = next.querySelector('.col-body');
      const cards = [...incoming.children];
      if (!cards.some(c => c.classList.contains('card'))) { patch(body, incoming); return; }
      body.querySelectorAll('.empty,.slot').forEach(n => n.remove());
      cards.forEach((fresh, at) => {
        const card = existing.get(fresh.dataset.id);
        if (card) patch(card, fresh);
        const chosen = card || fresh;
        if (body.children[at] !== chosen) body.insertBefore(chosen, body.children[at] || null);
      });
    });
  }
  root.SwitchReply = {renderMarkdown, renderBoard};
})(globalThis);
