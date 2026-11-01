/* Chat del asistente en todo el sistema: panel lateral que abre el botón «Asistente» de la barra superior, en
   cualquier página y sin salir de ella. Respuestas simples (texto corto, equipos en una línea, como mucho 2 botones y el
   resto en «Más acciones», la fuente) y tarjeta de confirmación antes de cualquier acción que cambie algo. */
(function () {
  'use strict';
  var T = JSON.parse((document.getElementById('ai-t') || {}).textContent || '{}');
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); };
  var btn = document.querySelector('[data-chat]'), panel = null, busy = false, info = null;
  if (!btn) return;
  window.ocChat = true;
  function title() { var c = document.querySelector('.crumbs b'); return (c && c.textContent.trim()) || document.title.split('·')[0].trim(); }
  function dev() { var m = location.pathname.match(/^\/devices\/(\d+)/); return m ? (document.querySelector('h1') || {}).textContent || m[1] : ''; }
  function post(url, body) { return fetch(url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})}).then(function (r) { return r.json(); }); }

  function build() {
    panel = document.createElement('aside');
    panel.className = 'ai-chat'; panel.id = 'ai-chat'; panel.setAttribute('aria-label', T.chat);
    var lg = document.getElementById('ai-logo');
    panel.innerHTML = '<div class="ai-ch">' + (lg ? lg.innerHTML : '') + '<div class="grow"><b>' + esc(T.chat) + '</b><small class="ai-who"></small></div>' +
      '<span class="ai-cloud">' + esc(T.cloud) + '</span><button type="button" class="tbtn" data-new title="' + esc(T.newc) + '" aria-label="' + esc(T.newc) + '">+</button>' +
      '<button type="button" class="tbtn" data-x aria-label="' + esc(T.close) + '">✕</button></div>' +
      '<div class="ai-view sm"></div><div class="ai-msgs" role="log" aria-live="polite"></div>' +
      '<div class="ai-chips"></div><form class="ai-in"><textarea rows="1" placeholder="' + esc(T.chatph) + '" aria-label="' + esc(T.chatph) + '"></textarea><button class="btn s pri" type="submit">↑</button></form>' +
      '<div class="ai-note">' + esc(T.chatnote) + '</div>';
    document.body.appendChild(panel);
    panel.querySelector('[data-x]').addEventListener('click', close);
    panel.querySelector('[data-new]').addEventListener('click', function () { post('/api/ai/chat/new').then(function () { panel.querySelector('.ai-msgs').innerHTML = ''; empty(); }); });
    panel.querySelector('form').addEventListener('submit', function (e) { e.preventDefault(); var ta = panel.querySelector('textarea'); ask(ta.value); ta.value = ''; });
    panel.querySelector('textarea').addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); panel.querySelector('form').requestSubmit(); } });
    panel.addEventListener('click', onClick);
  }
  function load(then) {
    fetch('/api/ai/chat?path=' + encodeURIComponent(location.pathname), {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (j) {
      info = j;
      if (!j.on) return;
      panel.querySelector('.ai-who').textContent = j.model + ' · ' + j.who;
      panel.querySelector('.ai-view').innerHTML = esc(T.viewing.replace('{0}', title()).replace('{1}', j.devices)) + ' · <span class="ok">' + esc(T.secrets) + '</span>';
      panel.querySelector('.ai-chips').innerHTML = (j.suggest || []).map(function (q) { return '<button type="button" data-q="' + esc(q) + '">' + esc(q) + '</button>'; }).join('');
      var box = panel.querySelector('.ai-msgs'); box.innerHTML = '';
      (j.messages || []).forEach(function (m) { if (m.role === 'user') user(m.text); else bot(m); });
      empty();
      if (then) then();
    });
  }
  function open(q) {
    if (!panel) build();
    panel.hidden = false; document.body.classList.add('ai-open'); btn.setAttribute('aria-pressed', 'true');
    try { sessionStorage.setItem('oc-chat', '1'); } catch (e) { /* sin almacenamiento */ }
    load(function () { if (q) ask(q); else panel.querySelector('textarea').focus(); });
  }
  function close() {
    panel.hidden = true; document.body.classList.remove('ai-open'); btn.setAttribute('aria-pressed', 'false');
    try { sessionStorage.removeItem('oc-chat'); } catch (e) { /* sin almacenamiento */ }
  }
  function empty() {  // sin conversación: saludo y las sugerencias como botones grandes
    var box = panel.querySelector('.ai-msgs'), chips = panel.querySelector('.ai-chips');
    var none = !box.querySelector('.ai-m');
    chips.hidden = none;
    if (none) box.innerHTML = '<div class="ai-empty"><p>' + esc(T.hello) + '</p>' + (info && info.suggest || []).map(function (q) { return '<button type="button" data-q="' + esc(q) + '">' + esc(q) + '</button>'; }).join('') + '</div>';
  }
  function add(html, cls) {
    var e = panel.querySelector('.ai-empty'); if (e) { e.remove(); panel.querySelector('.ai-chips').hidden = false; } var box = panel.querySelector('.ai-msgs'), d = document.createElement('div'); d.className = 'ai-m ' + cls; d.innerHTML = html; box.appendChild(d); box.scrollTop = box.scrollHeight; return d; }
  function user(t) { return add(esc(t), 'user'); }
  function bot(m, el) {
    var acts = m.actions || [], main = acts.slice(0, 2), more = acts.slice(2);
    var b = function (a, i) {
      return a.kind === 'go' ? '<a class="btn s ' + (i === 0 ? 'pri' : '') + '" href="' + esc(a.url) + '">' + esc(a.label) + '</a>'
        : '<button type="button" class="btn s ' + (i === 0 ? 'pri' : '') + '" data-act="' + esc(a.id) + '" data-t="' + esc(a.title) + '" data-s="' + esc(a.summary) + '" ' + (a.done ? 'disabled' : '') + '>' + esc(a.label) + (a.done ? ' ✓' : '') + '</button>';
    };
    var h = (m.text ? '<p>' + esc(m.text) + '</p>' : '') +
      ((m.items || []).length ? '<ul class="ai-items">' + m.items.map(function (i) { return '<li><span class="dot ' + ({ok: 'ok', aviso: 'warn', error: 'crit'}[i.state] || 'warn') + '"></span><span class="mono">' + esc(i.name) + '</span><span class="muted" title="' + esc(i.reason) + '">' + esc(i.reason) + '</span></li>'; }).join('') + '</ul>' : '') +
      (main.length ? '<div class="ai-acts">' + main.map(b).join('') + '</div>' : '') +
      (more.length ? '<details class="ai-more"><summary>' + esc(T.more) + '</summary><div class="ai-acts">' + more.map(function (a) { return b(a, 1); }).join('') + '</div></details>' : '') +
      (m.source ? '<div class="ai-src">' + esc(m.source) + '</div>' : '');
    if (el) { el.innerHTML = h; return el; }
    return add(h, 'bot');
  }
  function ask(q) {
    q = (q || '').trim(); if (!q || busy) return;
    busy = true; user(q);
    var wait = add('<span class="ai-think">' + esc(T.thinking) + '</span>', 'bot');
    post('/api/ai/chat', {q: q, path: location.pathname, title: title(), device: dev()}).then(function (j) {
      busy = false;
      if (j.error) { wait.innerHTML = '<p class="k-bad">' + esc(j.error) + '</p>'; return; }
      bot(j, wait); panel.querySelector('.ai-msgs').scrollTop = 1e9;
    }).catch(function () { busy = false; wait.innerHTML = '<p class="k-bad">…</p>'; });
  }
  function onClick(e) {
    var q = e.target.closest('[data-q]'), a = e.target.closest('[data-act]'), ok = e.target.closest('[data-ok]'), no = e.target.closest('[data-no]');
    if (q) ask(q.dataset.q);
    else if (a) {
      var card = document.createElement('div'); card.className = 'ai-confirm';
      card.innerHTML = '<b>' + esc(a.dataset.t) + '</b><p class="sm">' + esc(a.dataset.s) + '</p><div class="row end"><button type="button" class="btn s ghost" data-no>' + esc(T.cancel) + '</button>' +
        '<button type="button" class="btn s pri" data-ok="' + esc(a.dataset.act) + '">' + esc(T.confirm) + '</button></div>';
      a.closest('.ai-m').appendChild(card); panel.querySelector('.ai-msgs').scrollTop = 1e9;
    } else if (no) no.closest('.ai-confirm').remove();
    else if (ok) {
      var c = ok.closest('.ai-confirm'); ok.disabled = true;
      post('/api/ai/chat/act', {id: ok.dataset.ok}).then(function (j) {
        c.innerHTML = j.error ? '<p class="k-bad sm">' + esc(j.error) + '</p>' : '<p class="sm">✓ ' + esc(j.msg) + (j.url ? ' <a href="' + esc(j.url) + '">' + esc(T.see) + '</a>' : '') + '</p>';
        var src = document.querySelector('[data-act="' + ok.dataset.ok + '"]'); if (src && !j.error) { src.disabled = true; src.textContent += ' ✓'; }
      });
    }
  }
  btn.addEventListener('click', function () { if (panel && !panel.hidden) close(); else open(); });
  document.addEventListener('click', function (e) {  // «✦ Explicar este cambio» y «✦ ¿Cómo lo corrijo?» abren el chat con su pregunta
    var b = e.target.closest('[data-ai]'); if (!b) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var d = b.dataset, q = d.ask || '';
    if (d.ai === 'diff') q += ' (versiones ' + d.a + ' y ' + d.b + ')';
    if (d.ai === 'compliance') q += ' (' + (d.name || '') + ')';
    open(q);
  }, true);
  try { if (sessionStorage.getItem('oc-chat') === '1') open(); } catch (e) { /* sin almacenamiento */ }
})();
