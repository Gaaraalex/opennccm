/* Operar › Consola (app/templates/console_center.html): sesiones (lanzador, pestañas, 1/2/4, compartir, SFTP),
   multi-equipo, recetas, grabaciones (reproductor), herramientas y acceso. Las secciones están en la misma página
   para que las consolas abiertas no se cierren al cambiar de sección. */
(function () {
  'use strict';
  var D = JSON.parse(document.getElementById('cc-data').textContent), T = D.t;
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); };
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
      '<button type="button" class="cc-open" data-open="' + d.id + '"><span class="vend">' + esc(d.vend) + '</span><span class="grow"><b>' + esc(d.name) + '</b><small class="mono">' + esc(d.host) + '</small></span>' +
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
        '<button type="button" class="btn s ghost" data-share="' + id + '">' + esc(T.share) + '</button><button type="button" class="btn s ghost" data-files="' + id + '">' + esc(T.files) + '</button>' +
        '<button type="button" class="tbtn" data-pm="reconnect" data-id="' + id + '" title="' + esc(T.recon) + '" aria-label="' + esc(T.recon) + '">' + ic('refresh') + '</button>' +
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
  }
  $('cc-stabs').addEventListener('click', function (e) {
    var a = e.target.closest('[data-act]'), x = e.target.closest('[data-x]');
    if (x) { var id = +x.dataset.x; open = open.filter(function (i) { return i !== id; }); if (active === id) active = open[0] || null; save(); paint(); }
    else if (a) { active = +a.dataset.act; save(); paint(); }
  });
  $('cc-layout').addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) { layout = +b.dataset.n; save(); paint(); } });
  $('cc-grid').addEventListener('click', function (e) {
    var p = e.target.closest('.cc-panel'); if (p && +p.dataset.id !== active) { active = +p.dataset.id; save(); paint(); }
    var s = e.target.closest('[data-share]'), f = e.target.closest('[data-files]'), pm = e.target.closest('[data-pm]'), fu = e.target.closest('[data-full]');
    if (pm) msgTo(+pm.dataset.id, {cmd: pm.dataset.pm});
    if (fu && frames[fu.dataset.full].requestFullscreen) frames[fu.dataset.full].requestFullscreen();
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
      return '<label class="row nowrap sm"><input type="checkbox" data-m="' + d.id + '" ' + (msel[d.id] ? 'checked' : '') + '><span class="vend">' + esc(d.vend) + '</span><span class="grow">' + esc(d.name) + '</span><small class="muted mono">' + esc(d.host) + '</small></label>';
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
      if (j.change) { $('mu-res').hidden = true; $('mu-change').hidden = false; $('mu-change-t').textContent = j.msg; return; }
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
  $('mu-run').addEventListener('click', runMulti);
  $('mu-cmd').addEventListener('keydown', function (e) { if (e.key === 'Enter') runMulti(); });
  $('mu-cr').addEventListener('click', function () {
    var ids = Object.keys(msel).map(Number);
    post('/console/change-request', {device_ids: ids, body: $('mu-cmd').value, title: $('mu-cmd').value.slice(0, 120)}).then(function (j) { if (j.url) location.href = j.url; else toast(j.error); });
  });
  if ($('mu-ai')) $('mu-ai').addEventListener('click', function () {
    if (!lastMulti || !window.ocAi) return;
    var txt = 'Comando: ' + lastMulti.cmd + '\n\n' + lastMulti.res.groups.map(function (g, i) { return (i ? 'Distintos' : 'Iguales') + ' (' + g.devs.join(', ') + '):\n' + g.out; }).join('\n\n');
    window.ocAi.open({kind: 'multi', text: txt.slice(0, 15000), ask: T.analyze});
  });

  /* ---------- Recetas ---------- */
  var cat = '', vendors = {}; D.recipes.forEach(function (r) { vendors[r.vendor] = 1; });
  $('rc-vendor').innerHTML += Object.keys(vendors).sort().map(function (v) { return '<option>' + esc(v) + '</option>'; }).join('');
  $('rc-cats').innerHTML = '<button type="button" class="chip on" data-c="">' + esc(T.all) + '</button>' + Object.keys(D.cats).map(function (k) { return '<button type="button" class="chip" data-c="' + k + '">' + esc(D.cats[k]) + '</button>'; }).join('');
  $('rc-cats').addEventListener('click', function (e) { var b = e.target.closest('[data-c]'); if (!b) return; cat = b.dataset.c; Array.prototype.forEach.call(this.children, function (c) { c.classList.toggle('on', c === b); }); grid(); });
  $('rc-q').addEventListener('input', grid); $('rc-vendor').addEventListener('change', grid);
  function scopeL(r) { return r.scope === 'mine' ? T.mine : r.scope === 'team' ? T.team : T.opennccm; }
  function grid() {
    var q = $('rc-q').value.trim().toLowerCase(), v = $('rc-vendor').value;
    var rs = D.recipes.filter(function (r) { return (!cat || r.cat === cat) && (!v || r.vendor === v) && (!q || (r.name + ' ' + r.intro + ' ' + r.vendor).toLowerCase().indexOf(q) >= 0); });
    $('rc-grid').innerHTML = rs.map(function (r) {
      return '<button type="button" class="card cc-rc" data-r="' + esc(r.key) + '"><div class="row sm"><span class="pill sm">' + esc(r.vendor) + '</span><span class="muted">' + esc(D.cats[r.cat] || '') + '</span><span class="grow"></span><span class="muted">' + esc(scopeL(r)) + '</span></div>' +
        '<b>' + esc(r.name) + '</b><span class="muted sm">' + esc(T.steps.replace('{0}', r.steps.length)) + ' · ' + esc(T.uses.replace('{0}', r.uses)) + ' · ' + esc(r.by) + '</span></button>';
    }).join('') || '<p class="muted">' + esc(T.none) + '</p>';
  }
  var cur = null;
  function vals() { var o = {}; Array.prototype.forEach.call(document.querySelectorAll('#rc-detail [data-var]'), function (i) { o[i.dataset.var] = i.value; }); return o; }
  function fill(c, o) { return c.replace(/\{\{(\w+)\}\}/g, function (m, n) { return o[n] != null ? o[n] : m; }); }
  function osOf(r) { return r.os; }
  function riskOf(r, c) {  // lo mismo que consolecore, simplificado: ¿solo consulta?
    var l = c.trim();
    if (r.os === 'ros') return /(^|\s)(add|set|remove|enable|disable|unset|reset|reboot|import|move|shutdown)(\s|$)/.test(l) ? 'change' : 'read';
    return /^(show|sh |display|ping|traceroute|tracert)/i.test(l) ? 'read' : 'change';
  }
  function detail(key) {
    var r = D.recipes.find(function (x) { return x.key === key; }); if (!r) return; cur = r;
    var el = $('rc-detail'); el.hidden = false;
    el.innerHTML = '<div class="row"><div class="grow"><div class="row sm"><span class="pill sm">' + esc(r.vendor) + '</span><span class="muted">' + esc(r.os_label || '') + ' · ' + esc(D.cats[r.cat] || '') + ' · ' + esc(scopeL(r)) + '</span></div><h2 style="margin:6px 0 0">' + esc(r.name) + '</h2></div><button type="button" class="btn s ghost" id="rc-x">✕</button></div>' +
      '<p class="muted">' + esc(r.intro) + (r.doc ? ' <a href="' + esc(r.doc) + '" target="_blank" rel="noopener">' + esc(T.doc) + '</a>' : '') + '</p>' +
      (r.vars.length ? '<div class="f3">' + r.vars.map(function (v) { return '<div class="field"><label>' + esc(v.l) + '</label><div class="input mono"><input data-var="' + esc(v.n) + '" value="' + esc(v.v) + '"></div></div>'; }).join('') + '</div>' : '') +
      '<ol class="cc-steps" id="rc-steps"></ol>' +
      '<div class="row"><button type="button" class="btn pri" id="rc-run"></button><button type="button" class="btn" id="rc-multi">' + esc(T.multi) + '</button><span class="grow"></span>' +
      (D.role !== 'viewer' ? (r.mine ? '<button type="button" class="btn ghost" id="rc-edit-b">' + esc(T.edit) + '</button><button type="button" class="btn ghost dz" id="rc-del">' + esc(T.del) + '</button>' : '<button type="button" class="btn ghost" id="rc-dup">' + esc(T.dup) + '</button>') : '') + '</div>';
    steps();
    Array.prototype.forEach.call(el.querySelectorAll('[data-var]'), function (i) { i.addEventListener('input', steps); });
    $('rc-x').addEventListener('click', function () { el.hidden = true; });
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
    el.scrollIntoView({behavior: 'smooth', block: 'start'});
  }
  function steps() {
    var r = cur, o = vals();
    $('rc-steps').innerHTML = r.steps.map(function (s, i) {
      var c = fill(s.c, o);
      return '<li class="' + (s.warn ? 'warn' : '') + (s.check ? ' check' : '') + '"><div class="sm">' + (s.warn ? '<b class="k-warn">' + esc(T.warn) + ' · </b>' : s.check && s.t.indexOf(T.check) !== 0 ? '<b class="k-ok">' + esc(T.check) + ' · </b>' : '') + esc(s.t) + '</div>' +
        (c ? '<div class="cx" data-theme="0" style="height:auto"><div class="cx-out mono" style="padding:8px 10px">' + esc(c) + '</div></div><div class="row"><button type="button" class="btn s ghost" data-cp="' + i + '">' + esc(T.copy) + '</button><button type="button" class="btn s ghost" data-in="' + i + '">' + esc(T.insert) + '</button></div>' : '') + '</li>';
    }).join('');
  }
  $('rc-grid').addEventListener('click', function (e) { var b = e.target.closest('[data-r]'); if (b) { detail(b.dataset.r); post('/console/recipes/used', {key: b.dataset.r}); } });
  $('rc-detail').addEventListener('click', function (e) {
    var c = e.target.closest('[data-cp]'), n = e.target.closest('[data-in]');
    var o = vals();
    if (c) navigator.clipboard.writeText(fill(cur.steps[+c.dataset.cp].c, o)).then(function () { toast(T.copied); });
    if (n) { if (!active || !msgTo(active, {cmd: 'insert', text: fill(cur.steps[+n.dataset.in].c, o).split('\n')[0]})) toast(T.noact); else { show('sesiones'); } }
  });
  function runActive(r) {
    if (!active) { toast(T.noact); return; }
    var o = vals(), all = r.steps.map(function (s) { return fill(s.c, o); }).join('\n');
    msgTo(active, {cmd: 'run', text: all}); toast(T.sent); show('sesiones');
  }
  function applyDlg(r) {
    var dl = $('rc-apply');
    $('rc-apply-devs').innerHTML = D.devs.filter(function (d) { return d.os === r.os; }).map(function (d) { return '<label class="row nowrap sm"><input type="checkbox" value="' + d.id + '"><span class="vend">' + esc(d.vend) + '</span><span class="grow">' + esc(d.name) + '</span><small class="muted mono">' + esc(d.host) + '</small></label>'; }).join('') || '<p class="muted">—</p>';
    dl.showModal();
  }
  $('rc-apply-f').addEventListener('submit', function (e) {
    e.preventDefault();
    var ids = Array.prototype.map.call(this.querySelectorAll('input:checked'), function (i) { return +i.value; }), o = vals(), r = cur;
    if (!ids.length) { toast(T.pickdev); return; }
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

  list(); paint(); mlist(); grid();
  show(location.hash.slice(1) || D.tab);
  if (D.rec) { show('grabaciones'); openRec(D.rec); }
})();
