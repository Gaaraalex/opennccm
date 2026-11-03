/* Asistente IA: panel con contexto, preguntas sugeridas y respuestas con comandos (riesgo, Copiar, Insertar y
   «Enviar como solicitud de cambio»). Nunca ejecuta nada. Se usa dentro de la consola (#ai-panel) y, flotante, desde
   la barra superior, la comparación de versiones («✦ Explicar este cambio») y Cumplimiento («✦ ¿Cómo lo corrijo?»):
   window.ocAi.open({device_id, kind, a, b, id, title, ask}). */
(function () {
  'use strict';
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); };
  var panel = null, T = {}, opts = {}, info = null, busy = false;
  function store(k, v) {  // preferencias del panel en este navegador (cabecera oculta)
    try { if (v === undefined) return localStorage.getItem('oc-ai-' + k); localStorage.setItem('oc-ai-' + k, v); } catch (e) { return null; }
    return null;
  }
  function sug(show) {  // preguntas sugeridas: se ven al empezar y se pliegan al preguntar; el botón de abajo las muestra
    var s = panel.querySelector('.ai-sug'), b = panel.querySelector('[data-sugtog]');
    s.hidden = !show; b.setAttribute('aria-expanded', show ? 'true' : 'false'); b.lastChild.textContent = show ? '▾' : '▴';
  }
  // modelo de la cuenta que usa este usuario (lo elige aquí, en el chat de la barra superior o en Ajustes el de fábrica)
  function models(sel, list, cur) {
    list = list || [];
    sel.hidden = list.length < 2;
    sel.innerHTML = list.map(function (m) { return '<option' + (m === cur ? ' selected' : '') + '>' + esc(m) + '</option>'; }).join('');
  }
  function pickModel(sel) {
    fetch('/api/ai/model', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({model: sel.value})})
      .then(function (r) { return r.json(); }).then(function (j) {
        if (j.error) { note(j.error); return; }
        if (info) info.model = j.model;
        var w = panel.querySelector('.ai-who'); if (w && info) w.textContent = j.model + ' · ' + info.who;
        note((T.modelset || '{0}').replace('{0}', j.model));
      });
  }

  function build(el, o) {
    panel = el; opts = o || {};
    T = JSON.parse(el.dataset.t || '{}');
    var lg = document.getElementById('ai-logo');
    // arriba, ocultable (proveedor, contexto y aviso); abajo del todo, la fila de herramientas y la pregunta
    el.innerHTML = '<div class="ai-hd"' + (store('head') === '0' ? ' hidden' : '') + '><div class="ai-h">' + (lg ? lg.innerHTML : '<span class="ai-logo" aria-hidden="true">✦</span>') +
      '<div class="grow"><b>' + esc(T.title) + '</b><small class="ai-who"></small></div><span class="ai-cloud">' + esc(T.cloud) + '</span></div>' +
      '<div class="ai-ctx"></div><div class="ai-note">' + esc(T.note) + '</div></div>' +
      '<div class="ai-msgs" role="log" aria-live="polite"></div><div class="ai-sug" hidden></div>' +
      '<div class="ai-tools"><button type="button" class="ai-tg" data-sugtog aria-expanded="false" hidden>✦ ' + esc(T.sugtog) + ' <span aria-hidden="true">▴</span></button>' +
      '<span class="grow"></span><select class="ai-model" aria-label="' + esc(T.model) + '" hidden></select>' +
      '<button type="button" class="ai-tg" data-headtog aria-expanded="' + (store('head') === '0' ? 'false' : 'true') + '" title="' + esc(T.head) + '" aria-label="' + esc(T.head) + '">ⓘ</button>' +
      (el.classList.contains('ai-float') ? '<button type="button" class="ai-tg ai-x" aria-label="' + esc(T.close) + '">✕</button>' : '') + '</div>' +
      '<form class="ai-in"><textarea rows="1" placeholder="' + esc(T.ph) + '" aria-label="' + esc(T.ph) + '"></textarea><button class="btn s pri" type="submit">↑</button></form>';
    var x = el.querySelector('.ai-x'); if (x) x.addEventListener('click', function () { el.hidden = true; });
    el.querySelector('[data-headtog]').addEventListener('click', function () {
      var top = el.querySelector('.ai-hd'); top.hidden = !top.hidden; store('head', top.hidden ? '0' : '1'); this.setAttribute('aria-expanded', top.hidden ? 'false' : 'true');
    });
    el.querySelector('[data-sugtog]').addEventListener('click', function () { sug(el.querySelector('.ai-sug').hidden); });
    el.querySelector('.ai-model').addEventListener('change', function () { pickModel(this); });
    el.querySelector('form').addEventListener('submit', function (e) { e.preventDefault(); var ta = el.querySelector('textarea'); ask(ta.value); ta.value = ''; });
    el.querySelector('textarea').addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); el.querySelector('form').requestSubmit(); } });
    el.addEventListener('click', onClick);
    var dev = opts.device_id || el.dataset.device || '';
    fetch('/api/ai/info' + (dev ? '?device_id=' + dev : ''), {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (j) {
      info = j;
      if (!j.on) return;
      el.querySelector('.ai-who').textContent = j.model + ' · ' + j.who;
      var chips = [];
      if (el.dataset.name) chips.push(el.dataset.name);
      if (el.dataset.os) chips.push(el.dataset.os);
      if (j.backup && dev) chips.push(T.backup);
      if (j.console && el.dataset.kind === 'console') chips.push(T.console);
      el.querySelector('.ai-ctx').innerHTML = chips.map(function (c) { return '<span>' + esc(c) + '</span>'; }).join('') + '<span class="ok">🔒 ' + esc(T.secrets) + '</span>';
      el.querySelector('.ai-sug').innerHTML = (j.suggest || []).map(function (q) { return '<button type="button" data-q="' + esc(q) + '">' + esc(q) + '</button>'; }).join('');
      el.querySelector('[data-sugtog]').hidden = !(j.suggest || []).length;
      sug(!!(j.suggest || []).length && !el.querySelector('.ai-m.user'));
      models(el.querySelector('.ai-model'), j.models, j.model);
      if (opts.ask) ask(opts.ask);
    });
    if (T.hello && el.dataset.kind === 'console') msg('bot', '<p>' + esc(T.hello) + '</p>');
  }

  function msg(who, html) {
    var box = panel.querySelector('.ai-msgs'), d = document.createElement('div');
    d.className = 'ai-m ' + who; d.innerHTML = html; box.appendChild(d); box.scrollTop = box.scrollHeight; return d;
  }
  function para(t) { return t.split(/\n{2,}/).map(function (p) { return '<p>' + esc(p).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/\n/g, '<br>') + '</p>'; }).join(''); }
  function color(t) { return window.CXcolor ? t.split('\n').map(window.CXcolor).join('\n') : esc(t); }

  function ask(q) {
    q = (q || '').trim();
    if (!q || busy) return;
    busy = true;
    sug(false);
    msg('user', esc(q));
    var wait = msg('bot', '<span class="ai-think">' + esc(T.thinking) + '</span>');
    var body = {q: q, device_id: +(opts.device_id || panel.dataset.device || 0) || null, kind: opts.kind || panel.dataset.kind || '',
                a: opts.a, b: opts.b, id: opts.id, text: opts.text, title: opts.title || document.title};
    if (panel.dataset.kind === 'console' && window.CXlines) body.console = window.CXlines(200);
    fetch('/api/ai/ask', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
      .then(function (r) { return r.json(); }).then(function (j) {
        busy = false;
        if (j.error) { wait.innerHTML = '<p class="k-bad">' + esc(j.error) + '</p>'; return; }
        wait.innerHTML = j.segments.map(function (s) {
          if (s.k === 'text') return para(s.t);
          return '<div class="ai-cmd"><pre>' + color(s.t) + '</pre><div class="row"><span class="ai-risk ' + s.risk + '">' + esc(T.risk[s.risk]) + '</span><span class="grow"></span>' +
            '<button type="button" class="btn s ghost" data-copy="' + esc(s.t) + '">' + esc(T.copy) + '</button>' +
            (window.CXinsert || window.ocAiInsert ? '<button type="button" class="btn s ghost" data-ins="' + esc(s.t) + '">' + esc(T.insert) + '</button>' : '') +
            (s.risk !== 'read' && body.device_id ? '<button type="button" class="btn s" data-cr="' + esc(s.t) + '">' + esc(T.cr) + '</button>' : '') + '</div></div>';
        }).join('') + '<div class="meta">' + (j.source ? esc(T.source.replace('{0}', j.source)) + ' · ' : '') + esc(j.model) + ' · ' + esc(j.who) + ' · ' + j.ms + ' ms</div>';
        wait.parentNode.scrollTop = wait.parentNode.scrollHeight;
      }).catch(function () { busy = false; wait.innerHTML = '<p class="k-bad">…</p>'; });
  }

  function onClick(e) {
    var b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.q) ask(b.dataset.q);
    else if (b.dataset.copy != null) navigator.clipboard.writeText(b.dataset.copy).then(function () { note(T.copied); });
    else if (b.dataset.ins != null) { (window.CXinsert || window.ocAiInsert)(b.dataset.ins.split('\n')[0]); }
    else if (b.dataset.cr != null) {
      var dev = +(opts.device_id || panel.dataset.device || 0);
      fetch('/console/change-request', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({device_ids: [dev], body: b.dataset.cr, title: '✦ ' + (T.title || '')})}).then(function (r) { return r.json(); }).then(function (j) {
        if (j.url) { note(T.crdone); window.open(j.url, '_blank', 'noopener'); } else note(j.error || '…');
      });
    }
  }
  function note(t) { if (window.CXtoast) window.CXtoast(t); else if (window.ocToast) window.ocToast(t); else alert(t); }

  window.ocAi = {
    focus: function () { if (panel) panel.querySelector('textarea').focus(); },
    open: function (o) {  // flotante, desde cualquier página
      var el = document.getElementById('ai-float');
      if (!el) {
        el = document.createElement('aside'); el.id = 'ai-float'; el.className = 'ai-panel ai-float';
        el.dataset.t = (document.getElementById('ai-t') || {}).textContent || '{}';
        document.body.appendChild(el);
      }
      el.hidden = false;
      el.dataset.device = (o && o.device_id) || ''; el.dataset.kind = (o && o.kind) || 'page';
      el.dataset.name = (o && o.name) || ''; el.dataset.os = '';
      build(el, o);
      el.querySelector('textarea').focus();
    }
  };
  var docked = document.getElementById('ai-panel');
  if (docked) build(docked, {});
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-ai]');
    if (!b) return;
    e.preventDefault();
    var d = b.dataset;
    window.ocAi.open({kind: d.ai, device_id: d.device || '', a: d.a, b: d.b, id: d.id, name: d.name || '', ask: d.ask || ''});
  });
})();
