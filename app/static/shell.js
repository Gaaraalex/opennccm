/* Armazón de la interfaz «Señal»: barra lateral plegable, cajón en el móvil, avisos flotantes, paleta de comandos
   (Ctrl K) y formularios en paneles laterales. Sin dependencias externas. Todo funciona también sin JavaScript:
   los enlaces llevan a sus páginas y los avisos se ven como texto. */
(function () {
  'use strict';
  var T = (window.OC && window.OC.t) || {};
  var shell = document.getElementById('shell');
  var rail = document.getElementById('rail');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); };
  var icon = function (n, c) { return '<svg class="ico ' + (c || '') + '" aria-hidden="true"><use href="/static/icons.svg#i-' + n + '"/></svg>'; };

  /* ---------- barra lateral: plegar (escritorio) y cajón (móvil) ---------- */
  var toggle = document.getElementById('rail-toggle');
  function syncToggle() {
    var mini = shell.classList.contains('mini');
    toggle.setAttribute('aria-expanded', String(!mini));
    toggle.title = mini ? (T.expand || '') : (T.collapse || '');
  }
  if (shell && toggle) {
    syncToggle();
    toggle.addEventListener('click', function () {
      shell.classList.toggle('mini');
      try { localStorage.setItem('oc-rail', shell.classList.contains('mini') ? 'mini' : 'full'); } catch (e) { /* sin almacenamiento */ }
      syncToggle();
    });
  }
  var scrim = document.getElementById('rail-scrim');
  function drawer(open) {
    if (!shell) return;
    shell.classList.toggle('drawer', open);
    if (scrim) scrim.hidden = !open;
    $$('#menu-btn, #mob-more').forEach(function (b) { b.setAttribute('aria-expanded', String(open)); });
    if (open) { var first = $('a.nav', rail); if (first) first.focus(); }
  }
  $$('#menu-btn, #mob-more').forEach(function (b) { b.addEventListener('click', function () { drawer(!shell.classList.contains('drawer')); }); });
  if (scrim) scrim.addEventListener('click', function () { drawer(false); });

  /* ---------- menú de usuario: cerrar al hacer clic fuera ---------- */
  document.addEventListener('click', function (e) {
    $$('details.me[open]').forEach(function (d) { if (!d.contains(e.target)) d.open = false; });
  });

  /* ---------- avisos flotantes (toasts) ---------- */
  function enhanceToast(f) {
    if (f.parentNode && f.parentNode.classList.contains('toast')) return;
    var err = f.classList.contains('error');
    var w = document.createElement('div');
    w.className = 'toast' + (err ? ' err' : '');
    w.setAttribute('role', err ? 'alert' : 'status');
    f.parentNode.insertBefore(w, f);
    w.innerHTML = '<span class="bd">' + icon(err ? 'alert' : 'check', 'sm') + '</span>';
    w.appendChild(f);
    var close = document.createElement('button');
    close.type = 'button';
    close.setAttribute('aria-label', T.close || 'Cerrar');
    close.innerHTML = icon('x', 'sm');
    close.addEventListener('click', function () { w.remove(); });
    w.appendChild(close);
    if (!err) setTimeout(function () { w.classList.add('out'); setTimeout(function () { w.remove(); }, 300); }, 6000);
  }
  $$('.toasts > .flash').forEach(enhanceToast);
  window.ocToast = function (msg, kind) {
    var box = $('.toasts') || document.body.appendChild(Object.assign(document.createElement('div'), {className: 'toasts'}));
    var f = document.createElement('div');
    f.className = 'flash ' + (kind || 'ok');
    f.textContent = msg;
    box.appendChild(f);
    enhanceToast(f);
  };

  /* ---------- paneles laterales para formularios de alta y edición ---------- */
  var sheet = null;
  function closeSheet() {
    if (!sheet) return;
    var back = sheet._from;
    sheet.remove();
    sheet = null;
    document.body.classList.remove('locked');
    if (back) back.focus();
  }
  function openSheet(a) {
    var href = a.getAttribute('href');
    closeSheet();
    sheet = document.createElement('div');
    sheet.className = 'sheet-wrap';
    sheet._from = a;
    sheet.innerHTML = '<aside class="sheet" role="dialog" aria-modal="true" aria-busy="true"><header><h2>' + esc(a.dataset.title || a.textContent.trim()) +
      '</h2><button type="button" class="rail-btn x" aria-label="' + esc(T.close || 'Cerrar') + '">' + icon('x') + '</button></header>' +
      '<div class="body"><p class="muted">' + esc(T.loading || '…') + '</p></div></aside>';
    document.body.appendChild(sheet);
    document.body.classList.add('locked');
    sheet.addEventListener('click', function (e) { if (e.target === sheet) closeSheet(); });
    $('.x', sheet).addEventListener('click', closeSheet);
    fetch(href, {credentials: 'same-origin'}).then(function (r) {
      if (!r.ok || r.redirected) throw new Error(r.status);
      return r.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var main = doc.getElementById('contenido');
      if (!main || !sheet) throw new Error('sin contenido');
      $$('.toasts, .page-foot, .admin-tabs, .page-tabs', main).forEach(function (n) { n.remove(); });
      var h1 = $('h1', main);
      if (h1) { $('header h2', sheet).textContent = h1.textContent.trim(); h1.remove(); }
      var body = $('.body', sheet);
      body.innerHTML = '';
      $$(':scope > *', main).forEach(function (n) { body.appendChild(document.importNode(n, true)); });
      $$('form', body).forEach(function (f) {  // sin action, el formulario se enviaría a la página de fondo
        if (!f.getAttribute('action')) f.setAttribute('action', href);
      });
      $$('script', body).forEach(function (old) {  // los scripts importados no se ejecutan solos
        var s = document.createElement('script');
        s.textContent = old.textContent;
        old.replaceWith(s);
      });
      $$('a', body).forEach(function (l) {  // «Cancelar» cierra el panel en lugar de navegar
        if (l.getAttribute('href') === location.pathname && l.classList.contains('btn')) {
          l.addEventListener('click', function (e) { e.preventDefault(); closeSheet(); });
        }
      });
      if (window.ocLabels) window.ocLabels(body);
      $('.sheet', sheet).removeAttribute('aria-busy');
      var focus = $('input:not([type=hidden]):not([disabled]), select, textarea', body);
      if (focus) focus.focus();
    }).catch(function () { location.href = href; });
  }
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[data-sheet]');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
    if (window.matchMedia('(max-width: 560px)').matches) return;  // en pantallas muy pequeñas, página completa
    e.preventDefault();
    openSheet(a);
  });

  /* ---------- paleta de comandos (Ctrl K) ---------- */
  var pal = document.getElementById('palette');
  var input = document.getElementById('pal-q');
  var res = document.getElementById('pal-res');
  var devices = null, items = [], sel = 0, lastFocus = null;
  function norm(s) { return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  var pages = $$('#rail a.nav').map(function (a) {
    return {g: 'pages', label: a.querySelector('.txt').textContent.trim(), href: a.getAttribute('href'), ic: (a.querySelector('use') || {getAttribute: function () { return '#i-right'; }}).getAttribute('href').split('#i-')[1]};
  }).concat([{g: 'pages', label: T.account || 'Mi cuenta', href: '/account', ic: 'user'}]);
  var actions = [{g: 'actions', label: T.run_backup || 'Equipos', href: '/devices', ic: 'server'}];
  if (window.OC && window.OC.admin) actions.unshift({g: 'actions', label: T.new_device || 'Nuevo', href: '/devices/new', ic: 'plus'});
  function loadDevices() {
    if (devices !== null) return;
    devices = [];
    fetch('/api/devices', {credentials: 'same-origin'}).then(function (r) { return r.ok ? r.json() : []; }).then(function (list) {
      devices = list.map(function (d) {
        return {g: 'devices', label: d.name, sub: [d.host, d.model, d.serial].filter(Boolean).join(' · '), href: '/devices/' + d.id, ic: 'server',
                key: norm([d.name, d.host, d.model, d.serial, d.version].join(' '))};
      });
      render();
    }).catch(function () { devices = []; });
  }
  function render() {
    var q = norm(input.value.trim());
    var match = function (it) { return !q || (it.key || norm(it.label)).indexOf(q) >= 0; };
    var groups = [['devices', (devices || []).filter(match).slice(0, q ? 8 : 5)], ['pages', pages.filter(match).slice(0, 8)],
                  ['actions', actions.filter(match)]];
    if (q) groups.push(['configs', [{g: 'configs', label: (T.search_in || '{0}').replace('{0}', input.value.trim()),
                                      href: '/search?q=' + encodeURIComponent(input.value.trim()), ic: 'code'}]]);
    items = [];
    var html = '';
    groups.forEach(function (g) {
      if (!g[1].length) return;
      html += '<div class="gl label">' + esc(T[g[0]] || g[0]) + '</div>';
      g[1].forEach(function (it) {
        var i = items.length;
        items.push(it);
        html += '<a class="it" role="option" id="pal-' + i + '" href="' + esc(it.href) + '">' + icon(it.ic || 'right') +
                '<span>' + esc(it.label) + '</span>' + (it.sub ? '<span class="r mono">' + esc(it.sub) + '</span>' : '') + '</a>';
      });
    });
    res.innerHTML = html || '<p class="muted pal-none">' + esc(T.none || '') + '</p>';
    sel = Math.min(sel, Math.max(items.length - 1, 0));
    mark();
  }
  function mark() {
    $$('.it', res).forEach(function (el, i) {
      el.classList.toggle('on', i === sel);
      el.setAttribute('aria-selected', String(i === sel));
    });
    var on = document.getElementById('pal-' + sel);
    input.setAttribute('aria-activedescendant', on ? on.id : '');
    if (on) on.scrollIntoView({block: 'nearest'});
  }
  function openPal() {
    if (!pal) return;
    lastFocus = document.activeElement;
    pal.hidden = false;
    document.body.classList.add('locked');
    input.value = '';
    sel = 0;
    loadDevices();
    render();
    input.focus();
  }
  function closePal() {
    if (!pal || pal.hidden) return;
    pal.hidden = true;
    document.body.classList.remove('locked');
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  if (pal) {
    $$('[data-palette]').forEach(function (b) { b.addEventListener('click', openPal); });
    input.addEventListener('input', function () { sel = 0; render(); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, items.length - 1); mark(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); mark(); }
      else if (e.key === 'Enter' && items[sel]) { e.preventDefault(); location.href = items[sel].href; }
    });
    pal.addEventListener('click', function (e) { if (e.target === pal) closePal(); });
    res.addEventListener('mousemove', function (e) {
      var it = e.target.closest('.it');
      if (it) { sel = +it.id.slice(4); mark(); }
    });
  }

  /* ---------- teclado global ---------- */
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      if (pal && pal.hidden) openPal(); else closePal();
      return;
    }
    if (e.key === 'Escape') {
      if (pal && !pal.hidden) closePal();
      else if (sheet) closeSheet();
      else if (shell && shell.classList.contains('drawer')) drawer(false);
      $$('details.me[open]').forEach(function (d) { d.open = false; });
    }
  });
})();
