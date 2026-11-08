/* «Respaldar todos» con su progreso (app/bulkbackup.py; maqueta de Diseño UX «Dispositivos: progreso de Respaldar
   todos»). En todas las páginas: la píldora con el porcentaje junto a «Dispositivos» y el aviso al terminar. En
   Dispositivos: el botón, el panel (barra, contadores, quién se respalda ahora, cancelar, minimizar), cada fila de la
   lista en vivo y el resumen al terminar con los errores y «Reintentar». El respaldo corre en el servidor: al recargar
   se ve su estado real. Se consulta cada 2 s mientras corre. */
(function () {
  var tEl = document.getElementById('bk-t'); if (!tEl) return;
  var T = JSON.parse(tEl.textContent);
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); };
  var fmt = function (s) { var a = arguments; return s.replace(/\{(\d)\}/g, function (m, i) { return a[+i + 1]; }); };
  var spin = function (n) { n = n || 13; return '<svg class="bk-spin" width="' + n + '" height="' + n + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9"/></svg>'; };
  var check = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg>';
  var panel = $('bk-panel'), btn = $('bk-all'), form = $('bk-form'), pill = $('bk-pill'), sub = $('bk-sub');
  var subOrig = sub ? sub.innerHTML : '', timer = null, seenRun = false, minimized = false, lastId = null, seen = {};
  var errNow = {}, driftNow = {}, origSt = {};
  Array.prototype.forEach.call(document.querySelectorAll('#devtable tr[data-dev]'), function (tr) {
    var st = tr.querySelector('[data-bk-st]'); errNow[tr.dataset.dev] = !!(st && st.querySelector('.pill.crit'));
    if (st) origSt[tr.dataset.dev] = st.innerHTML;  // para devolver la fila a como estaba si se cancela antes de respaldarla
    driftNow[tr.dataset.dev] = tr.dataset.drift === '1';
  });
  var chipBase = {};
  ['error', 'drift'].forEach(function (k) { var c = document.querySelector('[data-chip="' + k + '"] .n'); if (c) chipBase[k] = +c.textContent || 0; });

  function dur(s) { var m = Math.floor(s / 60); return m ? fmt(T.m, m, s % 60) : fmt(T.s, s); }
  function plural(n, one, many) { return n === 1 ? one : fmt(many, n); }

  function paint(j) {
    var running = j && !j.ended;
    if (pill) { pill.hidden = !running; if (running) pill.innerHTML = spin(9) + j.pct + ' %'; }
    if (btn) {
      btn.disabled = !!running; btn.classList.toggle('bk-busy', !!running);
      btn.querySelector('span').textContent = running ? fmt(T.busy, j.pct) : T.all;
    }
    if (sub) sub.innerHTML = running ? ' · ' + esc(T.inprog) : (j && j.ended ? ' · ' + esc(T.last_now) : subOrig);
    if (!panel) return;
    if (!j || minimized) { panel.hidden = true; } else { panel.hidden = false; panel.innerHTML = running ? runHTML(j) : doneHTML(j); }
    rows(j);
  }

  function counts(j) {
    return '<div class="bk-cnt"><span><i class="bk-d ok"></i>' + esc(T.ok) + ' <b>' + j.n.ok + '</b></span><span><i class="bk-d er"></i>' + esc(T.err) + ' <b>' + j.n.err + '</b></span>' +
      '<span><i class="bk-d rn"></i>' + esc(T.run) + ' <b>' + j.n.run + '</b></span><span><i class="bk-d q"></i>' + esc(T.q) + ' <b>' + j.n.q + '</b></span>' +
      (j.now.length ? '<span class="bk-now">' + esc(T.now) + ' <b>' + esc(j.now.slice(0, 3).join(', ') + (j.now.length > 3 ? ', …' : '')) + '</b></span>' : '') + '</div>';
  }
  function bar(j, live) {
    var w = function (n) { return j.total ? (100 * n / j.total).toFixed(2) : 0; };
    return '<div class="bk-bar"><i class="ok" style="width:' + w(j.n.ok) + '%"></i><i class="er" style="width:' + w(j.n.err) + '%"></i>' + (live ? '<i class="rn" style="width:' + w(j.n.run) + '%"></i>' : '') + '</div>';
  }
  function runHTML(j) {
    var done = j.n.ok + j.n.err, eta = j.eta == null ? '' : ' · ' + (j.eta >= 90 ? fmt(T.eta_m, Math.round(j.eta / 60)) : fmt(T.eta, j.eta));
    return '<div class="bk-pp"><div class="bk-hd"><span class="bk-ic">' + spin() + '</span><div class="bk-t"><b>' + esc(j.total === 1 ? fmt(T.title1, 1, j.pct) : fmt(T.title, j.total, j.pct)) + '</b>' +
      '<span>' + esc(fmt(T.line, done, j.total, j.conc) + eta) + '</span></div>' +
      '<button type="button" class="btn s ghost" data-bk="min"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 15l6-6 6 6"/></svg> ' + esc(T.min) + '</button>' +
      (j.cancel ? '' : '<button type="button" class="btn s" data-bk="cancel">' + esc(T.cancel) + '</button>') + '</div>' +
      bar(j, true) + counts(j) + '<div class="bk-note">' + esc(T.note) + '</div></div>';
  }
  function doneHTML(j) {
    var ok = j.n.ok, er = j.n.err, chg = j.changed;
    var title = j.cancel ? T.cancelled : (er ? fmt(T.done_both, plural(ok, T.c1, T.cn), plural(er, T.e1, T.en)) : (ok === 1 ? T.done_ok1 : fmt(T.done_ok, ok)));
    var line = fmt(T.sum, j.total, dur(j.elapsed)) + ' · ' + (chg ? plural(chg, T.chg1, T.chgn) : T.chg0) + ' · ' + T.upd;
    var errs = j.devs.filter(function (d) { return d.st === 'err'; });
    return '<div class="bk-pp ' + (j.cancel ? 'cancelled' : 'done') + '"><div class="bk-hd"><span class="bk-ic g">' + check + '</span><div class="bk-t"><b>' + esc(title) + '</b><span>' + esc(line) + '</span></div>' +
      (er ? '<a class="btn s" href="/devices?status=error">' + esc(plural(er, T.see_err1, T.see_errn)) + '</a>' : '') +
      (chg ? '<a class="btn s" href="/reports/view/changes?days=1">' + esc(plural(chg, T.see_chg1, T.see_chgn)) + '</a>' : '') +
      '<button type="button" class="btn s ghost" data-bk="close">' + esc(T.close) + '</button></div>' + bar(j, false) +
      (errs.length ? '<div class="bk-errs"><b>' + esc(T.errbox) + '</b>' + errs.map(function (d) { return '<span><b>' + esc(d.name) + '</b> · ' + esc(d.why) + '</span>'; }).join('') +
        '<button type="button" class="bk-retry" data-bk="retry"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5"/></svg>' +
        esc(plural(errs.length, T.retry1, T.retryn)) + '</button></div>' : '') + '</div>';
  }

  function rows(j) {
    if (!j) return;
    var first = lastId !== j.id; lastId = j.id;
    j.devs.forEach(function (d) {
      var tr = document.querySelector('#devtable tr[data-dev="' + d.id + '"]'); if (!tr) return;
      var st = tr.querySelector('[data-bk-st]'), at = tr.querySelector('[data-bk-at]'), fw = tr.querySelector('[data-bk-fw]');
      var prev = seen[d.id]; seen[d.id] = d.st;
      tr.classList.toggle('bk-now', d.st === 'run');
      if (d.st === 'cancel') { if (origSt[d.id] != null) st.innerHTML = origSt[d.id]; }
      else if (d.st === 'q') st.innerHTML = '<span class="pill bk-q">' + esc(T.queued) + '</span>';
      else if (d.st === 'run') st.innerHTML = '<span class="pill bk-run">' + spin(12) + esc(d.step) + '</span>';
      else if (d.st === 'ok' || d.st === 'err') {
        if (prev === d.st && !first) return;
        st.innerHTML = (d.st === 'ok' ? '<span class="pill ok live">' + esc(T.okp) + '</span>' : '<span class="pill crit" title="' + esc(d.why) + '">' + esc(st.dataset.proto === 'ssh' ? T.ssh : T.error) + '</span>') +
          (d.changed ? '<span class="bk-chg">' + esc(T.changed) + '</span>' : '');
        if (at) at.innerHTML = d.st === 'ok' ? '<span>' + esc(T.secs) + '</span><div class="muted sm">' + esc(T.by) + '</div>'
          : (at.dataset.orig || (at.dataset.orig = at.querySelector('span') ? at.querySelector('span').outerHTML : '—')) + '<div class="muted sm">' + esc(T.failed) + '</div>';
        if (fw && d.fw && fw.textContent !== d.fw) fw.textContent = d.fw;
        if (prev && prev !== d.st) { var c = d.st === 'ok' ? 'bk-flash' : 'bk-flashE'; tr.classList.add(c); setTimeout(function () { tr.classList.remove(c); }, 1000); }
        errNow[d.id] = d.st === 'err';
        if (d.drift != null) driftNow[d.id] = !!d.drift;
      }
    });
    chips();
  }
  var errStart = Object.assign({}, errNow), driftStart = Object.assign({}, driftNow);
  function chips() {
    var de = 0, dd = 0;
    Object.keys(errNow).forEach(function (k) { de += (errNow[k] ? 1 : 0) - (errStart[k] ? 1 : 0); dd += (driftNow[k] ? 1 : 0) - (driftStart[k] ? 1 : 0); });
    var ce = document.querySelector('[data-chip="error"] .n'), cd = document.querySelector('[data-chip="drift"] .n');
    if (ce && chipBase.error != null) ce.textContent = Math.max(0, chipBase.error + de);
    if (cd && chipBase.drift != null) cd.textContent = Math.max(0, chipBase.drift + dd);
  }

  function toast(msg) {
    var t = document.createElement('div'); t.className = 'bk-toast'; t.setAttribute('role', 'status'); t.innerHTML = check + '<span>' + esc(msg) + '</span>';
    document.body.appendChild(t); setTimeout(function () { t.remove(); }, 3500);
  }
  function poll() {
    fetch('/backup/job', {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (r) {
      var j = r.job;
      if (j && !j.ended) seenRun = true;
      if (j && j.ended && seenRun) { seenRun = false; toast(j.cancel ? T.cancelled : T.toast); }
      paint(j);
      clearTimeout(timer);
      if (j && !j.ended) timer = setTimeout(poll, 2000);
    }).catch(function () { clearTimeout(timer); timer = setTimeout(poll, 4000); });
  }
  function post(url, body) {
    return fetch(url, {method: 'POST', credentials: 'same-origin', headers: {'Accept': 'application/json'}, body: body}).then(function (r) { return r.json(); });
  }
  if (form) form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (btn.disabled || (form.dataset.confirm && !confirm(form.dataset.confirm))) return;
    minimized = false;
    post('/backup/all', new FormData(form)).then(function (r) { if (!r.ok && r.error) toast(r.error); seenRun = true; poll(); });
  });
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-bk]'); if (!b) return;
    var a = b.dataset.bk;
    if (a === 'min') { minimized = true; panel.hidden = true; }
    else if (a === 'cancel') post('/backup/job/cancel').then(poll);
    else if (a === 'close') post('/backup/job/close').then(function () { panel.hidden = true; panel.innerHTML = ''; });
    else if (a === 'retry') { minimized = false; post('/backup/job/retry').then(function (r) { if (!r.ok && r.error) toast(r.error); seenRun = true; poll(); }); }
  });
  poll();
})();
