/* Ficha del equipo › Versiones: «Cambios y problemas en Zabbix» (app/zbxcorr.py).
   Pide /devices/{id}/zabbix?days=N y dibuja, en una sola franja de tiempo: los cambios de configuración (líneas vN),
   los problemas de Zabbix (barras por gravedad) y una métrica (latencia, pérdida, CPU o memoria) con cursor e
   información al pasar el puntero. Debajo, la tabla de problemas con el cambio que hubo justo antes. También marca en
   la línea de tiempo de versiones las que tuvieron problemas poco después. Sin dependencias externas. */
(function () {
  var card = document.getElementById('zbx');
  if (!card) return;
  var T = JSON.parse(document.getElementById('zbx-i18n').textContent);
  var dev = card.getAttribute('data-dev'), body = document.getElementById('zbx-body');
  var hostEl = document.getElementById('zbx-host'), rangeEl = document.getElementById('zbx-range');
  var NS = 'http://www.w3.org/2000/svg';
  var SEV_COLOR = ['var(--muted)', 'var(--brand)', 'var(--cat-4)', 'var(--warn)', 'var(--major)', 'var(--crit)'];
  var SEV_PILL = ['', 'info', 'warn', 'warn', 'major', 'crit'];
  var days = 7, data = null, metric = 0, tip = null, lastW = 0;
  var lang = document.documentElement.lang || 'es';
  var fmtDay = new Intl.DateTimeFormat(lang, {day: 'numeric', month: 'short'});
  var fmtHour = new Intl.DateTimeFormat(lang, {hour: '2-digit', minute: '2-digit'});
  var fmtFull = new Intl.DateTimeFormat(lang, {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'});

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function dur(sec) {
    var m = Math.max(1, Math.round(sec / 60));
    if (m < 60) return T.min.replace('{0}', m);
    var h = Math.floor(m / 60);
    if (h < 48) return T.hmin.replace('{0}', h).replace('{1}', m % 60);
    return T.dh.replace('{0}', Math.floor(h / 24)).replace('{1}', h % 24);
  }
  function num(v) { return (Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 10) / 10).toLocaleString(lang); }
  function openSheet(href) {
    var a = document.createElement('a');
    a.href = href; a.setAttribute('data-sheet', ''); a.setAttribute('data-wide', ''); a.hidden = true;
    document.body.appendChild(a); a.click(); a.remove();
  }

  function load() {
    body.innerHTML = '<p class="muted sm">' + esc(T.loading) + '</p>';
    fetch('/devices/' + dev + '/zabbix?days=' + days, {credentials: 'same-origin'})
      .then(function (r) { return r.json(); })
      .then(function (j) { data = j; metric = Math.min(metric, Math.max(0, (j.metrics || []).length - 1)); render(); mark(); })
      .catch(function () { data = {ok: false, msg: T.failed}; render(); });
  }

  // línea de tiempo de versiones: «N problemas en Zabbix» en las versiones después de las cuales empezó alguno
  function mark() {
    document.querySelectorAll('.tl .zbx-tag').forEach(function (t) { t.remove(); });
    if (!data || !data.ok) return;
    var by = {};
    data.problems.forEach(function (p) { if (p.after) (by[p.after.id] = by[p.after.id] || []).push(p); });
    Object.keys(by).forEach(function (id) {
      var ps = by[id], worst = Math.max.apply(null, ps.map(function (p) { return p.sev; }));
      document.querySelectorAll('.tl li[data-id="' + id + '"] > div.row').forEach(function (row) {
        var s = document.createElement('span');
        s.className = 'tag zbx-tag ' + (SEV_PILL[worst] || '');
        s.textContent = (ps.length === 1 ? T.tag1 : T.tagn.replace('{0}', ps.length));
        s.setAttribute('data-tip', ps.map(function (p) { return p.sev_label + ' · ' + p.name + ' (' + T.after_min.replace('{0}', p.after.min) + ')'; }).join('\n'));
        row.appendChild(s);
      });
    });
  }

  function render() {
    if (!data) return;
    hostEl.innerHTML = data.ok ? '<a href="' + esc(data.host.url) + '" target="_blank" rel="noopener">' + esc(T.open_in.replace('{0}', data.host.name)) + ' ↗</a>' : '';
    if (!data.ok) {
      body.innerHTML = '<div class="banner ' + (data.reason === 'error' ? 'crit' : 'info') + '" role="status"><span class="grow">' + esc(data.msg) + '</span></div>';
      return;
    }
    var probs = data.problems, ms = data.metrics || [];
    var rel = probs.filter(function (p) { return p.after; }).length, open = probs.filter(function (p) { return !p.end; }).length;
    var h = '<div class="zbx-sum"><b>' + esc(probs.length === 1 ? T.p1 : T.pn.replace('{0}', probs.length)) + '</b>';
    if (open) h += ' · <span>' + esc(T.open_n.replace('{0}', open)) + '</span>';
    if (rel) h += ' · <b class="zbx-rel">' + esc(rel === 1 ? T.rel1 : T.reln.replace('{0}', rel)) + '</b>';
    h += ' · <span class="muted">' + esc(data.changes.length === 1 ? T.c1 : T.cn.replace('{0}', data.changes.length)) + '</span></div>';
    if (ms.length > 1) {
      h += '<div class="seg zbx-met" role="group" aria-label="' + esc(T.metric) + '">' + ms.map(function (m, i) {
        return '<button type="button" data-m="' + i + '" class="' + (i === metric ? 'on' : '') + '" aria-pressed="' + (i === metric) + '" title="' + esc(m.item) + '">' + esc(m.label) + '</button>';
      }).join('') + '</div>';
    }
    h += '<div class="zbx-chart" id="zbx-chart"></div>';
    h += '<div class="zbx-leg muted sm"><span><i class="zl-chg"></i>' + esc(T.leg_change) + '</span>';
    var sevs = {};
    probs.forEach(function (p) { sevs[p.sev] = p.sev_label; });
    Object.keys(sevs).sort().reverse().forEach(function (s) { h += '<span><i style="background:' + SEV_COLOR[s] + '"></i>' + esc(sevs[s]) + '</span>'; });
    if (ms.length) h += '<span><i class="zl-met"></i>' + esc(ms[metric].label + ' (' + ms[metric].unit + ')') + '</span>';
    h += '</div>';
    if (!ms.length) h += '<p class="muted sm">' + esc(T.no_metrics) + '</p>';
    if (probs.length) {
      h += '<div class="tw"><table class="t zbx-t"><thead><tr><th>' + esc(T.th_sev) + '</th><th>' + esc(T.th_prob) + '</th><th>' + esc(T.th_start) + '</th><th>' + esc(T.th_dur) + '</th><th>' + esc(T.th_after) + '</th></tr></thead><tbody>';
      probs.slice().reverse().forEach(function (p) {
        var a = p.after ? '<a href="' + (p.after.prev ? '?a=' + p.after.prev + '&b=' + p.after.id + '#versions' : '/versions/' + p.after.id) + '"' + (p.after.prev ? '' : ' data-sheet data-wide') + '>' + esc(T.after_v.replace('{0}', p.after.n).replace('{1}', p.after.min)) + '</a>' : '<span class="muted">—</span>';
        h += '<tr' + (p.after ? ' class="zbx-hit"' : '') + '><td><span class="pill ' + (SEV_PILL[p.sev] || '') + '">' + esc(p.sev_label) + '</span></td><td><a href="' + esc(p.url) + '" target="_blank" rel="noopener">' + esc(p.name) + '</a></td>' +
          '<td class="nowrap">' + esc(fmtFull.format(new Date(p.start * 1000))) + '</td><td class="nowrap">' + (p.end ? esc(dur(p.end - p.start)) : '<b>' + esc(T.ongoing) + '</b>') + '</td><td class="nowrap">' + a + '</td></tr>';
      });
      h += '</tbody></table></div>';
    } else {
      h += '<p class="muted sm">' + esc(T.no_problems) + '</p>';
    }
    body.innerHTML = h;
    body.querySelectorAll('.zbx-met button').forEach(function (b) {
      b.addEventListener('click', function () { metric = +b.getAttribute('data-m'); render(); });
    });
    draw();
  }

  function draw() {
    var box = document.getElementById('zbx-chart');
    if (!box) return;
    var W = box.clientWidth;
    lastW = W;
    if (!W) return;  // pestaña oculta: se dibuja al mostrarse
    box.innerHTML = '';
    var probs = data.problems, ms = data.metrics || [], m = ms[metric];
    var t0 = data.from, t1 = data.till, L = 44, R = 12;
    // problemas en filas sin solaparse (como mucho 4: el resto se junta en la última)
    var rows = [];
    probs.forEach(function (p) {
      var s = Math.max(p.start, t0), e = p.end || t1, r = 0;
      while (r < 3 && rows[r] !== undefined && rows[r] > s) r++;
      p._row = r; rows[r] = Math.max(rows[r] || 0, e);
    });
    var lanes = Math.max(1, rows.length), top = 22, laneH = 10, gap = 4;
    var pTop = top, pH = lanes * (laneH + gap);
    var mTop = pTop + pH + 10, mH = m ? 130 : 0, axisY = mTop + mH + (m ? 8 : 0), H = axisY + 22;
    var svg = el('svg', {width: W, height: H, viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': T.chart_label}, box);
    var x = function (t) { return L + (t - t0) / (t1 - t0) * (W - L - R); };
    // eje de tiempo
    var span = t1 - t0, step = span <= 86400 ? 4 * 3600 : span <= 8 * 86400 ? 86400 : 5 * 86400;
    while ((W - L - R) * step / span < 56) step *= 2;  // pantallas estrechas: menos marcas, sin que se pisen
    var first = Math.ceil(t0 / step) * step;
    for (var t = first; t <= t1; t += step) {
      var d = new Date(t * 1000);
      el('line', {x1: x(t), x2: x(t), y1: pTop - 4, y2: axisY, class: 'zg'}, svg);
      var lb = el('text', {x: x(t), y: axisY + 15, 'text-anchor': 'middle', class: 'zt'}, svg);
      lb.textContent = span <= 86400 ? fmtHour.format(d) : fmtDay.format(d);
    }
    // métrica: líneas de referencia, valores y la serie
    if (m && m.points.length) {
      var vmax = Math.max.apply(null, m.points.map(function (p) { return p[1]; }));
      if (m.unit === '%') vmax = Math.max(vmax, 10);
      vmax = vmax <= 0 ? 1 : vmax * 1.1;
      var y = function (v) { return mTop + mH - v / vmax * mH; };
      [0, 0.5, 1].forEach(function (f) {
        el('line', {x1: L, x2: W - R, y1: y(vmax * f), y2: y(vmax * f), class: 'zg'}, svg);
        el('text', {x: L - 6, y: y(vmax * f) + 4, 'text-anchor': 'end', class: 'zt'}, svg).textContent = num(vmax * f);
      });
      var path = m.points.map(function (p, i) { return (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1); }).join(' ');
      el('path', {d: path, class: 'zm'}, svg);
    } else if (m) {
      el('text', {x: (W + L) / 2, y: mTop + mH / 2, 'text-anchor': 'middle', class: 'zt'}, svg).textContent = T.no_data;
    }
    // problemas
    probs.forEach(function (p) {
      var s = Math.max(p.start, t0), e = p.end || t1, bx = x(s), bw = Math.max(x(e) - bx, 4);
      var g = el('g', {class: 'zp', tabindex: '0', 'data-tip': p.sev_label + ' · ' + p.name + '\n' + fmtFull.format(new Date(p.start * 1000)) + ' · ' + (p.end ? dur(p.end - p.start) : T.ongoing) + (p.after ? '\n' + T.after_v.replace('{0}', p.after.n).replace('{1}', p.after.min) : '')}, svg);
      el('rect', {x: bx, y: pTop + p._row * (laneH + gap), width: bw, height: laneH, rx: 3, fill: SEV_COLOR[p.sev] || SEV_COLOR[0]}, g);
      if (!p.end) el('rect', {x: bx + bw - 2, y: pTop + p._row * (laneH + gap), width: 2, height: laneH, fill: 'var(--surface)'}, g);
    });
    // cambios de configuración
    var lastLabel = -99;
    data.changes.forEach(function (c) {
      var cx = x(c.t), g = el('g', {class: 'zc', tabindex: '0', role: 'button', 'aria-label': 'v' + c.n,
        'data-tip': 'v' + c.n + ' · ' + c.src + (c.who ? ' · ' + c.who : '') + ' · ' + fmtFull.format(new Date(c.t * 1000)) + (c.first ? '' : '\n+' + c.added + ' −' + c.removed)}, svg);
      el('line', {x1: cx, x2: cx, y1: top - 6, y2: axisY, class: 'zcl' + (c.added || c.removed || c.first ? '' : ' same')}, g);
      el('rect', {x: cx - 6, y: 0, width: 12, height: axisY, fill: 'transparent'}, g);  // zona de clic más ancha
      if (cx - lastLabel > 26) { el('text', {x: cx, y: 10, 'text-anchor': 'middle', class: 'zt zcv'}, g).textContent = 'v' + c.n; lastLabel = cx; }
      var go = function () { openSheet('/versions/' + c.id); };
      g.addEventListener('click', go);
      g.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });
    // cursor sobre la métrica
    if (m && m.points.length) {
      var cur = el('line', {x1: 0, x2: 0, y1: mTop, y2: mTop + mH, class: 'zcur', visibility: 'hidden'}, svg);
      var dot = el('circle', {r: 4, class: 'zdot', visibility: 'hidden'}, svg);
      var hit = el('rect', {x: L, y: mTop, width: W - L - R, height: mH, fill: 'transparent'}, svg);
      if (!tip) { tip = document.createElement('div'); tip.className = 'viz-tip'; tip.hidden = true; document.body.appendChild(tip); }
      var ymax = vmax;
      hit.addEventListener('pointermove', function (e) {
        var r = svg.getBoundingClientRect(), px = e.clientX - r.left;
        var tt = t0 + (px - L) / (W - L - R) * (t1 - t0), best = m.points[0];
        m.points.forEach(function (p) { if (Math.abs(p[0] - tt) < Math.abs(best[0] - tt)) best = p; });
        var bx = x(best[0]), by = mTop + mH - best[1] / ymax * mH;
        cur.setAttribute('x1', bx); cur.setAttribute('x2', bx); cur.setAttribute('visibility', 'visible');
        dot.setAttribute('cx', bx); dot.setAttribute('cy', by); dot.setAttribute('visibility', 'visible');
        var act = probs.filter(function (p) { return p.start <= best[0] && (!p.end || p.end >= best[0]); });
        tip.textContent = fmtFull.format(new Date(best[0] * 1000)) + ' · ' + m.label + ': ' + num(best[1]) + ' ' + m.unit +
          (act.length ? '\n' + act.map(function (p) { return p.sev_label + ' · ' + p.name; }).join('\n') : '');
        tip.hidden = false;
        var tr = tip.getBoundingClientRect(), tx = e.clientX + 14;
        if (tx + tr.width > window.innerWidth - 8) tx = e.clientX - tr.width - 14;
        tip.style.left = Math.max(8, tx) + 'px'; tip.style.top = (e.clientY + 14) + 'px';
      });
      hit.addEventListener('pointerleave', function () { cur.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); if (tip) tip.hidden = true; });
    }
  }

  rangeEl.querySelectorAll('button').forEach(function (b) {
    b.addEventListener('click', function () {
      days = +b.getAttribute('data-d');
      rangeEl.querySelectorAll('button').forEach(function (o) { o.classList.toggle('on', o === b); o.setAttribute('aria-pressed', o === b); });
      load();
    });
  });
  if (window.ResizeObserver) new ResizeObserver(function () { var b = document.getElementById('zbx-chart'); if (data && data.ok && b && b.clientWidth !== lastW) draw(); }).observe(card);
  load();
})();
