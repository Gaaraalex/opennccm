/* Operar › Consola (app/templates/console_center.html): sesiones (lanzador, pestañas, 1/2/4, compartir, SFTP),
   multi-equipo, recetas, grabaciones (reproductor), herramientas y acceso. Las secciones están en la misma página
   para que las consolas abiertas no se cierren al cambiar de sección. */
(function () {
  'use strict';
  var D = JSON.parse(document.getElementById('cc-data').textContent), T = D.t;
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); };
  // logo de marca de un equipo (app/vendorlogos.py): clave «extreme», «mikrotik»… del sprite static/vendor-logos.svg
  // ícono del sprite de la app (static/icons.svg)
  function ccIcon(n) { return '<svg class="ico s" aria-hidden="true"><use href="/static/icons.svg#i-' + n + '"/></svg>'; }
  // píldora del sello: verde «Verificada · VOSS 9.3.0.0» o gris «Sin verificar»
  function vPill(r) {
    return r.verified_on ? '<span class="rc-pill ok">' + ccIcon('shield') + esc(T.v_pill.replace('{0}', r.verified_on)) + '</span>'
                         : '<span class="rc-pill">' + ccIcon('file') + esc(T.v_none) + '</span>';
  }
  var vlogo = function (k, px) {
    return '<svg class="vlg" width="' + px + '" height="' + px + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><use href="'
      + esc(((window.OC && OC.vl) || '/static/vendor-logos.svg') + '#vl-' + (k || 'other')) + '"/></svg>';
  };
  var post = function (url, body) { return fetch(url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}).then(function (r) { return r.json(); }); };
  var store = function (k, v) { try { if (v === undefined) return localStorage.getItem('oc-cc-' + k); localStorage.setItem('oc-cc-' + k, v); } catch (e) { return null; } };
  var ic = function (n) { return '<svg class="ico s" aria-hidden="true"><use href="/static/icons.svg#i-' + n + '"/></svg>'; };
  var byId = {}; D.devs.forEach(function (d) { byId[d.id] = d; });
  function toast(t) { var el = $('cx-toast'); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(function () { el.hidden = true; }, 3200); }

  /* ---------- secciones ---------- */
  var tabs = Array.prototype.slice.call(document.querySelectorAll('#cc-tabs [data-tab]'));
  function show(k) {
    var secs = document.querySelectorAll('.cc-sec'), ok = false;
    Array.prototype.forEach.call(secs, function (s) { if (s.dataset.sec === k) ok = true; });
    if (!ok) k = 'sesiones';
    Array.prototype.forEach.call(secs, function (s) { s.hidden = s.dataset.sec !== k; });
    tabs.forEach(function (a) { var on = a.dataset.tab === k; a.classList.toggle('on', on); a.setAttribute('aria-selected', on ? 'true' : 'false'); });
    if (k === 'recetas') setTimeout(sizeCols, 0);
    history.replaceState(null, '', location.pathname + location.search.replace(/([?&])tab=[^&]*&?/, '$1').replace(/[?&]$/, '') + '#' + k);
  }
  tabs.forEach(function (a) { a.addEventListener('click', function (e) { e.preventDefault(); show(a.dataset.tab); }); });
  window.addEventListener('hashchange', function () { show(location.hash.slice(1)); });

  /* ---------- Sesiones ---------- */
  var open = []; try { open = JSON.parse(store('open') || '[]').filter(function (i) { return byId[i]; }); } catch (e) { open = []; }
  var active = +(store('active') || 0) || open[0] || null, layout = +(store('layout') || 1), favs = D.favs.slice(), states = {};
  function row(d) {
    var fav = favs.indexOf(d.id) >= 0;
    return '<div class="cc-dev" data-id="' + d.id + '"><button type="button" class="cc-star ' + (fav ? 'on' : '') + '" data-fav="' + d.id + '" aria-label="★" aria-pressed="' + fav + '">★</button>' +
      '<button type="button" class="cc-open" data-open="' + d.id + '">' + vlogo(d.vl, 24) + '<span class="grow"><b>' + esc(d.name) + '</b><small class="mono">' + esc(d.host) + '</small></span>' +
      '<span class="dot ' + (d.status === 'ok' ? 'ok' : d.status === 'error' ? 'crit' : '') + '"></span></button></div>';
  }
  function list() {
    var q = $('cc-find').value.trim().toLowerCase(), h = '';
    var m = function (d) { return !q || d.name.toLowerCase().indexOf(q) >= 0 || d.host.toLowerCase().indexOf(q) >= 0 || (d.label || '').toLowerCase().indexOf(q) >= 0; };
    if (q) { h = D.devs.filter(m).slice(0, 80).map(row).join(''); }
    else {
      var f = favs.map(function (i) { return byId[i]; }).filter(Boolean), r = D.recent.map(function (i) { return byId[i]; }).filter(Boolean);
      if (f.length) h += '<div class="lbl">★ ' + esc(T.fav) + '</div>' + f.map(row).join('');
      if (r.length) h += '<div class="lbl">' + esc(T.recent) + '</div>' + r.map(row).join('');
      h += '<div class="lbl">' + esc(T.bygroup) + '</div>';
      var gs = {}; D.devs.forEach(function (d) { (gs[d.group || T.nogroup] = gs[d.group || T.nogroup] || []).push(d); });
      Object.keys(gs).sort().forEach(function (g) { h += '<details class="cc-g"><summary>' + esc(g) + ' <span class="muted">' + gs[g].length + '</span></summary>' + gs[g].map(row).join('') + '</details>'; });
    }
    $('cc-list').innerHTML = h;
  }
  $('cc-find').addEventListener('input', list);
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && !$('t-sesiones').hidden) { e.preventDefault(); e.stopPropagation(); $('cc-find').focus(); }
  }, true);
  $('cc-list').addEventListener('click', function (e) {
    var f = e.target.closest('[data-fav]'), o = e.target.closest('[data-open]');
    if (f) {
      var id = +f.dataset.fav, on = favs.indexOf(id) < 0;
      post('/console/fav', {id: id, on: on}).then(function (j) { favs = j.favs || favs; list(); });
    } else if (o) openSession(+o.dataset.open);
  });
  function openSession(id) {
    if (open.indexOf(id) < 0) open.push(id);
    active = id; save(); paint();
  }
  function save() { store('open', JSON.stringify(open)); store('active', active || ''); store('layout', layout); }
  var frames = {};
  function frame(id) {
    if (!frames[id]) {
      var w = document.createElement('div'); w.className = 'cc-panel'; w.dataset.id = id;
      var d = byId[id];
      w.innerHTML = '<div class="cc-ph"><b class="grow">' + esc(d.name) + ' <span class="cc-rec">● ' + esc(T.rec) + '</span> <span class="cc-who muted sm"></span></b>' +
        '<button type="button" class="tbtn" data-pm="find" data-id="' + id + '" title="' + esc(T.find) + '" aria-label="' + esc(T.find) + '">' + ic('search') + '</button>' +
        '<button type="button" class="tbtn" data-pm="theme" data-id="' + id + '" title="' + esc(T.theme) + '" aria-label="' + esc(T.theme) + '">' + ic('moon') + '</button>' +
        '<button type="button" class="tbtn" data-share="' + id + '" title="' + esc(T.share) + '" aria-label="' + esc(T.share) + '">' + ic('users') + '</button><button type="button" class="btn s ghost" data-files="' + id + '">' + esc(T.files) + '</button>' +
        (D.ai ? '<button type="button" class="btn s ghost cx-aibtn" data-pm="ai" data-id="' + id + '">' + (D.ailogo || '✦ ') + esc(T.ai) + '</button>' : '') +
        '<button type="button" class="tbtn" data-pm="reconnect" data-id="' + id + '" title="' + esc(T.recon) + '" aria-label="' + esc(T.recon) + '">' + ic('refresh') + '</button>' +
        // tamaño, como el diálogo de la ficha: letra, ampliar en la ventana, ventana aparte y pantalla completa
        '<button type="button" class="tbtn txt" data-pm="fs-" data-id="' + id + '" title="' + esc(T.fsm) + '" aria-label="' + esc(T.fsm) + '">A−</button>' +
        '<button type="button" class="tbtn txt" data-pm="fs+" data-id="' + id + '" title="' + esc(T.fsp) + '" aria-label="' + esc(T.fsp) + '">A+</button>' +
        '<button type="button" class="btn s ghost" data-big="' + id + '" aria-pressed="false">' + ic('expand') + '<span>' + esc(T.big) + '</span></button>' +
        '<button type="button" class="tbtn" data-win="' + id + '" title="' + esc(T.win) + '" aria-label="' + esc(T.win) + '">' + ic('monitor') + '</button>' +
        '<button type="button" class="tbtn" data-full="' + id + '" title="' + esc(T.full) + '" aria-label="' + esc(T.full) + '">' + ic('expand') + '</button></div>' +
        '<iframe src="/devices/' + id + '/console?embed=2" title="' + esc(d.name) + '"></iframe>';
      frames[id] = w;
    }
    return frames[id];
  }
  function paint() {
    $('cc-stabs').innerHTML = open.map(function (id) {
      var d = byId[id];
      return '<span class="cc-st ' + (id === active ? 'on' : '') + '" role="tab" aria-selected="' + (id === active) + '"><button type="button" data-act="' + id + '">' + esc(d.name) + ' <span class="cc-rec">●</span></button><button type="button" data-x="' + id + '" aria-label="' + esc(T.close) + '">✕</button></span>';
    }).join('');
    var grid = $('cc-grid'); grid.dataset.n = layout;
    Array.prototype.forEach.call(document.querySelectorAll('#cc-layout button'), function (b) { b.classList.toggle('on', +b.dataset.n === layout); });
    var shown = [];
    if (active && open.indexOf(active) >= 0) shown.push(active);
    open.forEach(function (id) { if (shown.length < layout && shown.indexOf(id) < 0) shown.push(id); });
    Object.keys(frames).forEach(function (id) { if (open.indexOf(+id) < 0) { frames[id].remove(); delete frames[id]; } });
    $('cc-empty').hidden = shown.length > 0;
    Object.keys(frames).forEach(function (id) { frames[id].hidden = shown.indexOf(+id) < 0; });
    shown.forEach(function (id) { var f = frame(id); f.classList.toggle('on', id === active); if (!f.parentNode) grid.appendChild(f); f.hidden = false; });
    $('cc-open').textContent = open.length === 1 ? T.open1 : T.open.replace('{0}', open.length);
    document.body.classList.toggle('cc-big', !!document.querySelector('.cc-panel.big:not([hidden])'));
  }
  $('cc-stabs').addEventListener('click', function (e) {
    var a = e.target.closest('[data-act]'), x = e.target.closest('[data-x]');
    if (x) { var id = +x.dataset.x; open = open.filter(function (i) { return i !== id; }); if (active === id) active = open[0] || null; save(); paint(); }
    else if (a) { active = +a.dataset.act; save(); paint(); }
  });
  // «Ampliar»: la sesión ocupa toda la ventana del navegador (sin pantalla completa); Esc o el mismo botón la devuelven
  function big(w, on) {
    if (!w) return;
    Object.keys(frames).forEach(function (k) { if (frames[k] !== w) frames[k].classList.remove('big'); });
    w.classList.toggle('big', on); document.body.classList.toggle('cc-big', on);
    var b = w.querySelector('[data-big]'); b.setAttribute('aria-pressed', on ? 'true' : 'false'); b.lastChild.textContent = on ? T.small : T.big;
    setTimeout(function () { msgTo(+w.dataset.id, {cmd: 'fit'}); }, 60);
  }
  document.addEventListener('keydown', function (e) { var w = document.querySelector('.cc-panel.big'); if (e.key === 'Escape' && w) big(w, false); });
  $('cc-layout').addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) { layout = +b.dataset.n; save(); paint(); } });
  $('cc-grid').addEventListener('click', function (e) {
    var p = e.target.closest('.cc-panel'); if (p && +p.dataset.id !== active) { active = +p.dataset.id; save(); paint(); }
    var s = e.target.closest('[data-share]'), f = e.target.closest('[data-files]'), pm = e.target.closest('[data-pm]'), fu = e.target.closest('[data-full]');
    if (pm) msgTo(+pm.dataset.id, {cmd: pm.dataset.pm});
    if (fu && frames[fu.dataset.full].requestFullscreen) frames[fu.dataset.full].requestFullscreen();
    var bg = e.target.closest('[data-big]');
    if (bg) big(frames[bg.dataset.big], !frames[bg.dataset.big].classList.contains('big'));
    var wn = e.target.closest('[data-win]');
    if (wn) {  // ventana aparte: esta sesión se cierra aquí y se abre otra en la ventana
      var url = '/devices/' + wn.dataset.win + '/console', wid = +wn.dataset.win;
      open = open.filter(function (i) { return i !== wid; }); if (active === wid) active = open[0] || null; save(); paint();
      if (window.abrirVentana) window.abrirVentana(url, 'consola-' + wid, 1000, 640); else window.open(url);
    }
    if (s) msgTo(+s.dataset.share, {cmd: 'share'});
    if (f) files(+f.dataset.files, '/');
  });
  function msgTo(id, m) { var f = frames[id] && frames[id].querySelector('iframe'); if (f && f.contentWindow) { m.oc = 'console'; f.contentWindow.postMessage(m, location.origin); return true; } return false; }
  window.addEventListener('message', function (e) {
    if (e.origin !== location.origin || !e.data || e.data.oc !== 'console' || !e.data.dev) return;
    states[e.data.dev] = e.data;
  });

  /* ---------- archivos (SFTP) ---------- */
  var fdev = null, fpath = '/';
  function files(id, path) {
    fdev = id; fpath = path || '/';
    $('cc-files-d').textContent = byId[id].name; $('cc-files-p').textContent = fpath; $('cc-flist').innerHTML = '…'; $('cc-files-msg').textContent = '';
    var dl = $('cc-files'); if (!dl.open) dl.showModal();
    fetch('/console/files/' + id + '?path=' + encodeURIComponent(fpath), {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (j) {
      if (j.error) { $('cc-flist').innerHTML = '<p class="k-bad">' + esc(j.error) + '</p>'; return; }
      $('cc-flist').innerHTML = j.items.map(function (f) {
        var p = (fpath.replace(/\/$/, '') + '/' + f.n);
        return '<div class="row cc-f">' + (f.dir ? '<button type="button" class="linkish grow" data-dir="' + esc(p) + '">📁 ' + esc(f.n) + '</button>'
          : '<span class="grow mono">' + esc(f.n) + ' <small class="muted">' + Math.round(f.size / 1024) + ' KB</small></span><a class="btn s ghost" href="/console/files/' + id + '/get?path=' + encodeURIComponent(p) + '">↓</a>') + '</div>';
      }).join('') || '<p class="muted sm">—</p>';
    });
  }
  $('cc-flist').addEventListener('click', function (e) { var d = e.target.closest('[data-dir]'); if (d) files(fdev, d.dataset.dir); });
  $('cc-files-up').addEventListener('click', function () { files(fdev, fpath.replace(/\/[^\/]*\/?$/, '') || '/'); });
  $('cc-files-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var fd = new FormData(this);
    fetch('/console/files/' + fdev + '/put?path=' + encodeURIComponent(fpath), {method: 'POST', body: fd, credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (j) {
      $('cc-files-msg').textContent = j.error || j.path; if (!j.error) files(fdev, fpath);
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll('dialog [data-close]'), function (b) { b.addEventListener('click', function () { b.closest('dialog').close(); }); });

  /* ---------- Multi-equipo ---------- */
  var msel = {};
  function mlist() {
    var q = $('mu-find').value.trim().toLowerCase();
    $('mu-list').innerHTML = D.devs.filter(function (d) { return !q || (d.name + ' ' + d.host + ' ' + d.vendor).toLowerCase().indexOf(q) >= 0; }).map(function (d) {
      return '<label class="row nowrap sm"><input type="checkbox" data-m="' + d.id + '" ' + (msel[d.id] ? 'checked' : '') + '>' + vlogo(d.vl, 24) + '<span class="grow">' + esc(d.name) + '</span><small class="muted mono">' + esc(d.host) + '</small></label>';
    }).join('');
    var n = Object.keys(msel).length; $('mu-n').textContent = n === 1 ? T.sel1 : T.sel.replace('{0}', n); $('mu-run').textContent = $('mu-run').textContent.replace(/\s*\(\d+\)$/, '') + ' (' + n + ')';
  }
  $('mu-groups').innerHTML = D.groups.map(function (g) { return '<button type="button" class="chip" data-g="' + g.id + '">' + esc(g.name) + '</button>'; }).join('');
  $('mu-groups').addEventListener('click', function (e) {
    var b = e.target.closest('[data-g]'); if (!b) return;
    var gid = +b.dataset.g, all = D.devs.filter(function (d) { return d.gid === gid; }), on = !all.every(function (d) { return msel[d.id]; });
    all.forEach(function (d) { if (on) msel[d.id] = 1; else delete msel[d.id]; }); mlist();
  });
  $('mu-find').addEventListener('input', mlist);
  $('mu-list').addEventListener('change', function (e) { var c = e.target.closest('[data-m]'); if (c) { if (c.checked) msel[c.dataset.m] = 1; else delete msel[c.dataset.m]; mlist(); } });
  var lastMulti = null;
  function runMulti() {
    var ids = Object.keys(msel).map(Number), cmd = $('mu-cmd').value.trim();
    if (!ids.length) { toast(T.pickdev); return; }
    if (!cmd) return;
    $('mu-change').hidden = true; $('mu-res').hidden = false; $('mu-groups-out').innerHTML = '<p class="muted">' + esc(T.running) + '</p>'; $('mu-same').textContent = $('mu-diff').textContent = $('mu-meta').textContent = '';
    post('/console/multi', {device_ids: ids, command: cmd}).then(function (j) {
      if (j.change) { $('mu-res').hidden = true; $('mu-change').hidden = false; $('mu-change-t').textContent = j.msg; $('mu-ack').checked = false; $('mu-cr').disabled = true; muChange(true); return; }
      if (j.error) { $('mu-groups-out').innerHTML = '<p class="k-bad">' + esc(j.error) + '</p>'; return; }
      lastMulti = {cmd: cmd, res: j};
      $('mu-same').textContent = j.same === 1 ? T.same1 : T.same.replace('{0}', j.same);
      $('mu-diff').textContent = j.diff === 1 ? T.diff1 : T.diff.replace('{0}', j.diff);
      $('mu-meta').textContent = T.meta.replace('{0}', j.answered).replace('{1}', j.total).replace('{2}', (j.ms / 1000).toFixed(1));
      var base = j.groups[0] ? j.groups[0].out.split('\n') : [];
      $('mu-groups-out').innerHTML = j.groups.map(function (g, i) {
        var lines = g.out.split('\n').map(function (l) { return '<div class="' + (i && base.indexOf(l) < 0 ? 'cc-dl' : '') + '">' + (esc(l) || '&#8203;') + '</div>'; }).join('');
        return '<div class="cc-mg ' + (i ? 'diff' : 'same') + '"><div class="row sm"><b>' + esc(i ? T.distinct : T.equal) + ' · ' + g.devs.length + '</b><span class="muted">' + esc(g.devs.join(', ')) + '</span></div><div class="cx" data-theme="0" style="height:auto"><div class="cx-out mono" style="max-height:260px">' + lines + '</div></div></div>';
      }).join('') + (j.failed.length ? '<div class="cc-mg"><b class="sm">' + esc(T.failed) + '</b>' + j.failed.map(function (f) { return '<div class="sm"><b>' + esc(f.d) + '</b>: <span class="muted">' + esc(f.err) + '</span></div>'; }).join('') + '</div>' : '');
    });
  }
  function muChange(on) {  // comando que cambia la configuración: el botón principal es «Crear solicitud de cambio»
    var r = $('mu-run'); r.disabled = on; r.classList.toggle('pri', !on); r.title = on ? T.mu_change : '';
    $('mu-cr').classList.toggle('pri', on); $('mu-cr').classList.toggle('s', !on);
  }
  $('mu-cmd').addEventListener('input', function () { if (!$('mu-change').hidden) { $('mu-change').hidden = true; muChange(false); } });
  $('mu-run').addEventListener('click', runMulti);
  $('mu-cmd').addEventListener('keydown', function (e) { if (e.key === 'Enter') runMulti(); });
  $('mu-ack').addEventListener('change', function () { $('mu-cr').disabled = !this.checked; });
  $('mu-cr').addEventListener('click', function () {
    if (!$('mu-ack').checked) return;
    var ids = Object.keys(msel).map(Number);
    post('/console/change-request', {device_ids: ids, body: $('mu-cmd').value, title: $('mu-cmd').value.slice(0, 120)}).then(function (j) { if (j.url) location.href = j.url; else toast(j.error); });
  });
  if ($('mu-ai')) $('mu-ai').addEventListener('click', function () {
    if (!lastMulti || !window.ocAi) return;
    var txt = 'Comando: ' + lastMulti.cmd + '\n\n' + lastMulti.res.groups.map(function (g, i) { return (i ? 'Distintos' : 'Iguales') + ' (' + g.devs.join(', ') + '):\n' + g.out; }).join('\n\n');
    window.ocAi.open({kind: 'multi', text: txt.slice(0, 15000), ask: T.analyze});
  });

  /* ---------- Recetas: por equipo, por tarea y búsqueda agrupada (tres columnas del alto de la pantalla) ---------- */
  // el orden de las categorías (el JSON de la página las trae por orden alfabético)
  var CATK = ['sistema', 'seguridad', 'interfaces', 'servicios', 'diagnostico'].filter(function (k) { return D.cats[k]; }).concat(Object.keys(D.cats).filter(function (k) { return ['sistema', 'seguridad', 'interfaces', 'servicios', 'diagnostico'].indexOf(k) < 0; })), devN = {}; D.devs.forEach(function (d) { devN[d.os] = (devN[d.os] || 0) + 1; });
  var PL = {}, PLS = [];  // plataformas (familia de comandos): su nombre, marca, logo y recetas
  D.recipes.forEach(function (r) {
    var k = r.os || 'generic', p = PL[k];
    if (!p) { p = PL[k] = {os: k, label: D.oslabels[k] || r.os_label || T.generic, vendor: r.vendor || T.generic, vl: r.vl, recs: [], nV: 0, vc: {}}; PLS.push(p); }
    p.recs.push(r); if (r.verified_on) p.nV++; if (r.scope === 'opennccm') p.vc[r.vendor] = (p.vc[r.vendor] || 0) + 1;
  });
  PLS.forEach(function (p) {  // la marca de la plataforma: la de más recetas de OpenNCCM
    var best = Object.keys(p.vc).sort(function (a, b) { return p.vc[b] - p.vc[a]; })[0]; if (best) p.vendor = best;
    var own = p.recs.find(function (r) { return r.vendor === p.vendor; }); if (own) p.vl = own.vl;
  });
  // «Todas las marcas»: las marcas de los tipos de equipo, cada una con las plataformas de sus equipos que tienen recetas
  var BRS = (D.brands || []).map(function (b) {
    return {name: b.n, vl: b.vl, plats: b.os.map(function (o) { return PL[o]; }).filter(Boolean).sort(function (a, c) { return a.label.localeCompare(c.label); })};
  }).filter(function (b) { return b.plats.length; });
  var pKey = 'oc-rc-pins-' + (D.user || ''), pins = []; try { pins = JSON.parse(localStorage.getItem(pKey) || '[]'); } catch (e) { pins = []; }
  function yours() {
    return PLS.filter(function (p) { return devN[p.os] || pins.indexOf(p.os) >= 0; }).sort(function (a, b) {
      return (pins.indexOf(a.os) < 0) - (pins.indexOf(b.os) < 0) || (devN[b.os] || 0) - (devN[a.os] || 0) || a.label.localeCompare(b.label); });
  }
  var st = {view: 'eq', plat: null, rkey: null, task: null, onlyV: false, open: {}, bq: '', oq: '', more: false};
  (function () { var y = yours(), want = new URLSearchParams(location.search).get('rc'); st.plat = want && PL[want] ? want : y.length ? y[0].os : null; })();
  // la primera tarea con receta en tus equipos (o la más común)
  var TK = {}; D.recipes.forEach(function (r) { if (r.task && D.tasks[r.task]) (TK[r.task] = TK[r.task] || {}); if (r.task && TK[r.task]) TK[r.task][r.os] = TK[r.task][r.os] || r; });
  var nf = function (n, one, many) { return n === 1 ? one : many.replace('{0}', n); };
  var devTxt = function (n) { return nf(n, T.dev_1, T.dev_n); };
  function pillV(r, sm) { return r.verified_on ? '<span class="rc-pill ok' + (sm ? ' sm' : '') + '">' + ccIcon('shield') + esc(sm ? T.w_v : T.v_pill.replace('{0}', r.verified_on)) + '</span>' : '<span class="rc-pill' + (sm ? ' sm' : '') + '">' + esc(T.v_none) + '</span>'; }
  var chev = '<svg class="ico s rc-chev" aria-hidden="true"><use href="/static/icons.svg#i-right"/></svg>';
  function sizeCols() {  // las columnas llenan la pantalla y bajan cada una por su lado (en pantallas angostas, una debajo de otra)
    var c = $('rc-cols'); if (!c || c.offsetParent === null) return;
    if (window.innerWidth <= 1100) { c.style.height = ''; return; }
    var top = c.getBoundingClientRect().top + window.scrollY, hgt = Math.max(460, window.innerHeight - top - 10);
    c.style.height = hgt + 'px';
    var over = document.documentElement.scrollHeight - window.innerHeight;  // lo que agrega la página debajo (márgenes del contenido)
    if (over > 0 && hgt - over >= 460) c.style.height = (hgt - over) + 'px';
  }
  window.addEventListener('resize', sizeCols);
  function platRow(p, cls) {
    return '<button type="button" class="rc-pl' + (st.plat === p.os ? ' on' : '') + (cls ? ' ' + cls : '') + '" data-p="' + esc(p.os) + '">' + vlogo(p.vl, 28) +
      '<span class="rc-pt"><b>' + esc(p.label) + '</b><i>' + (devN[p.os] ? esc(devTxt(devN[p.os])) : (pins.indexOf(p.os) >= 0 ? esc(T.pinned) : esc(p.vendor))) + '</i></span><span class="rc-pn">' + p.recs.length + '</span></button>';
  }
  function left() {
    var h = '';
    if (st.view === 'eq') {
      var y = yours();
      if (y.length) h += '<div class="rc-lh">' + esc(T.yours) + ' <span>· ' + esc(nf(y.length, T.n_plat1, T.n_plats)) + '</span></div>' + y.map(function (p) { return platRow(p); }).join('');
      var q = st.bq.toLowerCase();
      h += '<div class="rc-lh' + (y.length ? ' gap' : '') + '">' + esc(T.brands) + ' <span>· ' + BRS.length + '</span></div>' +
        '<div class="input s rc-bq">' + ccIcon('search') + '<input id="rc-bq" type="search" value="' + esc(st.bq) + '" placeholder="' + esc(T.brand_q) + '" aria-label="' + esc(T.brand_q) + '"></div>';
      BRS.forEach(function (b) {
        var ps = q ? b.plats.filter(function (p) { return (b.name + ' ' + p.label).toLowerCase().indexOf(q) >= 0; }) : b.plats;
        if (!ps.length) return;
        var open = !!(q || st.open[b.name]);
        h += '<button type="button" class="rc-br' + (open ? ' open' : '') + '" data-b="' + esc(b.name) + '" aria-expanded="' + open + '">' + vlogo(b.vl, 22) + '<b>' + esc(b.name) + '</b><span class="muted grow">' + esc(nf(b.plats.length, T.n_plat1, T.n_plats)) + '</span>' +
          '<svg class="ico s rc-chev" aria-hidden="true"><use href="/static/icons.svg#i-' + (open ? 'down' : 'right') + '"/></svg></button>';
        if (open) h += '<div class="rc-sub">' + ps.map(function (p) {
          return '<button type="button" class="rc-sp' + (st.plat === p.os ? ' on' : '') + '" data-p="' + esc(p.os) + '"><span class="grow">' + esc(p.label) + '</span>' +
            (p.nV ? '<span class="rc-pill ok sm">' + ccIcon('shield') + p.nV + '</span>' : '') + '<span class="rc-pn">' + p.recs.length + '</span></button>';
        }).join('') + '</div>';
      });
    } else {
      h += '<div class="rc-lh">' + esc(T.what) + '</div>';
      CATK.forEach(function (c) {
        var ts = Object.keys(D.tasks).filter(function (t) { return D.tasks[t].c === c && TK[t]; });
        if (!ts.length) return;
        h += '<div class="rc-cg">' + esc(D.cats[c]) + '</div>' + ts.map(function (t) {
          return '<button type="button" class="rc-tk' + (st.task === t ? ' on' : '') + '" data-t="' + esc(t) + '"><span class="grow">' + esc(D.tasks[t].n) + '</span><span class="rc-pn">' + Object.keys(TK[t]).length + '</span></button>';
        }).join('');
      });
    }
    $('rc-left').innerHTML = h;
  }
  function recRow(r, sel) {
    return '<button type="button" class="rc-row' + (sel ? ' on' : '') + '" data-r="' + esc(r.key) + '"><span class="rc-rt">' + esc(r.short || r.name) + '</span>' +
      (r.scope !== 'opennccm' ? '<span class="muted sm">' + esc(scopeL(r)) + '</span>' : '') + '<span class="muted sm nowrap">' + esc(T.steps.replace('{0}', r.steps.length)) + '</span>' + pillV(r, true) + chev + '</button>';
  }
  function mid() {
    var h = '';
    if (st.view === 'eq') {
      var p = PL[st.plat];
      if (!p) { $('rc-mid').innerHTML = '<p class="muted rc-empty">' + esc(T.pick_plat) + '</p>'; return; }
      var pinned = pins.indexOf(p.os) >= 0;
      h += '<div class="rc-mh">' + vlogo(p.vl, 36) + '<div class="grow"><h2>' + esc(p.label) + '</h2><div class="muted sm">' + esc(T.n_recs.replace('{0}', p.recs.length)) + ' · ' + esc(T.n_ver.replace('{0}', p.nV)) +
        (devN[p.os] ? ' · ' + esc(devTxt(devN[p.os])) : '') + '</div></div><button type="button" class="btn s ghost' + (pinned ? ' on' : '') + '" id="rc-pin" aria-pressed="' + pinned + '" title="' + esc(T.pin_t) + '">' + ccIcon('star') + esc(pinned ? T.pinned : T.pin) + '</button></div>' +
        '<div class="rc-chips"><button type="button" class="chip' + (st.onlyV ? '' : ' on') + '" data-o="0">' + esc(T.all) + ' <span class="n">' + p.recs.length + '</span></button>' +
        '<button type="button" class="chip rc-only' + (st.onlyV ? ' on' : '') + '" data-o="1"' + (p.nV ? '' : ' disabled') + ' aria-pressed="' + st.onlyV + '">' + ccIcon('shield') + esc(T.v_only) + ' <span class="n">' + p.nV + '</span></button></div>';
      CATK.forEach(function (c) {
        var rs = p.recs.filter(function (r) { return r.cat === c && (!st.onlyV || r.verified_on); });
        if (rs.length) h += '<div class="rc-cg">' + esc(D.cats[c]) + ' <span>' + rs.length + '</span></div>' + rs.map(function (r) { return recRow(r, r.key === st.rkey); }).join('');
      });
    } else {
      var t = D.tasks[st.task];
      if (!t) { $('rc-mid').innerHTML = '<p class="muted rc-empty">' + esc(T.pick_rec) + '</p>'; return; }
      var byOs = TK[st.task], all = PLS.filter(function (p) { return byOs[p.os]; });
      var mine = all.filter(function (p) { return devN[p.os]; }).sort(function (a, b) { return devN[b.os] - devN[a.os]; }), rest = all.filter(function (p) { return !devN[p.os]; });
      h += '<div class="rc-mh"><div class="grow"><div class="muted sm">' + esc(D.cats[t.c] || '') + '</div><h2>' + esc(t.n) + '</h2><div class="muted sm">' + esc(t.i) + ' ' + esc(T.task_n.replace('{0}', all.length)) + '</div></div></div>';
      var prow = function (p) { var r = byOs[p.os]; return '<button type="button" class="rc-row' + (r.key === st.rkey ? ' on' : '') + '" data-r="' + esc(r.key) + '">' + vlogo(p.vl, 24) +
        '<span class="rc-rt">' + esc(p.label) + '<i>' + (devN[p.os] ? esc(devTxt(devN[p.os])) : esc(p.vendor)) + '</i></span>' + pillV(r, true) + chev + '</button>'; };
      h += '<div class="rc-cg">' + esc(T.in_yours) + ' <span>' + mine.length + '</span></div>' + (mine.length ? mine.map(prow).join('') : '<p class="muted sm rc-empty">' + esc(T.no_yours) + '</p>');
      var oq = st.oq.toLowerCase(), shown = oq ? rest.filter(function (p) { return (p.label + ' ' + p.vendor).toLowerCase().indexOf(oq) >= 0; }) : [];
      h += '<div class="rc-cg gap">' + esc(T.others) + ' <span>' + rest.length + '</span></div><div class="input s rc-bq">' + ccIcon('search') + '<input id="rc-oq" type="search" value="' + esc(st.oq) + '" placeholder="' + esc(T.plat_q) + '" aria-label="' + esc(T.plat_q) + '"></div>' +
        (oq ? shown.map(prow).join('') || '<p class="muted sm rc-empty">' + esc(T.no_res.replace('{0}', st.oq)) + '</p>' : '<p class="muted sm rc-empty">' + esc(T.others_hint.replace('{0}', rest.length)) + '</p>');
    }
    $('rc-mid').innerHTML = h;
  }
  function hl(s, q) {  // resalta todas las veces que aparece lo buscado
    var i, out = '', low = s.toLowerCase(), from = 0;
    while (q && (i = low.indexOf(q, from)) >= 0) { out += esc(s.slice(from, i)) + '<mark>' + esc(s.slice(i, i + q.length)) + '</mark>'; from = i + q.length; }
    return out + esc(s.slice(from));
  }
  function cmdsOf(r) { return r.steps.map(function (s) { return s.c; }).join('\n'); }
  function why(r, q) {  // «en los comandos: …ip service set ssh address=…» cuando la palabra no está en el nombre
    if ((r.short || r.name).toLowerCase().indexOf(q) >= 0) return '';
    var src = [[r.intro, T.in_desc], [cmdsOf(r), T.in_cmds]].find(function (x) { return x[0].toLowerCase().indexOf(q) >= 0; });
    if (!src) return '';
    var t = src[0].replace(/\s+/g, ' '), i = t.toLowerCase().indexOf(q), a = Math.max(0, i - 28), b = Math.min(t.length, i + q.length + 32);
    return '<i class="rc-why">' + esc(src[1]) + ' ' + (a ? '…' : '') + hl(t.slice(a, b), q) + (b < t.length ? '…' : '') + '</i>';
  }
  function results() {
    var raw = $('rc-q').value.trim(), q = raw.toLowerCase();
    var groups = PLS.map(function (p) {
      return {p: p, rs: p.recs.filter(function (r) { return ((r.short || r.name) + ' ' + r.intro + ' ' + p.label + ' ' + p.vendor + ' ' + cmdsOf(r)).toLowerCase().indexOf(q) >= 0; })
        .sort(function (a, b) { return ((b.short || b.name).toLowerCase().indexOf(q) >= 0) - ((a.short || a.name).toLowerCase().indexOf(q) >= 0); })};
    }).filter(function (g) { return g.rs.length; });
    var mine = groups.filter(function (g) { return devN[g.p.os] || pins.indexOf(g.p.os) >= 0; }).sort(function (a, b) { return (devN[b.p.os] || 0) - (devN[a.p.os] || 0); });
    var rest = groups.filter(function (g) { return mine.indexOf(g) < 0; });
    var n = groups.reduce(function (s, g) { return s + g.rs.length; }, 0);
    var grp = function (g) {
      return '<div class="rc-sg"><div class="rc-sgh">' + vlogo(g.p.vl, 22) + '<b>' + esc(g.p.label) + '</b>' + (devN[g.p.os] ? '<span class="muted sm">' + esc(T.yours_tag) + '</span>' : '<span class="muted sm">' + esc(g.p.vendor) + '</span>') + '</div>' +
        g.rs.slice(0, 2).map(function (r) { return '<button type="button" class="rc-row" data-r="' + esc(r.key) + '"><span class="rc-rt">' + hl(r.short || r.name, q) + why(r, q) + '</span>' + pillV(r, true) + chev + '</button>'; }).join('') +
        (g.rs.length > 2 ? '<button type="button" class="linkbtn rc-more" data-p="' + esc(g.p.os) + '">' + esc(T.more.replace('{0}', g.rs.length - 2)) + '</button>' : '') + '</div>';
    };
    var h = !n ? '<p class="muted rc-empty">' + esc(T.no_res.replace('{0}', raw)) + '</p>'
      : '<div class="rc-rh"><b>' + esc(T.res.replace('{0}', n).replace('{1}', raw).replace('{2}', groups.length)) + '</b>' + (mine.length && rest.length ? ' · ' + esc(T.res_first) : '') + '</div>';
    if (n) {
      if (mine.length) {
        h += mine.map(grp).join('');
        if (rest.length) h += '<div class="rc-sg"><button type="button" class="rc-sgh rc-toggle" id="rc-more-all" aria-expanded="' + st.more + '"><b>' + esc(T.other_plats.replace('{0}', rest.length).replace('{1}', rest.reduce(function (s, g) { return s + g.rs.length; }, 0))) + '</b><span class="grow"></span><span class="muted sm">' + esc(st.more ? T.hide_more : T.show_more) + '</span>' +
          '<svg class="ico s" aria-hidden="true"><use href="/static/icons.svg#i-' + (st.more ? 'down' : 'right') + '"/></svg></button></div>' + (st.more ? rest.map(grp).join('') : '');
      } else h += rest.map(grp).join('');
    }
    $('rc-results').innerHTML = h;
  }
  function render() {
    var searching = !!$('rc-q').value.trim();
    Array.prototype.forEach.call(document.querySelectorAll('#rc-view [data-v]'), function (b) { var on = b.dataset.v === st.view; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); });
    ['rc-left', 'rc-mid', 'rc-detail'].forEach(function (id) { $(id).hidden = searching; }); $('rc-results').hidden = !searching;
    if (searching) results(); else { left(); mid(); if (!cur || cur.key !== st.rkey) detailEmpty(); }
    sizeCols();
  }
  function detailEmpty() { if (!st.rkey) { cur = null; $('rc-detail').innerHTML = '<p class="muted rc-empty">' + esc(T.pick_rec) + '</p>'; } else detail(st.rkey); }
  function pickRec(key) { st.rkey = key; detail(key); left(); mid(); post('/console/recipes/used', {key: key}); }
  $('rc-view').addEventListener('click', function (e) {
    var b = e.target.closest('[data-v]'); if (!b) return; st.view = b.dataset.v;
    if (st.view === 'tk' && !st.task) { var ts = Object.keys(TK).sort(function (a, c) { return Object.keys(TK[c]).length - Object.keys(TK[a]).length; }); st.task = ts.indexOf('syslog') >= 0 ? 'syslog' : ts[0]; }
    $('rc-q').value = ''; render(); $('rc-left').scrollTop = 0; $('rc-mid').scrollTop = 0;
  });
  $('rc-q').addEventListener('input', function () { st.more = false; render(); });
  $('rc-left').addEventListener('click', function (e) {
    var p = e.target.closest('[data-p]'), b = e.target.closest('[data-b]'), t = e.target.closest('[data-t]');
    if (p) { st.plat = p.dataset.p; st.onlyV = false; st.rkey = null; cur = null; render(); $('rc-mid').scrollTop = 0; }
    else if (b) { st.open[b.dataset.b] = !st.open[b.dataset.b]; left(); }
    else if (t) { st.task = t.dataset.t; st.oq = ''; var y = TK[st.task], first = PLS.filter(function (x) { return y[x.os] && devN[x.os]; }).sort(function (a, c) { return devN[c.os] - devN[a.os]; })[0]; st.rkey = first ? y[first.os].key : null; cur = null; render(); }
  });
  $('rc-left').addEventListener('input', function (e) { if (e.target.id === 'rc-bq') { st.bq = e.target.value; left(); var i = $('rc-bq'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); } });
  $('rc-mid').addEventListener('click', function (e) {
    var r = e.target.closest('[data-r]'), o = e.target.closest('[data-o]');
    if (r) pickRec(r.dataset.r);
    else if (o) { st.onlyV = o.dataset.o === '1'; mid(); }
    else if (e.target.closest('#rc-pin')) {
      var i = pins.indexOf(st.plat); if (i >= 0) pins.splice(i, 1); else pins.unshift(st.plat);
      try { localStorage.setItem(pKey, JSON.stringify(pins)); } catch (er) {}
      left(); mid();
    }
  });
  $('rc-mid').addEventListener('input', function (e) { if (e.target.id === 'rc-oq') { st.oq = e.target.value; mid(); var i = $('rc-oq'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); } });
  $('rc-results').addEventListener('click', function (e) {
    var r = e.target.closest('[data-r]'), m = e.target.closest('.rc-more');
    if (e.target.closest('#rc-more-all')) { st.more = !st.more; results(); return; }
    if (!r && !m) return;
    var key = r ? r.dataset.r : null, rec = key && D.recipes.find(function (x) { return x.key === key; });
    st.view = 'eq'; st.plat = rec ? (rec.os || 'generic') : m.dataset.p; st.onlyV = false; st.rkey = key; $('rc-q').value = ''; cur = null; render();
    if (key) post('/console/recipes/used', {key: key});
  });
  // «Revisa cada receta…»: una línea arriba; «Ver el aviso» abre el aviso completo con el foro y GitHub
  function ghUrl(r) {
    if (!r) return D.issues;
    var seal = r.verified_on ? T.v_box_t.replace('{0}', r.verified_on) : T.v_none;
    var L = T.gh_l, body = [L[0].replace('{0}', r.name).replace('{1}', r.key), L[1].replace('{0}', r.os_label || r.vendor || ''), L[2].replace('{0}', seal),
      L[3].replace('{0}', D.version || ''), '', L[4], '', L[5], ''].join('\n');
    return D.issues + '?title=' + encodeURIComponent(T.gh_title.replace('{0}', r.name)) + '&body=' + encodeURIComponent(body);
  }
  function reportLinks(r, small) {
    var ext = '<svg class="ico s rc-ext" aria-hidden="true"><use href="/static/icons.svg#i-ext"/></svg>';
    function one(cls, href, ico, t, s, title) {
      return '<a class="rc-rep ' + cls + '" href="' + esc(href) + '" target="_blank" rel="noopener"' + (title ? ' title="' + esc(title) + '"' : '') + '>' +
        '<svg class="ico" aria-hidden="true"><use href="/static/icons.svg#i-' + ico + '"/></svg><span class="grow"><b>' + esc(t) + '</b>' + (small ? '' : '<small>' + esc(s) + '</small>') + '</span>' + ext + '</a>';
    }
    return '<div class="rc-reps' + (small ? ' sm' : '') + '">' + (D.forum ? one('forum', D.forum, 'chat2', T.f_t, T.f_s, r ? r.name : '') : '') +
      (D.issues ? one('gh', ghUrl(r), 'issue', T.gh_t, T.gh_s, '') : '') + '</div>';
  }
  function scopeL(r) { return r.scope === 'mine' ? T.mine : r.scope === 'team' ? T.team : T.opennccm; }
  var cur = null;
  function vals() { var o = {}; Array.prototype.forEach.call(document.querySelectorAll('#rc-detail [data-var]'), function (i) { o[i.dataset.var] = i.value; }); return o; }
  function fill(c, o) { return c.replace(/\{\{(\w+)\}\}/g, function (m, n) { return o[n] != null ? o[n] : m; }); }
  function riskOf(r, c) {  // lo mismo que consolecore, simplificado: ¿solo consulta?
    var l = c.trim();
    if (r.os === 'ros') return /(^|\s)(print|monitor|monitor-traffic|export|ping|traceroute|torch|getall)(\s|$)|^:put\b/.test(l) ? 'read' : 'change';
    return /^(show|sh |display|ping|traceroute|tracert|get |dump )/i.test(l) ? 'read' : 'change';
  }
  function detail(key) {
    var r = D.recipes.find(function (x) { return x.key === key; }); if (!r) return; cur = r;
    var el = $('rc-detail'), p = PL[r.os || 'generic'];
    el.innerHTML = '<div class="rc-dh">' + vlogo(p ? p.vl : r.vl, 36) + '<div class="grow"><div class="muted sm">' + esc(p ? p.label : r.os_label || '') + ' · ' + esc(D.cats[r.cat] || '') + (r.scope !== 'opennccm' ? ' · ' + esc(scopeL(r)) : '') + '</div>' +
      '<h3>' + esc(r.short || r.name) + '</h3></div></div><div>' + pillV(r) + '</div>' +
      (window.ocWarn ? window.ocWarn({title: T.w_title, gh: D.issues ? ghUrl(r) : '', where: r.name}) : '') +
      '<p class="muted sm rc-intro">' + esc(r.intro) + (r.doc ? ' <a href="' + esc(r.doc) + '" target="_blank" rel="noopener">' + esc(T.doc) + '</a>' : '') + '</p>' +
      (D.role === 'admin' ? '<div><button type="button" class="btn s ghost" id="rc-verify">' + ccIcon('shield') + esc(r.verified_on ? T.v_unset : T.v_set) + '</button></div>' : '') +
      (r.vars.length ? '<div class="rc-vars">' + r.vars.map(function (v) { return '<div class="field"><label>' + esc(v.l) + '</label><div class="input mono s"><input data-var="' + esc(v.n) + '" value="' + esc(v.v) + '" aria-label="' + esc(v.l) + '"></div></div>'; }).join('') + '</div>' : '') +
      '<ol class="cc-steps" id="rc-steps"></ol>' +
      '<div class="rc-acts"><button type="button" class="btn pri" id="rc-run"></button><button type="button" class="btn" id="rc-multi">' + esc(T.multi) + '</button>' +
      (D.role !== 'viewer' ? (r.mine ? '<button type="button" class="btn ghost" id="rc-edit-b">' + esc(T.edit) + '</button><button type="button" class="btn ghost dz" id="rc-del">' + esc(T.del) + '</button>' : '<button type="button" class="btn ghost" id="rc-dup">' + esc(T.dup) + '</button>') : '') + '</div>';
    steps();
    if ($('rc-verify')) $('rc-verify').addEventListener('click', function () {
      var v = r.verified_on ? '' : prompt(T.v_ask, r.os_label ? r.os_label.split(' ')[0] + ' ' : '');
      if (v === null) return;
      post('/console/recipes/verify', {key: r.key, verified_on: v}).then(function (j) {
        if (!j.ok) { toast(j.error); return; }
        var was = !!r.verified_on; r.verified_on = j.verified_on; if (p) p.nV += (r.verified_on ? 1 : 0) - (was ? 1 : 0); detail(r.key); mid();
      });
    });
    Array.prototype.forEach.call(el.querySelectorAll('[data-var]'), function (i) { i.addEventListener('input', steps); });
    var onlyRead = r.steps.every(function (s) { return s.c.split('\n').every(function (l) { return !l.trim() || riskOf(r, l) === 'read'; }); });
    $('rc-run').textContent = onlyRead ? T.runact : T.apply;
    $('rc-run').addEventListener('click', function () { onlyRead ? runActive(r) : applyDlg(r); });
    $('rc-multi').addEventListener('click', function () {
      var o = vals(), first = r.steps.map(function (s) { return fill(s.c, o).split('\n')[0]; }).find(function (c) { return riskOf(r, c) === 'read'; }) || '';
      $('mu-cmd').value = first; D.devs.forEach(function (d) { if (d.os === r.os) msel[d.id] = 1; }); mlist(); show('multi');
    });
    if ($('rc-dup')) $('rc-dup').addEventListener('click', function () { editDlg(r, true); });
    if ($('rc-edit-b')) $('rc-edit-b').addEventListener('click', function () { editDlg(r, false); });
    if ($('rc-del')) $('rc-del').addEventListener('click', function () { post('/console/recipes/' + r.id + '/delete', {}).then(function () { location.hash = 'recetas'; location.reload(); }); });
    el.scrollTop = 0;
  }
  function steps() {
    var r = cur, o = vals();
    $('rc-steps').innerHTML = r.steps.map(function (s, i) {
      var c = fill(s.c, o);
      return '<li class="' + (s.warn ? 'warn' : '') + (s.check ? ' check' : '') + '"><div class="sm">' + (s.warn ? '<b class="k-warn">' + esc(T.warn) + ' · </b>' : s.check && s.t.indexOf(T.check) !== 0 ? '<b class="k-ok">' + esc(T.check) + ' · </b>' : '') + esc(s.t) + '</div>' +
        (c ? '<div class="cx" data-theme="0" style="height:auto"><div class="cx-out mono" style="padding:8px 10px">' + esc(c) + '</div></div><div class="row"><button type="button" class="btn s ghost" data-cp="' + i + '">' + esc(T.copy) + '</button><button type="button" class="btn s ghost" data-in="' + i + '">' + esc(T.insert) + '</button></div>' : '') + '</li>';
    }).join('');
  }
  $('rc-detail').addEventListener('click', function (e) {
    var c = e.target.closest('[data-cp]'), n = e.target.closest('[data-in]');
    var o = vals();
    if (c) navigator.clipboard.writeText(fill(cur.steps[+c.dataset.cp].c, o)).then(function () { toast(T.copied); });
    if (n) { if (!active || !msgTo(active, {cmd: 'insert', text: fill(cur.steps[+n.dataset.in].c, o).split('\n')[0]})) toast(T.noact); else { show('sesiones'); } }
  });
  render();
  function runActive(r) {
    if (!active) { toast(T.noact); return; }
    var o = vals(), all = r.steps.map(function (s) { return fill(s.c, o); }).join('\n');
    msgTo(active, {cmd: 'run', text: all}); toast(T.sent); show('sesiones');
  }
  // versión del equipo comparada con la del sello («VOSS 9.3.0.0» y «9.3.0.0» son la misma)
  function verNum(s) { var m = String(s || '').match(/\d+(?:\.\d+)+/); return m ? m[0] : String(s || '').trim(); }
  // el sello puede nombrar varias versiones probadas («RouterOS 6.49.23 / 7.24.5»): basta con que el equipo tenga una
  function sameVer(dev, seal) { var vs = String(seal || '').match(/\d+(?:\.\d+)+/g) || [String(seal || '').trim()]; return vs.indexOf(verNum(dev)) >= 0; }
  function applyCheck() {
    var r = cur, dl = $('rc-apply'), sel = Array.prototype.filter.call(dl.querySelectorAll('#rc-apply-devs input:checked'), function () { return true; });
    var devs = sel.map(function (i) { return D.devs.find(function (d) { return d.id === +i.value; }); }).filter(Boolean);
    $('rc-ack-t').textContent = T.ack.replace('{0}', devs.length === 1 ? devs[0].name : T.ack_sel);
    var diff = r.verified_on ? devs.filter(function (d) { return d.ver && !sameVer(d.ver, r.verified_on); }) : [];
    $('rc-apply-ver').innerHTML = diff.map(function (d) {
      var txt = esc(T.v_diff).replace('{0}', '<b>' + esc(d.name) + '</b>').replace('{1}', esc(r.verified_on))
        .replace('{2}', esc((r.os_label ? r.os_label.split(' ')[0] + ' ' : '') + d.ver));
      return '<div class="banner warn">' + ccIcon('alert') + '<span class="grow">' + txt + '</span></div>';
    }).join('');
    $('rc-apply-go').disabled = !$('rc-ack').checked || !devs.length;
  }
  $('rc-apply').addEventListener('change', applyCheck);
  function applyDlg(r) {
    var dl = $('rc-apply');
    $('rc-apply-devs').innerHTML = D.devs.filter(function (d) { return d.os === r.os; }).map(function (d) { return '<label class="row nowrap sm"><input type="checkbox" value="' + d.id + '">' + vlogo(d.vl, 24) + '<span class="grow">' + esc(d.name) + '</span><small class="muted mono">' + esc(d.host) + '</small></label>'; }).join('') || '<p class="muted">—</p>';
    $('rc-ack').checked = false; applyCheck();
    dl.showModal();
  }
  $('rc-apply-f').addEventListener('submit', function (e) {
    e.preventDefault();
    var ids = Array.prototype.map.call(this.querySelectorAll('#rc-apply-devs input:checked'), function (i) { return +i.value; }), o = vals(), r = cur;
    if (!ids.length) { toast(T.pickdev); return; }
    if (!$('rc-ack').checked) return;
    var body = r.steps.filter(function (s) { return !s.check; }).map(function (s) { return fill(s.c, o).split('\n').filter(function (l) { return riskOf(r, l) !== 'read'; }).join('\n'); }).filter(Boolean).join('\n');
    post('/console/change-request', {device_ids: ids, body: body, title: r.name}).then(function (j) { if (j.url) location.href = j.url; else toast(j.error); });
  });
  function editDlg(r, dup) {
    var dl = $('rc-edit'); dl.dataset.id = dup || !r ? '' : r.id;
    $('rf-name').value = r ? r.name : ''; $('rf-vendor').value = r ? r.vendor : ''; $('rf-os').value = r ? r.os : 'ios'; $('rf-cat').value = r ? r.cat : 'diagnostico';
    $('rf-osl').value = r ? (r.os_label || '') : ''; $('rf-intro').value = r ? r.intro : '';
    $('rf-vars').value = r ? r.vars.map(function (v) { return v.n + ' = ' + v.l + ' = ' + v.v; }).join('\n') : '';
    $('rf-steps').value = r ? r.steps.map(function (s) { return (s.warn ? '! ' : s.check ? '? ' : '') + s.t + (s.c ? '\n' + s.c : ''); }).join('\n\n') : '';
    dl.showModal();
  }
  if ($('rc-new')) $('rc-new').addEventListener('click', function () { editDlg(null, true); });
  $('rc-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var vars = $('rf-vars').value.split('\n').filter(function (l) { return l.trim(); }).map(function (l) { var p = l.split('='); return {n: (p[0] || '').trim(), l: (p[1] || p[0] || '').trim(), v: (p.slice(2).join('=') || '').trim()}; });
    var stepsL = $('rf-steps').value.split(/\n\s*\n/).filter(function (b) { return b.trim(); }).map(function (b) {
      var ls = b.split('\n'), t = ls[0].trim(), warn = /^!\s/.test(t), check = /^\?\s/.test(t);
      return {t: t.replace(/^[!?]\s/, ''), c: ls.slice(1).join('\n').trim(), warn: warn, check: check};
    });
    post('/console/recipes', {id: +this.closest('dialog').dataset.id || 0, name: $('rf-name').value, vendor: $('rf-vendor').value, os: $('rf-os').value, cat: $('rf-cat').value,
      os_label: $('rf-osl').value, intro: $('rf-intro').value, vars: vars, steps: stepsL}).then(function (j) { if (j.ok) { toast(T.saved); location.hash = 'recetas'; location.reload(); } else toast(j.error); });
  });

  /* ---------- Grabaciones ---------- */
  var ev = [], playing = false, speed = 1, pos = 0, dur = 1, timer = null;
  function openRec(id) {
    fetch('/console/rec/' + id, {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (j) {
      ev = j.events || []; dur = ev.length ? Math.max(1, ev[ev.length - 1][0]) : 1; pos = dur;  // se abre entera; ▶ la reproduce desde el principio
      var dz = j.danger || [];
      $('rp-cmds').innerHTML = ev.filter(function (e) { return e[1] === 'i'; }).map(function (e) {
        return '<li><button type="button" class="' + (dz.indexOf(e[0]) >= 0 ? 'dz' : '') + '" data-t="' + e[0] + '"><span class="mono muted">' + fmt(e[0]) + '</span> <span class="mono">' + esc(e[2]) + '</span></button></li>';
      }).join('') || '<li class="muted sm">—</li>';
      $('rec-player').hidden = false; $('rp-t').textContent = j.u + ' · ' + j.d;
      $('rp-sub').textContent = (j.when || '').replace('T', ' ').slice(0, 16) + (j.ip ? ' · ' + j.ip : '') + ' · ' + T.played.replace('{0}', ev.filter(function (e) { return e[1] === 'i'; }).length).replace('{1}', fmt(dur));
      $('rp-export').href = '/console/rec/' + id + '/export';
      $('rp-marks').innerHTML = (j.danger || []).map(function (t) { return '<span style="left:' + (100 * t / dur) + '%"></span>'; }).join('');
      $('rp-pos').max = dur; draw();
      $('rec-player').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    });
  }
  function colorize(h) {  // los mismos colores que la consola: prompt y comando, IP, activo, caído, deshabilitado, errores
    return h.split('\n').map(function (l) {
      var m = l.match(/^([\w.\-\/@()\[\]&;<>]{1,60}?[#>$])(.*)$/);
      if (m && !/\s{3,}/.test(m[1])) return '<span class="k-host">' + m[1] + '</span><span class="k-path">' + m[2] + '</span>';
      if (/^\s*(%|bad command|syntax error|Error:|failure:)/i.test(l)) return '<span class="k-bad">' + l + '</span>';
      if (/^(Interface|Port|Device ID|Flags:|Columns:)\s/.test(l)) return '<span class="k-head">' + l + '</span>';
      return l.replace(/\b(\d{1,3}(?:\.\d{1,3}){3}(?:\/\d{1,2})?)\b/g, '<span class="k-ip">$1</span>')
        .replace(/\b(administratively down|disabled|notconnect)\b/g, '<span class="k-warn">$1</span>')
        .replace(/\b(up|connected|running|enabled|YES)\b/g, '<span class="k-ok">$1</span>')
        .replace(/\b(down|errors?|CRC|failed|invalid)\b/g, '<span class="k-bad">$1</span>');
    }).join('\n');
  }
  function fmt(s) { s = Math.round(s); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
  function draw() {
    var html = '';
    ev.forEach(function (e) {
      if (e[0] > pos) return;
      if (e[1] === 'n') html += '\n<span class="k-note">' + esc(e[2]) + '</span>\n';
      else if (e[1] === 'o') html += colorize(esc(e[2]));
    });
    $('rp-out').innerHTML = html; $('rp-out').scrollTop = $('rp-out').scrollHeight;
    Array.prototype.forEach.call(document.querySelectorAll('#rp-cmds button'), function (b) { b.classList.toggle('on', +b.dataset.t <= pos); });
    $('rp-pos').value = pos; $('rp-time').textContent = fmt(pos) + ' / ' + fmt(dur);
  }
  function tick() { pos = Math.min(dur, pos + 0.25 * speed); draw(); if (pos >= dur) stop(); }
  function stop() { playing = false; clearInterval(timer); $('rp-play').textContent = '▶'; }
  $('rp-cmds').addEventListener('click', function (e) { var b = e.target.closest('[data-t]'); if (b) { stop(); pos = +b.dataset.t + 0.05; draw(); } });
  $('rp-play').addEventListener('click', function () { if (playing) { stop(); return; } if (pos >= dur) pos = 0; playing = true; this.textContent = '❚❚'; timer = setInterval(tick, 250); });
  $('rp-pos').addEventListener('input', function () { pos = +this.value; draw(); });
  $('rp-speed').addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return; speed = +b.dataset.s; Array.prototype.forEach.call(this.children, function (c) { c.classList.toggle('on', c === b); }); });
  $('rec-table').addEventListener('click', function (e) { var r = e.target.closest('[data-rec]'); if (r) openRec(+r.dataset.rec); });
  $('rec-table').addEventListener('keydown', function (e) { var r = e.target.closest('[data-rec]'); if (r && e.key === 'Enter') openRec(+r.dataset.rec); });

  /* ---------- Herramientas ---------- */
  var tool = 'ping';
  $('tl-tool').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return; tool = b.dataset.t;
    Array.prototype.forEach.call(this.children, function (c) { c.classList.toggle('on', c === b); });
    $('tl-port-w').hidden = tool !== 'tcp';
    Array.prototype.forEach.call(document.querySelectorAll('.tl-help'), function (h) { h.hidden = h.dataset.for !== tool; });
  });
  function toolColors(s) {
    return esc(s).replace(/(\b\d{1,3}(?:\.\d{1,3}){3}\b)/g, '<span class="k-ip">$1</span>').replace(/(\d+(?:\.\d+)?\s?ms)/g, '<span class="k-val">$1</span>')
      .replace(/\b(abierto|open|0% packet loss|0% perdidos)\b/gi, '<span class="k-ok">$1</span>').replace(/(\d+% (?:packet )?loss|no responde|timed out|\* \* \*|Unreachable)/gi, '<span class="k-bad">$1</span>');
  }
  var toolRun = null;
  function toolBusy(on) { $('tl-run').hidden = on; $('tl-stop').hidden = !on; }
  function runTool() {  // la salida llega por partes; tras «\x1e» viene el resumen en JSON
    var t = $('tl-target').value.trim(); if (!t || toolRun) return;
    var run = toolRun = Date.now().toString(36) + Math.random().toString(36).slice(2, 6), out = $('tl-out'), buf = '';
    out.textContent = T.running; $('tl-sum').innerHTML = ''; $('tl-open').hidden = true; toolBusy(true);
    var done = function () { if (toolRun === run) { toolRun = null; toolBusy(false); } };
    fetch('/console/tools', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({tool: tool, target: t, port: $('tl-port').value, run: run})}).then(function (r) {
      if (!r.ok || (r.headers.get('content-type') || '').indexOf('json') >= 0) return r.json().then(function (j) { out.textContent = j.error || ('HTTP ' + r.status); done(); });
      var rd = r.body.getReader(), dec = new TextDecoder();
      function step() {
        return rd.read().then(function (x) {
          if (x.done) { done(); return; }
          buf += dec.decode(x.value, {stream: true});
          var cut = buf.indexOf('\x1e'), text = cut >= 0 ? buf.slice(0, cut) : buf;
          var stick = out.scrollTop + out.clientHeight >= out.scrollHeight - 30;
          out.innerHTML = toolColors(text); if (stick) out.scrollTop = out.scrollHeight;
          if (cut >= 0) {
            try {
              var j = JSON.parse(buf.slice(cut + 1));
              $('tl-sum').innerHTML = j.summary.map(function (s) { return '<dt>' + esc(s[0]) + '</dt><dd>' + esc(s[1]) + '</dd>'; }).join('');
              if (j.device) { var b = $('tl-open'); b.hidden = false; b.textContent = T.openc.replace('{0}', j.device.name); b.onclick = function () { openSession(j.device.id); show('sesiones'); }; }
            } catch (e) { /* aún no ha llegado entero */ }
          }
          return step();
        });
      }
      return step();
    }).catch(function (e) { out.textContent = String(e); done(); });
  }
  $('tl-stop').addEventListener('click', function () { if (toolRun) post('/console/tools/stop', {run: toolRun}).then(function () { toast(T.stopped); }); });
  $('tl-run').addEventListener('click', runTool);
  $('tl-target').addEventListener('keydown', function (e) { if (e.key === 'Enter') runTool(); });

  /* ---------- Acceso: los selectores por rol ---------- */
  Array.prototype.forEach.call(document.querySelectorAll('#t-acceso .seg'), function (s) {
    s.addEventListener('change', function () { Array.prototype.forEach.call(s.querySelectorAll('label'), function (l) { l.classList.toggle('on', l.querySelector('input').checked); }); });
  });

  list(); paint(); mlist();
  show(location.hash.slice(1) || D.tab);
  if (D.rec) { show('grabaciones'); openRec(D.rec); }
})();
