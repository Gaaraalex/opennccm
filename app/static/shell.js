/* Armazón de la interfaz «Señal» (igual que el prototipo de diseño): barra lateral plegable, cajón en el móvil,
   menús de la barra superior (tema y acento, notificaciones), atajos de teclado, avisos flotantes, paleta Ctrl K y
   formularios en paneles laterales. Sin dependencias externas. Sin JavaScript todo sigue funcionando: los enlaces
   llevan a sus páginas y los avisos se ven como texto. */
(function () {
  'use strict';
  var OC = window.OC || {}, T = OC.t || {};
  var app = document.getElementById('app');
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); };
  var icon = function (n, c) { return '<svg class="ico ' + (c || '') + '" aria-hidden="true"><use href="/static/icons.svg#i-' + n + '"/></svg>'; };
  var RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
  window.ocIcon = icon; window.ocEsc = esc;

  function postForm(url, data) {  // envía un formulario «de verdad» (POST + redirección del servidor)
    var f = document.createElement('form');
    f.method = 'post'; f.action = url; f.hidden = true;
    Object.keys(data || {}).forEach(function (k) {
      var i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = data[k]; f.appendChild(i);
    });
    document.body.appendChild(f); f.submit();
  }
  window.ocPost = postForm;

  /* ---------- barra lateral: plegar (escritorio) y cajón (móvil) ---------- */
  var scrimRail = document.getElementById('rail-scrim');
  function drawer(open) {
    if (!app) return;
    app.classList.toggle('drawer', open);
    if (scrimRail) scrimRail.hidden = !open;
    if (open) { var first = $('#rail a.nav'); if (first) first.focus(); }
  }
  $$('[data-mini]').forEach(function (b) {
    b.addEventListener('click', function () {
      app.classList.toggle('mini');
      try { localStorage.setItem('oc-rail', app.classList.contains('mini') ? 'mini' : 'full'); } catch (e) { /* sin almacenamiento */ }
    });
  });
  $$('[data-drawer]').forEach(function (b) { b.addEventListener('click', function () { drawer(!app.classList.contains('drawer')); }); });
  if (scrimRail) scrimRail.addEventListener('click', function () { drawer(false); });
  $$('.rail .nav.on, .mobnav a.on').forEach(function (a) { a.setAttribute('aria-current', 'page'); });

  /* ---------- menús desplegables de la barra superior y de las páginas ---------- */
  function closeDrops(except) {
    $$('.drop[data-open]').forEach(function (d) {
      if (d === except) return;
      d.hidden = true; d.removeAttribute('data-open');
      var b = $('[data-drop="' + d.id + '"]'); if (b) b.setAttribute('aria-expanded', 'false');
    });
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-drop]');
    if (b) {
      var d = document.getElementById(b.dataset.drop);
      if (!d) return;
      var open = d.hidden;
      closeDrops(d);
      d.hidden = !open;
      if (open) d.setAttribute('data-open', ''); else d.removeAttribute('data-open');
      b.setAttribute('aria-expanded', String(open));
      if (open) { var f = $('button, a, input', d); if (f && e.detail === 0) f.focus(); }
      return;
    }
    if (!e.target.closest('.drop')) closeDrops();
  });

  /* ---------- tema y acento: se aplican al momento y se guardan en el usuario ---------- */
  function isDark() {
    var t = document.documentElement.dataset.theme;
    return t === 'oscuro' || t === 'contraste' || (t === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  }
  function paintSwatches() {
    $$('[data-set-accent]').forEach(function (b) { b.style.background = isDark() ? b.dataset.d : b.dataset.l; });
  }
  function savePref(data) {
    var fd = new FormData(); fd.append('quiet', '1');
    Object.keys(data).forEach(function (k) { fd.append(k, data[k]); });
    return fetch('/account/theme', {method: 'POST', body: fd, credentials: 'same-origin'});
  }
  window.ocSavePref = savePref;
  function setTheme(t) {
    document.documentElement.dataset.theme = t;
    savePref({theme: t});
    $$('[data-set-theme]').forEach(function (b) {
      var chk = $('.chk-on', b); if (chk) chk.remove();
      if (b.dataset.setTheme === t) b.insertAdjacentHTML('beforeend', '<span class="chk-on" style="margin-left:auto;color:var(--brand)">' + icon('check', 's') + '</span>');
    });
    var tb = $('[data-drop="theme-menu"] use');
    if (tb) tb.setAttribute('href', '/static/icons.svg#i-' + ({oscuro: 'moon', claro: 'sun', contraste: 'eye'}[t] || 'monitor'));
    paintSwatches();
    document.dispatchEvent(new CustomEvent('oc-theme'));
  }
  window.ocSetTheme = setTheme;
  $$('[data-set-theme] span[style]').forEach(function (s) { s.classList.add('chk-on'); });
  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-set-theme]');
    if (t) { setTheme(t.dataset.setTheme); return; }
    var a = e.target.closest('[data-set-accent]');
    if (a) {
      document.documentElement.dataset.accent = a.dataset.setAccent;
      $$('[data-set-accent]').forEach(function (x) { x.classList.toggle('on', x === a); });
      savePref({accent: a.dataset.setAccent});
      document.dispatchEvent(new CustomEvent('oc-theme'));
    }
  });
  paintSwatches();
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', paintSwatches);

  /* ---------- consola SSH en un diálogo sobre la página (prototipo); el terminal va en un iframe (?embed=1) ---------- */
  document.addEventListener('click', function (e) {
    var a = e.target.closest('a[data-console]');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey) return;  // con modificadores: abrirla en otra pestaña, como un enlace
    e.preventDefault();
    var d = document.createElement('dialog'), href = a.getAttribute('href');
    d.className = 'dlg wide console-dlg';
    d.setAttribute('aria-label', (T.console || '{0}').replace('{0}', a.dataset.name || ''));
    d.innerHTML = '<div class="row nowrap"><span class="vend">' + esc(a.dataset.vend || '') + '</span>' +
      '<h2 class="grow">' + esc((T.console || '{0}').replace('{0}', a.dataset.name || '')) + '</h2>' +
      '<button type="button" class="btn s ghost" data-c="reconnect">' + icon('refresh', 's') + esc(T.reconnect || '') + '</button>' +
      '<button type="button" class="btn s ghost" data-c="full">' + icon('expand', 's') + esc(T.fullscreen || '') + '</button>' +
      '<button type="button" class="btn s icon ghost" data-c="window" title="' + esc(T.window || '') + '" aria-label="' + esc(T.window || '') + '">' + icon('monitor', 's') + '</button>' +
      '<button type="button" class="tbtn" data-c="close" aria-label="' + esc(T.close || '') + '">' + icon('x') + '</button></div>' +
      '<div class="term" style="padding:0;overflow:hidden"><iframe title="' + esc(a.getAttribute('aria-label') || '') + '" src="' + esc(href) + '?embed=1"></iframe></div>' +
      '<div class="row nowrap"><span class="pill" role="status" aria-live="polite">' + esc(T.c_connecting || '') + '</span>' +
      '<p class="muted sm grow" style="margin:0">' + esc(T.c_help || '') + '</p></div>';
    document.body.appendChild(d);
    var frame = $('iframe', d), pill = $('.pill', d);
    function cmd(c) { if (frame.contentWindow) frame.contentWindow.postMessage({oc: 'console', cmd: c}, location.origin); }
    function onMsg(ev) {
      if (ev.origin !== location.origin || ev.source !== frame.contentWindow || !ev.data || ev.data.oc !== 'console') return;
      var s = ev.data.state;
      pill.textContent = T['c_' + {connecting: 'connecting', connected: 'connected'}[s]] || T.c_closed || s;
      pill.className = 'pill ' + ({connected: 'ok', disconnected: 'warn'}[s] || '');
    }
    window.addEventListener('message', onMsg);
    d.addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-c]');
      if (ev.target === d) { d.close(); return; }  // clic en el fondo
      if (!b) return;
      var c = b.dataset.c;
      if (c === 'close') d.close();
      else if (c === 'reconnect') cmd('reconnect');
      else if (c === 'full') { d.classList.toggle('full'); setTimeout(function () { cmd('fit'); }, 60); }
      else if (c === 'window') { d.close(); if (window.abrirVentana) window.abrirVentana(href, 'consola-' + (a.dataset.console || ''), 1000, 640); else window.open(href); }
    });
    d.addEventListener('close', function () { window.removeEventListener('message', onMsg); d.remove(); });  // cierra la sesión SSH
    d.showModal();
    frame.addEventListener('load', function () { cmd('fit'); });
  });

  /* ---------- «Apoya OpenNCCM»: en el móvil, diálogo centrado en lugar del desplegable; «Donar» del aviso ---------- */
  document.addEventListener('click', function (e) {
    var dlg = $('#don-dlg');
    var top = e.target.closest('[data-drop="don-pop"]');
    if (top && dlg && window.innerWidth < 860) {
      e.preventDefault(); e.stopImmediatePropagation(); closeDrops(); dlg.showModal(); return;
    }
    if (e.target.closest('[data-donate]')) {
      e.preventDefault();
      var b = $('[data-drop="don-pop"]');
      if (b && window.innerWidth >= 860 && b.offsetParent !== null) { e.stopImmediatePropagation(); closeDrops(); setTimeout(function () { b.click(); }, 0); }
      else if (dlg) dlg.showModal();
      var t = e.target.closest('.toast'); if (t) t.remove();
      return;
    }
    if (e.target.closest('[data-dismiss-toast]')) { var w = e.target.closest('.toast'); if (w) w.remove(); }
  }, true);

  /* ---------- diálogos (atajos, confirmaciones creadas por las páginas) ---------- */
  var overlay = null;
  function closeOverlay() {
    if (!overlay) return;
    var back = overlay._from;
    overlay.remove(); overlay = null;
    document.body.classList.remove('locked');
    if (back && back.focus) back.focus();
  }
  window.ocClose = closeOverlay;
  function dialog(html, cls, from) {
    closeOverlay();
    overlay = document.createElement('div');
    overlay.className = 'scrim';
    overlay._from = from || document.activeElement;
    overlay.innerHTML = '<div class="dlg ' + (cls || '') + '" role="dialog" aria-modal="true">' + html + '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', function (e) { if (e.target === overlay || e.target.closest('[data-close]')) closeOverlay(); });
    var f = $('input:not([type=hidden]), .btn.pri, [data-close]', overlay); if (f) f.focus();
    return overlay;
  }
  window.ocDialog = dialog;
  function help() { var t = document.getElementById('help-tpl'); if (t) dialog(t.innerHTML); }
  $$('[data-help]').forEach(function (b) { b.addEventListener('click', help); });

  /* ---------- avisos flotantes (toasts) ---------- */
  function enhanceToast(f, sub) {
    if (f.closest('.toast')) return;
    var err = f.classList.contains('error');
    var w = document.createElement('div');
    w.className = 'toast' + (err ? ' err' : '');
    w.setAttribute('role', err ? 'alert' : 'status');
    f.parentNode.insertBefore(w, f);
    w.innerHTML = '<span class="bd">' + icon(err ? 'x' : 'check', 's') + '</span><div></div>';
    w.children[1].appendChild(f);
    if (sub) w.children[1].insertAdjacentHTML('beforeend', '<div class="s">' + sub + '</div>');
    var close = document.createElement('button');
    close.type = 'button';
    close.setAttribute('aria-label', T.close || 'Cerrar');
    close.innerHTML = icon('x', 's');
    close.addEventListener('click', function () { w.remove(); });
    w.appendChild(close);
    return w;
  }
  function autoClose(w, ms) {
    setTimeout(function () {
      if (!w.isConnected || w.contains(document.activeElement)) return;  // no quitarlo mientras se usa con el teclado
      w.classList.add('out'); setTimeout(function () { w.remove(); }, 300);
    }, ms);
  }
  var undo = $('.toasts > .undo-form');
  $$('.toasts > .flash').forEach(function (f, i, all) {
    var w = enhanceToast(f);
    if (!w) return;
    if (undo && i === all.length - 1 && !w.classList.contains('err')) {  // «Deshacer» va en el último aviso
      var s = document.createElement('div'); s.className = 's'; s.appendChild(undo); undo.hidden = false;
      w.children[1].appendChild(s); undo = null;
      autoClose(w, 7000);
    } else if (!w.classList.contains('err')) autoClose(w, 5200);
  });
  if (undo) undo.remove();
  window.ocToast = function (msg, kind, sub) {
    var box = $('.toasts') || document.body.appendChild(Object.assign(document.createElement('div'), {className: 'toasts'}));
    var f = document.createElement('div');
    f.className = 'flash ' + (kind || 'ok');
    f.textContent = msg;
    box.appendChild(f);
    var w = enhanceToast(f, sub);
    if (kind !== 'error') autoClose(w, 5200);
  };

  /* ---------- paneles laterales (formularios de alta y edición, versión, importar…) ---------- */
  function openSheet(a) {
    var href = a.getAttribute('href'), wide = a.hasAttribute('data-wide');
    closeOverlay();
    overlay = document.createElement('div');
    overlay.className = 'sheetw';
    overlay._from = a;
    overlay.innerHTML = '<aside class="sheet ' + (wide ? 'wide' : '') + '" role="dialog" aria-modal="true" aria-busy="true"><header><div class="grow"><h2>' +
      esc(a.dataset.title || a.textContent.trim()) + '</h2></div><button type="button" class="tbtn" data-close aria-label="' + esc(T.close || 'Cerrar') + '">' +
      icon('x') + '</button></header><div class="body"><p class="muted">' + esc(T.loading || '…') + '</p></div></aside>';
    document.body.appendChild(overlay);
    document.body.classList.add('locked');
    var ov = overlay;
    ov.addEventListener('click', function (e) { if (e.target === ov || e.target.closest('[data-close]')) closeOverlay(); });
    fetch(href, {credentials: 'same-origin'}).then(function (r) {
      if (!r.ok || r.redirected) throw new Error(r.status);
      return r.text();
    }).then(function (html) {
      if (ov !== overlay) return;
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var main = doc.getElementById('contenido');
      if (!main) throw new Error('sin contenido');
      var h1 = $('h1', main), sheet = $('.sheet', ov);
      if (h1) { $('header h2', sheet).textContent = h1.textContent.trim(); h1.remove(); }
      var sub = $('[data-sheet-sub]', main);
      if (sub) { $('header .grow', sheet).insertAdjacentHTML('beforeend', '<div class="muted sm" style="margin-top:2px">' + sub.innerHTML + '</div>'); sub.remove(); }
      $$('.toasts, .tabs.admin, .page-only', main).forEach(function (n) { n.remove(); });
      if (main.querySelector('[data-sheet-wide]')) sheet.classList.add('wide');
      var body = $('.body', sheet);
      body.innerHTML = '';
      $$(':scope > *', main).forEach(function (n) { body.appendChild(document.importNode(n, true)); });
      $$('form', body).forEach(function (f, i) {  // sin action, el formulario se enviaría a la página de fondo
        if (!f.getAttribute('action')) f.setAttribute('action', href);
        if (!f.id) f.id = 'sheet-form-' + i;
      });
      var foot = $('[data-sheet-foot]', body);  // la fila de botones pasa al pie fijo del panel
      if (foot) {
        var form = foot.closest('form');
        var footer = document.createElement('footer');
        while (foot.firstChild) footer.appendChild(foot.firstChild);
        $$('button:not([form]), input[type=submit]', footer).forEach(function (b) { if (form && !b.closest('form')) b.setAttribute('form', form.id); });
        foot.remove();
        sheet.appendChild(footer);
      }
      $$('script', sheet).forEach(function (old) {  // los scripts importados no se ejecutan solos
        var s = document.createElement('script');
        s.textContent = old.textContent;
        old.replaceWith(s);
      });
      $$('a.btn', sheet).forEach(function (l) {  // «Cancelar» cierra el panel en lugar de navegar
        if (l.getAttribute('href') === location.pathname || l.hasAttribute('data-cancel')) {
          l.addEventListener('click', function (e) { e.preventDefault(); closeOverlay(); });
        }
      });
      if (window.ocLabels) window.ocLabels(sheet);
      sheet.removeAttribute('aria-busy');
      var focus = $('.body input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([readonly]):not([disabled]), .body textarea', sheet);
      if (focus) focus.focus();
      document.dispatchEvent(new CustomEvent('oc-sheet', {detail: sheet}));
    }).catch(function () { location.href = href; });
  }
  window.ocSheet = openSheet;
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[data-sheet]');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
    if (window.matchMedia('(max-width: 560px)').matches) return;  // en pantallas muy pequeñas, página completa
    e.preventDefault();
    closeDrops();
    openSheet(a);
  });

  /* ---------- paleta de comandos (Ctrl K) ---------- */
  var devices = null, palItems = [], palIdx = 0, palTimer = 0, cfgHits = [], cfgQ = '';
  function norm(s) { return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function hl(t, q) {
    if (!q) return esc(t);
    var i = norm(t).indexOf(q);
    return i < 0 ? esc(t) : esc(t.slice(0, i)) + '<mark>' + esc(t.slice(i, i + q.length)) + '</mark>' + esc(t.slice(i + q.length));
  }
  var navPages = $$('#rail a.nav').map(function (a) {
    var u = a.querySelector('use');
    return {i: u ? u.getAttribute('href').split('#i-')[1] : 'right', t: a.querySelector('.txt').textContent.trim(), r: T.go, href: a.getAttribute('href')};
  }).concat((OC.pages || []).map(function (p) { return {i: p[2], t: p[0], r: T.go, href: p[1]}; }));
  function runAction(a) {
    if (a[3] === 'post') postForm(a[1], {});
    else if (a[3] === 'sheet') { var l = document.createElement('a'); l.href = a[1]; l.dataset.title = a[0]; openSheet(l); }
    else if (a[3] === 'theme') setTheme(a[1]);
    else if (a[3] === 'help') help();
    else location.href = a[1];
  }
  function loadDevices() {
    if (devices !== null) return;
    devices = [];
    fetch('/api/devices', {credentials: 'same-origin'}).then(function (r) { return r.ok ? r.json() : []; }).then(function (list) {
      devices = list.map(function (d) {
        return {i: 'server', t: d.name, sub: [d.host, d.platform || d.device_type].filter(Boolean).join(' · '), r: T.device, href: '/devices/' + d.id,
                key: norm([d.name, d.host, d.model, d.serial, d.version, d.device_type].join(' '))};
      });
      if (overlay && overlay.classList.contains('palw')) palFill();
    }).catch(function () { devices = []; });
  }
  function searchCfg(q) {
    clearTimeout(palTimer);
    if (q.length < 2) { cfgHits = []; cfgQ = ''; return; }
    palTimer = setTimeout(function () {
      fetch('/api/config-search?q=' + encodeURIComponent(q), {credentials: 'same-origin'}).then(function (r) { return r.ok ? r.json() : []; }).then(function (hits) {
        cfgQ = q;
        cfgHits = hits.map(function (h) {
          return {i: 'code', t: h.device + ' · ' + (T.line || '{0}').replace('{0}', h.line_no), sub: h.line, r: T.config, href: '/search?q=' + encodeURIComponent(q)};
        });
        if (overlay && overlay.classList.contains('palw')) palFill();
      }).catch(function () {});
    }, 220);
  }
  function palFill() {
    var inp = $('#palq'); if (!inp) return;
    var raw = inp.value.trim(), q = norm(raw);
    var groups = [];
    var devs = (devices || []).filter(function (d) { return !q || d.key.indexOf(q) >= 0; }).slice(0, 5);
    if (devs.length) groups.push([T.devices, devs]);
    if (q.length > 1 && cfgQ === raw && cfgHits.length) groups.push([T.configs, cfgHits]);
    else if (q.length > 1) groups.push([T.configs, [{i: 'code', t: (T.search_in || '{0}').replace('{0}', raw), r: T.config, href: '/search?q=' + encodeURIComponent(raw)}]]);
    var pages = navPages.filter(function (p) { return !q || norm(p.t).indexOf(q) >= 0; });
    if (pages.length) groups.push([T.pages, pages.slice(0, q ? 7 : 4)]);
    var acts = (OC.actions || []).filter(function (a) { return !q || norm(a[0]).indexOf(q) >= 0; })
      .map(function (a) { return {i: a[2], t: a[0], act: a}; });
    if (acts.length) groups.push([T.actions, acts.slice(0, q ? 6 : 4)]);
    palItems = []; groups.forEach(function (g) { palItems = palItems.concat(g[1]); });
    if (palIdx >= palItems.length) palIdx = 0;
    var n = 0;
    $('#palres').innerHTML = palItems.length ? groups.map(function (g) {
      return '<div class="gl label">' + esc(g[0]) + '</div>' + g[1].map(function (it) {
        var k = n++;
        return '<div class="it ' + (k === palIdx ? 'on' : '') + '" role="option" id="pi' + k + '" data-pi="' + k + '">' + icon(it.i, 's') + '<span>' + hl(it.t, q) +
          (it.sub ? ' <span class="muted sm">· ' + hl(it.sub, q) + '</span>' : '') + '</span><span class="r">' + esc(it.r || '') + '</span></div>';
      }).join('');
    }).join('') : '<div class="empty" style="padding:28px"><p>' + esc((T.none || '{0}').replace('{0}', raw)) + '</p></div>';
    inp.setAttribute('aria-activedescendant', palItems.length ? 'pi' + palIdx : '');
  }
  function palRun(i) {
    var it = palItems[i]; if (!it) return;
    closeOverlay();
    if (it.act) runAction(it.act); else location.href = it.href;
  }
  function palette() {
    closeOverlay(); closeDrops();
    overlay = document.createElement('div');
    overlay.className = 'scrim palw';
    overlay._from = document.activeElement;
    overlay.innerHTML = '<div class="pal" role="dialog" aria-modal="true" aria-label="' + esc(T.ph) + '"><div class="q">' + icon('search') +
      '<input id="palq" placeholder="' + esc(T.ph) + '" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true" aria-controls="palres"><kbd>Esc</kbd></div>' +
      '<div class="res" id="palres" role="listbox"></div><div class="ft"><span><kbd>↑</kbd> <kbd>↓</kbd> ' + esc(T.move) + '</span><span><kbd>↵</kbd> ' + esc(T.open) +
      '</span><span class="grow"></span><a href="/search">' + esc(T.adv) + '</a></div></div>';
    document.body.appendChild(overlay);
    var ov = overlay;
    ov.addEventListener('click', function (e) {
      if (e.target === ov) { closeOverlay(); return; }
      var it = e.target.closest('.it[data-pi]'); if (it) palRun(+it.dataset.pi);
    });
    ov.addEventListener('mousemove', function (e) {
      var it = e.target.closest('.it[data-pi]');
      if (it && +it.dataset.pi !== palIdx) { palIdx = +it.dataset.pi; $$('.it', ov).forEach(function (x) { x.classList.toggle('on', +x.dataset.pi === palIdx); }); }
    });
    var inp = $('#palq');
    inp.addEventListener('input', function () { palIdx = 0; searchCfg(inp.value.trim()); palFill(); });
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); if (!palItems.length) return;
        palIdx = (palIdx + (e.key === 'ArrowDown' ? 1 : -1) + palItems.length) % palItems.length; palFill();
        var on = $('.it.on', ov); if (on) on.scrollIntoView({block: 'nearest'});
      } else if (e.key === 'Enter') { e.preventDefault(); palRun(palIdx); }
    });
    palIdx = 0; loadDevices(); palFill(); inp.focus();
  }
  window.ocPalette = palette;
  $$('[data-palette]').forEach(function (b) { b.addEventListener('click', palette); });

  /* ---------- pestañas de página enlazables (#resumen, #versions…); sin JavaScript se ven todas ---------- */
  $$('[data-tabs]').forEach(function (nav) {
    var links = $$('a[href^="#"]', nav);
    var panels = links.map(function (a) { return document.getElementById(a.getAttribute('href').slice(1)); }).filter(Boolean);
    if (!panels.length) return;
    nav.setAttribute('role', 'tablist');
    function show(id, push) {
      var target = panels.filter(function (p) { return p.id === id; })[0] || panels[0];
      panels.forEach(function (p) { p.hidden = p !== target; });
      links.forEach(function (a) {
        var on = a.getAttribute('href') === '#' + target.id;
        a.setAttribute('aria-selected', String(on));
        a.classList.toggle('on', on);
      });
      if (push) history.replaceState(null, '', '#' + target.id);
      document.dispatchEvent(new CustomEvent('oc-tab', {detail: target.id}));
    }
    links.forEach(function (a) {
      a.setAttribute('role', 'tab');
      a.addEventListener('click', function (e) { e.preventDefault(); show(a.getAttribute('href').slice(1), true); });
    });
    $$('[data-tab-link]').forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault(); closeDrops();
        show(a.getAttribute('href').slice(1), true);
        nav.scrollIntoView({block: 'nearest'});
      });
    });
    show(location.hash.slice(1), false);
    window.addEventListener('hashchange', function () { show(location.hash.slice(1), false); });
  });

  /* ---------- diálogos de confirmación: «escribe el nombre para confirmar» ---------- */
  /* (delegados: también funcionan en el contenido cargado en un panel lateral) */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-dialog]');
    if (btn) {
      var d = document.getElementById(btn.dataset.dialog);
      if (!d || !d.showModal) return;
      closeDrops();
      d.showModal();
      var inp = $('[data-expect]', d);
      if (inp) { inp.value = ''; inp.dispatchEvent(new Event('input', {bubbles: true})); inp.focus(); }
      return;
    }
    var c = e.target.closest('dialog [data-close]');
    if (c) c.closest('dialog').close();
  });
  document.addEventListener('input', function (e) {
    var inp = e.target.closest && e.target.closest('dialog [data-expect]');
    if (!inp) return;
    var go = $('button[type=submit], button:not([type])', inp.form);
    if (go) go.disabled = inp.value.trim() !== inp.dataset.expect;
  });

  /* ---------- botones que trabajan: girar el icono mientras se envía el formulario ---------- */
  document.addEventListener('submit', function (e) {
    var b = e.submitter;
    if (b && b.classList.contains('btn') && !b.hasAttribute('data-no-busy')) {
      b.classList.add('busy');
      var u = $('use', b); if (u) u.setAttribute('href', '/static/icons.svg#i-spin');
    }
  });

  /* ---------- teclado global ---------- */
  var gKey = 0;
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      if (overlay && overlay.classList.contains('palw')) closeOverlay(); else palette();
      return;
    }
    if (e.key === 'Escape') {
      if (overlay) closeOverlay();
      else if (app && app.classList.contains('drawer')) drawer(false);
      closeDrops();
      return;
    }
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || overlay || document.querySelector('dialog[open]')) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === '/') { e.preventDefault(); palette(); return; }
    if (e.key === '?') { help(); return; }
    if (e.key === 'g') { gKey = Date.now(); return; }
    if (Date.now() - gKey < 900) {
      var m = {p: '/', d: '/devices', c: '/changes', a: '/events'}[e.key];
      if (m) { gKey = 0; location.href = m; }
    }
  });

  /* ---------- formularios largos (Ajustes): barra «Tienes cambios sin guardar» y aviso al salir ---------- */
  $$('form[data-dirty]').forEach(function (form) {
    var bar = null, dirty = false, initial = null,
        saveLabel = (form.querySelector('[data-save]') || form.querySelector('button:not([type=button]):not([formaction])') || {}).textContent || 'OK';
    function snap() { return new URLSearchParams(new FormData(form)).toString(); }
    /* la foto inicial se toma tras cargar (el autocompletado del navegador no cuenta como cambio) */
    window.addEventListener('load', function () { setTimeout(function () { initial = snap(); }, 400); });
    function show(on) {
      dirty = on;
      if (on && !bar) {
        bar = document.createElement('div');
        bar.className = 'savebar';
        bar.setAttribute('role', 'region');
        bar.setAttribute('aria-label', T.dirty || '');
        bar.innerHTML = '<span></span><button type="button" class="btn s ghost"></button><button type="button" class="btn s sig">' + icon('save', 's') + '<span></span></button>';
        bar.children[0].textContent = T.dirty || '';
        bar.children[1].textContent = T.discard || '';
        bar.children[2].lastChild.textContent = saveLabel.trim();
        bar.children[1].addEventListener('click', function () { form.reset(); show(false); form.dispatchEvent(new Event('oc-reset')); });
        bar.children[2].addEventListener('click', function () { if (form.requestSubmit) form.requestSubmit(); else form.submit(); });
        document.body.appendChild(bar);
      }
      if (bar) bar.hidden = !on;
    }
    function check() { show(initial === null || snap() !== initial); }
    form.addEventListener('input', check);
    form.addEventListener('change', check);
    form.addEventListener('submit', function () { dirty = false; });
    window.addEventListener('beforeunload', function (e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  });

  /* ---------- números que cuentan al aparecer (indicadores del panel) ---------- */
  $$('[data-count]').forEach(function (n) {
    var to = +n.dataset.count, suf = n.dataset.suffix || '';
    if (isNaN(to) || RM) return;
    var t0 = performance.now();
    (function step(t) {
      var k = Math.min(1, (t - t0) / 900), ez = 1 - Math.pow(1 - k, 3);
      n.textContent = Math.round(to * ez) + suf;
      if (k < 1) requestAnimationFrame(step);
    })(t0);
  });

})();
