/* Configlets y scripts en tres columnas, como Recetas: a la izquierda «Para tus equipos» y las plataformas (las que
   no están en tu inventario, atenuadas), en el medio los configlets y los scripts, y a la derecha el elegido. */
(function () {
  var D = JSON.parse(document.getElementById('cfl-data').textContent), T = D.t;
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); };
  var ic = function (n) { return '<svg class="ico s" aria-hidden="true"><use href="/static/icons.svg#i-' + n + '"/></svg>'; };
  var vlogo = function (k, px) {
    return '<svg class="vlg" width="' + px + '" height="' + px + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><use href="'
      + esc(((window.OC && OC.vl) || '/static/vendor-logos.svg') + '#vl-' + (k || 'other')) + '"/></svg>';
  };
  var chev = '<svg class="ico s rc-chev" aria-hidden="true"><use href="/static/icons.svg#i-right"/></svg>';
  var st = {sel: '_mine', kind: '', id: null, more: false};
  var PL = {}; D.plats.forEach(function (p) { PL[p.os] = p; });
  var nf = function (n, one, many) { return n === 1 ? one : many.replace('{0}', n); };
  var forPlat = function (c, os) { return c.fams.indexOf('*') >= 0 || c.fams.indexOf(os) >= 0; };
  function pill(c, sm) { return c.verified_on ? '<span class="rc-pill ok sm">' + ic('shield') + esc(sm ? T.v_short : T.v_pill.replace('{0}', c.verified_on)) + '</span>' : '<span class="rc-pill sm">' + esc(T.v_none) + '</span>'; }
  function kindOk(c) { return !st.kind || c.kind === st.kind; }
  function q() { return $('cf-q').value.trim().toLowerCase(); }
  function match(c) { var s = q(); return !s || (c.name + ' ' + c.desc + ' ' + c.body + ' ' + c.serves).toLowerCase().indexOf(s) >= 0; }
  function sizeCols() {
    var c = $('cf-cols'); if (!c) return;
    if (window.innerWidth <= 1100) { c.style.height = ''; return; }
    var top = c.getBoundingClientRect().top + window.scrollY, h = Math.max(460, window.innerHeight - top - 10);
    c.style.height = h + 'px';
    var over = document.documentElement.scrollHeight - window.innerHeight; if (over > 0 && h - over >= 460) c.style.height = (h - over) + 'px';
  }
  window.addEventListener('resize', sizeCols);
  function counts() {
    var all = D.items.filter(match), b = $('cf-kind');
    b.querySelector('[data-k=""] .n').textContent = all.length;
    b.querySelector('[data-k="config"] .n').textContent = all.filter(function (c) { return c.kind !== 'exec'; }).length;
    b.querySelector('[data-k="exec"] .n').textContent = all.filter(function (c) { return c.kind === 'exec'; }).length;
  }
  function left() {
    var its = D.items.filter(function (c) { return kindOk(c) && match(c); });
    var cnt = function (p) { return its.filter(function (c) { return forPlat(c, p.os); }).length; };
    var mine = D.plats.filter(function (p) { return p.devs; }).sort(function (a, b) { return (!cnt(a)) - (!cnt(b)); }), other = D.plats.filter(function (p) { return !p.devs && p.n; });
    var row = function (p, dim) {
      var n = its.filter(function (c) { return forPlat(c, p.os); }).length;
      return '<button type="button" class="rc-pl' + (st.sel === p.os ? ' on' : '') + (dim ? ' dim' : '') + '" data-p="' + esc(p.os) + '">' + vlogo(p.vl, 28) +
        '<span class="rc-pt"><b>' + esc(p.label) + '</b><i>' + (p.devs ? esc(nf(p.devs, T.dev_1, T.dev_n)) : esc(T.none_dev)) + '</i></span><span class="rc-pn">' + n + '</span></button>';
    };
    var nMine = its.filter(function (c) { return c.compat; }).length;
    var h = '<div class="rc-lh">' + esc(T.yours) + '</div>' +
      '<button type="button" class="rc-pl' + (st.sel === '_mine' ? ' on' : '') + '" data-p="_mine"><span class="cf-mine">' + ic('server') + '</span><span class="rc-pt"><b>' + esc(T.for_yours) + '</b><i>' + esc(T.for_yours_s) + '</i></span><span class="rc-pn">' + nMine + '</span></button>' +
      mine.map(function (p) { return row(p, !cnt(p)); }).join('');
    if (other.length) h += '<div class="rc-lh gap">' + esc(T.not_yours) + '</div><p class="muted sm cf-note">' + esc(T.dim_note) + '</p>' + other.map(function (p) { return row(p, true); }).join('');
    $('cf-left').innerHTML = h;
  }
  function crow(c) {
    return '<button type="button" class="rc-row' + (c.id === st.id ? ' on' : '') + (c.compat ? '' : ' dim') + '" data-c="' + c.id + '">' + vlogo(c.vl, 24) +
      '<span class="rc-rt">' + esc(c.short) + '<i>' + esc(c.serves) + ' · ' + esc(c.compat ? nf(c.compat, T.dev_1, T.dev_n) : T.none_dev) + '</i></span>' + pill(c, true) + chev + '</button>';
  }
  function mid() {
    var its = D.items.filter(function (c) { return kindOk(c) && match(c) && (q() || (st.sel === '_mine' ? true : forPlat(c, st.sel))); });
    var head = '';
    if (q()) head = '<div class="rc-rh"><b>' + esc(T.found.replace('{0}', its.length).replace('{1}', $('cf-q').value.trim())) + '</b></div>';
    else if (st.sel === '_mine') head = '<div class="rc-mh"><span class="cf-mine big">' + ic('server') + '</span><div class="grow"><h2>' + esc(T.for_yours) + '</h2><div class="muted sm">' + esc(T.for_yours_s) + '</div></div></div>';
    else { var p = PL[st.sel]; head = '<div class="rc-mh">' + vlogo(p.vl, 36) + '<div class="grow"><h2>' + esc(p.label) + '</h2><div class="muted sm">' + esc(p.devs ? nf(p.devs, T.dev_1, T.dev_n) : T.none_dev) + '</div></div></div>'; }
    var shown = st.sel === '_mine' && !q() ? its.filter(function (c) { return c.compat; }) : its;
    var rest = st.sel === '_mine' && !q() ? its.filter(function (c) { return !c.compat; }) : [];
    var grp = function (k, list) {
      if (!list.length) return '';
      return '<div class="rc-cg cf-cg">' + ic(k === 'exec' ? 'term' : 'code') + esc(k === 'exec' ? T.g_exec : T.g_cfg) + ' <span>' + list.length + '</span></div>' + list.map(crow).join('');
    };
    var h = head + grp('config', shown.filter(function (c) { return c.kind !== 'exec'; })) + grp('exec', shown.filter(function (c) { return c.kind === 'exec'; }));
    if (!shown.length && !q() && st.sel !== '_mine') {
      var pl = PL[st.sel];
      h += '<div class="cf-empty"><p>' + esc(T.none_for.replace('{0}', pl.label)) + '</p><div class="row">' +
        (D.admin ? '<a class="btn pri" href="/configlets/new" data-sheet data-title="' + esc(T.new_t) + '">' + ic('plus') + esc(T.new_for.replace('{0}', pl.label)) + '</a>' : '') +
        '<a class="btn" href="/console?rc=' + encodeURIComponent(pl.os) + '#recetas">' + ic('file') + esc(T.see_recipes) + '</a></div></div>';
    } else if (!shown.length) h += '<p class="muted rc-empty">' + esc(q() ? T.no_res.replace('{0}', $('cf-q').value.trim()) : T.none) + '</p>';
    if (rest.length) h += '<button type="button" class="rc-sgh rc-toggle cf-more" id="cf-more" aria-expanded="' + st.more + '"><b>' + esc(T.not_yours) + ' ' + rest.length + '</b><span class="grow"></span><span class="muted sm">' + esc(st.more ? T.hide : T.show) + '</span>' + ic(st.more ? 'down' : 'right') + '</button>' +
      (st.more ? rest.map(crow).join('') : '');
    $('cf-mid').innerHTML = h;
  }
  function varmark(b) { return esc(b).replace(/\{\{\s*(\w+)\s*\}\}/g, '<b class="cf-var">{{ $1 }}</b>'); }
  function ghUrl(c) {
    var L = T.gh_l, body = [L[0].replace('{0}', c.name), L[1].replace('{0}', c.serves), L[2].replace('{0}', c.verified_on ? T.v_pill.replace('{0}', c.verified_on) : T.v_none),
      L[3].replace('{0}', D.version), '', L[4], '', L[5], ''].join('\n');
    return D.issues + '?title=' + encodeURIComponent(T.gh_title.replace('{0}', c.name)) + '&body=' + encodeURIComponent(body);
  }
  function detail() {
    var c = D.items.find(function (x) { return x.id === st.id; }), el = $('cf-detail');
    if (!c) { el.innerHTML = '<p class="muted rc-empty">' + esc(T.pick) + '</p>'; return; }
    var script = c.kind === 'exec';
    el.innerHTML = '<div class="rc-dh">' + vlogo(c.vl, 36) + '<div class="grow"><div class="muted sm">' + esc(script ? T.k_exec : T.k_cfg) + '</div><h3>' + esc(c.name) + '</h3></div></div>' +
      (c.desc ? '<p class="muted sm rc-intro">' + esc(c.desc) + '</p>' : '') +
      '<dl class="cf-dl"><dt>' + esc(T.serves) + '</dt><dd>' + esc(c.serves) + ' · ' + esc(c.compat ? nf(c.compat, T.dev_1, T.dev_n) : T.none_dev) + '</dd>' +
      '<dt>' + esc(T.seal) + '</dt><dd>' + pill(c, false) + '</dd><dt>' + esc(T.used) + '</dt><dd>' + esc(nf(c.uses, T.times_1, T.times_n)) + '</dd></dl>' +
      '<div class="wl-short">' + ic('alert') + '<span>' + esc(T.w_short) + (D.forum ? ' <a href="' + esc(D.forum) + '" target="_blank" rel="noopener">' + esc(T.forum) + '</a>' : '') +
        (D.issues ? ' · <a href="' + esc(ghUrl(c)) + '" target="_blank" rel="noopener">GitHub</a>' : '') + '</span></div>' +
      '<pre class="cf-body mono">' + varmark(c.body) + '</pre>' +
      (D.admin ? '<div><button type="button" class="btn s ghost" id="cf-verify">' + ic('shield') + esc(c.verified_on ? T.v_unset : T.v_set) + '</button></div>' : '') +
      '<div class="rc-acts">' + (D.can_run ? '<a class="btn pri" href="/configlets/' + c.id + '/run" data-sheet data-wide data-title="' + esc(T.run_t.replace('{0}', c.name)) + '">' + ic('play') + esc(T.run) + '</a>' : '') +
      (D.admin ? '<a class="btn" href="/configlets/' + c.id + '/edit" data-sheet data-title="' + esc(T.edit_t.replace('{0}', c.name)) + '">' + ic('edit') + esc(T.edit) + '</a>' +
        '<a class="btn ghost" href="/configlets/new?from=' + c.id + '" data-sheet data-title="' + esc(T.dup) + '">' + esc(T.dup) + '</a>' : '') + '</div>';
    if ($('cf-verify')) $('cf-verify').addEventListener('click', function () {
      var v = c.verified_on ? '' : prompt(T.v_ask, '');
      if (v === null) return;
      fetch('/configlets/' + c.id + '/verify', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({verified_on: v})})
        .then(function (r) { return r.json(); }).then(function (j) { if (j.ok) { c.verified_on = j.verified_on; mid(); detail(); } else alert(j.error); });
    });
  }
  function render() { counts(); left(); mid(); detail(); sizeCols(); }
  $('cf-q').addEventListener('input', function () { st.more = false; render(); });
  $('cf-kind').addEventListener('click', function (e) {
    var b = e.target.closest('[data-k]'); if (!b) return; st.kind = b.dataset.k;
    Array.prototype.forEach.call(this.querySelectorAll('[data-k]'), function (x) { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', String(x === b)); });
    render();
  });
  $('cf-left').addEventListener('click', function (e) { var p = e.target.closest('[data-p]'); if (p) { st.sel = p.dataset.p; st.more = false; render(); $('cf-mid').scrollTop = 0; } });
  $('cf-mid').addEventListener('click', function (e) {
    if (e.target.closest('#cf-more')) { st.more = !st.more; mid(); return; }
    var r = e.target.closest('[data-c]'); if (r) { st.id = +r.dataset.c; mid(); detail(); }
  });
  // al abrir: el primero que puedes ejecutar
  var first = D.items.filter(function (c) { return c.compat; })[0] || D.items[0]; st.id = first ? first.id : null;
  if (D.open) st.id = D.open;
  render();
})();
