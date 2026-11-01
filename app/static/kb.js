/* Equipo › Base de conocimiento (app/kb.py): buscador (Ctrl K), ficha (Me sirvió, Copiar, Abrir en consola) y editor
   (guardado automático, lo que falta, pasos, equipos, traer comandos de la grabación, asistente y casos parecidos). */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); };
  var toast = function (t, k) { if (window.ocToast) window.ocToast(t, k); };
  var json = function (url, body) {
    return fetch(url, body === undefined ? {credentials: 'same-origin'} : {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}).then(function (r) {
      if (!r.ok) throw new Error(r.status); return r.json();
    });
  };

  /* Ctrl K: el buscador de la base de conocimiento (antes que la paleta general) */
  window.addEventListener('keydown', function (e) {
    var q = $('kb-q');
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K') && q) { e.preventDefault(); e.stopImmediatePropagation(); q.focus(); q.select(); }
  }, true);

  document.addEventListener('DOMContentLoaded', function () { caseView(); editor(); });

  /* ---------- ficha ---------- */
  function caseView() {
    var art = $('kb-case'); if (!art) return;
    var T = JSON.parse($('kb-t').textContent), id = art.dataset.id, dev = art.dataset.dev;
    var vote = $('kb-vote');
    if (vote) vote.addEventListener('click', function () {
      fetch('/kb/' + id + '/vote', {method: 'POST', credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (j) {
        $('kb-vote-n').textContent = j.n; vote.classList.toggle('ok', j.on); vote.setAttribute('aria-pressed', j.on ? 'true' : 'false');
      }).catch(function () { toast(T.err, 'error'); });
    });
    art.addEventListener('click', function (e) {
      var c = e.target.closest('[data-copy]'), r = e.target.closest('[data-run]');
      if (c) {
        var text = c.dataset.copy;
        // la clave oculta no viaja: se copia el comando sin ella para escribirla desde Credenciales
        if (c.dataset.hid) text = text.replace(/•{6}/g, '');
        (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () {
          toast(c.dataset.hid ? T.copied_hid : T.copied);
        }).catch(function () { toast(T.err, 'error'); });
      } else if (r && dev) {
        // la consola del equipo con el comando escrito: no se ejecuta hasta que la persona pulse Enter
        var url = '/devices/' + dev + '/console?cmd=' + encodeURIComponent(r.dataset.run.replace(/•{6}/g, ''));
        if (window.abrirVentana) window.abrirVentana(url, 'consola-' + dev, 1000, 640); else window.open(url, '_blank');
      }
    });
  }

  /* ---------- editor ---------- */
  function editor() {
    var root = $('kb-ed'); if (!root) return;
    var P = JSON.parse($('kb-ed-data').textContent), T = P.t, D = P.data;
    var st = {id: P.id, title: D.title || '', category: D.category || '', symptom: D.symptom || '', cause: D.cause || '',
      verify: D.verify || '', prevent: D.prevent || '', steps: (D.steps || []).map(function (s) { return {t: s.t || '', c: s.c || '', hid: !!s.hid}; }),
      devices: (D.devices || []).filter(function (i) { return P.devs[i]; }), ai: (D.ai_marks || []).slice()};
    if (!st.steps.length) st.steps.push({t: '', c: '', hid: false});
    var dirty = false, saving = false, timer = null, lastSaved = 0;
    var fields = Array.prototype.slice.call(root.querySelectorAll('[data-f]'));
    fields.forEach(function (el) {
      el.value = st[el.dataset.f];
      el.addEventListener('input', function () {
        st[el.dataset.f] = el.value;
        var i = st.ai.indexOf(el.dataset.f); if (i >= 0) { st.ai.splice(i, 1); aiMarks(); }
        change(); if (el.dataset.f === 'title' || el.dataset.f === 'symptom') similar();
      });
    });

    /* categoría */
    var cats = Array.prototype.slice.call(root.querySelectorAll('[data-cat]'));
    function paintCats() { cats.forEach(function (b) { var on = b.dataset.cat === st.category; b.classList.toggle('on', on); b.setAttribute('aria-checked', on ? 'true' : 'false'); }); }
    cats.forEach(function (b) { b.addEventListener('click', function () { st.category = st.category === b.dataset.cat ? '' : b.dataset.cat; paintCats(); change(); }); });

    /* equipos */
    function devs() {
      var h = st.devices.map(function (i) { return '<span class="kb-dtag">' + esc(P.devs[i]) + ' <button type="button" data-rmdev="' + i + '" aria-label="' + esc(T.remove) + '">✕</button></span>'; }).join('');
      var opts = Object.keys(P.devs).filter(function (i) { return st.devices.indexOf(+i) < 0; })
        .sort(function (a, b) { return P.devs[a].localeCompare(P.devs[b]); })
        .map(function (i) { return '<option value="' + i + '">' + esc(P.devs[i]) + '</option>'; }).join('');
      $('ed-devs').innerHTML = h + '<select id="ed-adddev" aria-label="+ equipo"><option value="">+ equipo</option>' + opts + '</select>';
    }
    $('ed-devs').addEventListener('click', function (e) { var b = e.target.closest('[data-rmdev]'); if (b) { st.devices = st.devices.filter(function (i) { return i !== +b.dataset.rmdev; }); devs(); change(); } });
    $('ed-devs').addEventListener('change', function (e) { if (e.target.id === 'ed-adddev' && e.target.value) { st.devices.push(+e.target.value); devs(); change(); } });

    /* pasos */
    function steps() {
      $('ed-steps').innerHTML = st.steps.map(function (s, i) {
        return '<div class="kb-es"><span class="kb-n">' + (i + 1) + '</span><div class="f">' +
          '<input type="text" data-st="' + i + '" data-k="t" value="' + esc(s.t) + '" placeholder="' + esc(T.step) + '" aria-label="' + esc(T.step) + '">' +
          '<div class="row" style="gap:6px"><input type="text" class="cmd grow" data-st="' + i + '" data-k="c" value="' + esc(s.c) + '" placeholder="' + esc(T.cmd) + '" aria-label="' + esc(T.cmd) + '">' +
          (s.hid || s.c.indexOf('••••••') >= 0 ? '<span class="kb-hid">' + esc(T.hidden) + '</span>' : '') +
          '<button type="button" class="btn s ghost" data-rmst="' + i + '" aria-label="' + esc(T.remove) + '">✕</button></div></div></div>';
      }).join('');
    }
    $('ed-steps').addEventListener('input', function (e) { var el = e.target; if (el.dataset.st != null) { st.steps[+el.dataset.st][el.dataset.k] = el.value; change(); } });
    $('ed-steps').addEventListener('click', function (e) { var b = e.target.closest('[data-rmst]'); if (b) { st.steps.splice(+b.dataset.rmst, 1); if (!st.steps.length) st.steps.push({t: '', c: '', hid: false}); steps(); change(); } });
    $('ed-add').addEventListener('click', function () { st.steps.push({t: '', c: '', hid: false}); steps(); var l = $('ed-steps').querySelectorAll('[data-k="t"]'); l[l.length - 1].focus(); });

    /* lo que falta */
    function missing() {
      var s = st.steps.filter(function (x) { return x.t.trim() || x.c.trim(); });
      return [[T.req[0], !!(st.title.trim() && st.devices.length)], [T.req[1], !!st.symptom.trim()], [T.req[2], !!st.cause.trim()],
        [T.req[3], s.length > 0], [T.req[4], !!st.verify.trim()]];
    }
    function reqs() {
      var r = missing(), ok = r.filter(function (x) { return x[1]; }).length, full = ok === r.length;
      $('ed-reqs').innerHTML = r.map(function (x) { return '<div class="kb-req ' + (x[1] ? 'ok' : '') + '"><i>' + (x[1] ? '✓' : '!') + '</i><span>' + esc(x[0]) + '</span></div>'; }).join('');
      $('ed-bar').classList.toggle('ok', full); $('ed-bar').firstChild.style.width = Math.round(ok * 100 / r.length) + '%';
      $('ed-send').setAttribute('aria-disabled', full ? 'false' : 'true');
      $('ed-hint').textContent = full ? (P.direct ? T.hint_direct : T.hint_review) : T.hint_missing;
      fields.forEach(function (el) { if (el.dataset.f === 'cause' || el.dataset.f === 'verify') el.classList.toggle('miss', !st[el.dataset.f].trim()); });
    }
    function aiMarks() {
      ['cause', 'verify'].forEach(function (k) {
        var on = st.ai.indexOf(k) >= 0, n = root.querySelector('[data-ai-note="' + k + '"]');
        $('ed-' + k).classList.toggle('ai', on); if (n) { n.hidden = !on; n.textContent = T.proposed; }
      });
    }

    /* guardar */
    function body(action) {
      return {id: st.id, action: action, mode: P.mode, pending: P.pending, title: st.title, category: st.category, symptom: st.symptom, cause: st.cause,
        verify: st.verify, prevent: st.prevent, steps: st.steps, devices: st.devices, ai_marks: st.ai,
        source_kind: D.source_kind, source_id: D.source_id, console_id: D.console_id};
    }
    function apply(data) {
      // lo guardado ya trae las claves ocultas: se reflejan donde la persona no está escribiendo
      ['title', 'symptom', 'cause', 'verify', 'prevent'].forEach(function (k) {
        if (data[k] !== undefined && data[k] !== st[k] && document.activeElement !== $('ed-' + k)) { st[k] = data[k]; $('ed-' + k).value = data[k]; }
      });
      var typing = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.st != null;
      if (data.steps && !typing && data.steps.some(function (s, i) { return s.hid && (!st.steps[i] || !st.steps[i].hid); })) {
        st.steps = data.steps.map(function (s) { return {t: s.t, c: s.c, hid: s.hid}; });
        if (!st.steps.length) st.steps.push({t: '', c: '', hid: false});
        steps();
      }
    }
    function save(action) {
      if (saving) return Promise.resolve(null);
      saving = true; clearTimeout(timer); $('ed-state').textContent = T.saving;
      return json('/kb/save', body(action)).then(function (j) {
        saving = false;
        if (j.id && !st.id) { st.id = j.id; history.replaceState(null, '', '/kb/' + j.id + '/edit'); }
        if (j.data) apply(j.data);
        if (j.ok || action === 'auto') { dirty = false; lastSaved = Date.now(); tick(); }
        return j;
      }).catch(function () { saving = false; $('ed-state').textContent = T.err; return null; });
    }
    function tick() { if (lastSaved) $('ed-state').textContent = T.saved + ' · ' + T.ago_s.replace('{0}', Math.max(1, Math.round((Date.now() - lastSaved) / 1000))); }
    setInterval(tick, 5000);
    function change() { dirty = true; reqs(); if (P.mode !== 'propose') { clearTimeout(timer); timer = setTimeout(function () { save('auto'); }, 2500); } }
    window.addEventListener('beforeunload', function (e) { if (dirty && P.mode !== 'propose') { save('auto'); } else if (dirty) { e.preventDefault(); e.returnValue = ''; } });
    if ($('ed-draft')) $('ed-draft').addEventListener('click', function () { save('draft').then(function (j) { if (j && j.ok) toast(T.draft_saved); }); });
    $('ed-send').addEventListener('click', function () {
      var m = missing().filter(function (x) { return !x[1]; });
      if (m.length) { toast(T.missing + ' ' + m.map(function (x) { return x[0].toLowerCase(); }).join(', ') + '.', 'error'); return; }
      save('send').then(function (j) {
        if (!j) return;
        if (j.ok) { dirty = false; if (j.msg) sessionStorage.setItem('kb-msg', j.msg); location.href = j.url; }
        else toast(j.error || (T.missing + ' ' + (j.missing || []).join(', ').toLowerCase() + '.'), 'error');
      });
    });

    /* traer comandos de la grabación */
    if ($('ed-rec')) $('ed-rec').addEventListener('click', function () {
      json('/kb/api/rec/' + P.rec).then(function (j) {
        if (!j.items.length) { toast(T.rec_none); return; }
        st.steps = st.steps.filter(function (s) { return s.t.trim() || s.c.trim(); });
        j.items.forEach(function (c) { st.steps.push({t: '', c: c.c, hid: c.hid}); });
        steps(); change(); toast(T.rec_got.replace('{0}', j.items.length));
      }).catch(function () { toast(T.err, 'error'); });
    });

    /* asistente: propone causa y comprobación, marcadas para revisar */
    if ($('ed-ai-go')) $('ed-ai-go').addEventListener('click', function () {
      var b = $('ed-ai-go'), old = b.textContent; b.disabled = true; b.textContent = T.ai_busy;
      json('/kb/api/ai', body('auto')).then(function (j) {
        b.disabled = false; b.textContent = old;
        if (!j.ok) { toast(j.error, 'error'); return; }
        Object.keys(j.fields).forEach(function (k) {
          if (!st[k].trim()) { st[k] = j.fields[k]; $('ed-' + k).value = j.fields[k]; if (st.ai.indexOf(k) < 0) st.ai.push(k); }
        });
        aiMarks(); change(); toast(T.ai_done);
      }).catch(function () { b.disabled = false; b.textContent = old; toast(T.err, 'error'); });
    });

    /* casos parecidos */
    var simT = null;
    function similar() {
      clearTimeout(simT);
      simT = setTimeout(function () {
        json('/kb/api/similar?exclude=' + (st.id || 0) + '&q=' + encodeURIComponent((st.title + ' ' + st.symptom).slice(0, 400))).then(function (j) {
          $('ed-similar').innerHTML = j.items.length ? j.items.map(function (c) {
            return '<a class="kb-mini" href="/kb/' + c.id + '" target="_blank" rel="noopener"><span class="m kb-id">' + esc(c.code) + '</span><span class="grow">' + esc(c.title) + '</span></a>';
          }).join('') : '<div style="margin-bottom:6px">' + esc(T.no_similar) + '</div>';
        }).catch(function () { /* sin sugerencias */ });
      }, 500);
    }

    paintCats(); devs(); steps(); reqs(); aiMarks(); similar();
  }

  /* mensaje tras enviar (la página siguiente lo muestra) */
  try { var m = sessionStorage.getItem('kb-msg'); if (m) { sessionStorage.removeItem('kb-msg'); document.addEventListener('DOMContentLoaded', function () { toast(m); }); } } catch (e) { /* sin almacenamiento */ }
})();
