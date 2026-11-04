/* Consola de un equipo (app/templates/console.html): terminal clásica, todo seguido, con colores por significado
   según el sistema; línea de comando fija con autocompletado (texto fantasma, menú, Tab, ↑ historial); frecuentes;
   guarda de comandos que cortan el servicio; solo lectura; buscar, copiar, descargar, temas y pantalla completa.
   Habla con /devices/<id>/console/ws?v=2 en JSON. Sin dependencias. */
(function () {
  'use strict';
  var C = window.CX, T = C.t, P = C.profile, OS = P.os;
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); };
  var cx = $('cx'), out = $('cx-out'), inp = $('cx-in'), ghost = $('cx-ghost'), sug = $('cx-sug');
  var store = function (k, v) { try { if (v === undefined) return localStorage.getItem('oc-cx-' + k); localStorage.setItem('oc-cx-' + k, v); } catch (e) { return null; } };

  /* ---------- colores por significado ---------- */
  var VERBS = /^(print|add|set|remove|enable|disable|export|monitor-traffic|monitor|reboot|reset-configuration|find|where|once|get|unset|edit|detail|terse)$/;
  var CMDW = {
    ros: null,
    ios: /^(show|sh|configure|conf|interface|int|reload|ping|traceroute|copy|write|no|terminal|line|router|ip|vlan|switchport|username|crypto|end|exit)$/,
    vrp: /^(display|dis|system-view|interface|undo|reboot|ping|tracert|save|quit|return|vlan|port)$/,
    junos: /^(show|configure|set|delete|commit|request|ping|traceroute|run|edit|exit)$/,
    exos: /^(show|configure|enable|disable|create|delete|unconfigure|reboot|ping|traceroute|save)$/
  };
  CMDW.nxos = CMDW.eos = CMDW.fastpath = CMDW.generic = CMDW.ios;
  var PIPE = /^(\||include|inc|section|sec|begin|exclude|count|last|match|no-more|display)$/;
  function span(c, t) { return '<span class="k-' + c + '">' + esc(t) + '</span>'; }
  function colorCmd(cmd) {
    return cmd.split(/(\s+)/).map(function (w) {
      if (!w || /^\s+$/.test(w)) return esc(w);
      if (OS === 'ros') {
        if (/^\//.test(w) || /^:/.test(w)) return span('path', w);
        var m = w.match(/^([a-z][\w-]*)(=|~|!=)(.*)$/i);
        if (m) return span('key', m[1]) + span('dim', m[2]) + span(/^\d{1,3}(\.\d{1,3}){3}/.test(m[3]) ? 'ip' : 'val', m[3]);
        if (/^\[|\]$/.test(w)) return span('mut', w);
        if (VERBS.test(w)) return span('verb', w);
        if (/^[a-z][\w-]*$/.test(w)) return span('path', w);
        return colorWord(w);
      }
      var re = CMDW[OS] || CMDW.generic;
      if (re.test(w.toLowerCase())) return span('path', w);
      if (PIPE.test(w)) return span('verb', w);
      return colorWord(w);
    }).join('');
  }
  function colorWord(w) {
    if (/^\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?$/.test(w)) return span('ip', w);
    if (/^(Gi|Te|Fa|Eth|GigabitEthernet|TenGigabitEthernet|FastEthernet|ether|sfp|ge-|xe-|Vlan|Po|Port-channel)\S*\d/i.test(w)) return span('key', w);
    return esc(w);
  }
  var RX = /(\b\d{1,3}(?:\.\d{1,3}){3}(?:\/\d{1,2})?\b)|((?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}|[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})|(-?\d+(?:\.\d+)?\s?(?:Mbps|kbps|Gbps|bps|dBm|MiB|KiB|GiB|ms|%))|\b(administratively down|disabled|warning|notconnect|err-disabled|inactive)\b|\b(up|RUNNING|running|connected|enabled|established|active|full|forwarding|stable|YES)\b|\b(down|DOWN|errors?|CRC|failed|failure|invalid|Invalid|bad|denied|unreachable|timeout|low)\b|(%[A-Z0-9_]+-\d-[A-Z0-9_]+)|\b([a-z][\w-]*)(=)("[^"]*"|\S+)/g;
  function colorOut(t) {
    if (/^\s*(%|bad command|syntax error|Error:|failure:|expected end of command|Unrecognized command|Invalid input)/i.test(t)) return span('bad', t);
    if (/^\s*[#!]/.test(t) && !/^\s*#\s*$/.test(t)) return span('com', t);
    if (/^\s*(Flags:|Columns:|Interface\s{2,}|Device ID|Port\s{2,}|Name\s{2,}|VLAN\s+Name|Protocol\s+Address|\s*#\s+[A-Z])/.test(t) ||
        (/^[\sA-Z#.-]{6,}$/.test(t) && /[A-Z]{3}/.test(t))) return span('head', t);
    var html = '', last = 0, m, fl;
    if (OS === 'ros' && (fl = t.match(/^(\s*\d+\s+)([RXDSIA;]+)(\s)/))) {
      html += span('mut', fl[1]) + span(/X/.test(fl[2]) ? 'warn' : 'ok', fl[2]) + esc(fl[3]); last = fl[0].length;
    }
    RX.lastIndex = last;
    while ((m = RX.exec(t))) {
      if (m.index > last) html += esc(t.slice(last, m.index));
      if (m[8]) html += span('key', m[8]) + span('dim', m[9]) + span(/^\d{1,3}(\.\d{1,3}){3}/.test(m[10]) ? 'ip' : 'val', m[10]);
      else html += span(m[1] ? 'ip' : m[2] ? 'val' : m[3] ? 'val' : m[4] ? 'warn' : m[5] ? 'ok' : m[6] ? 'bad' : 'verb', m[0]);
      last = m.index + m[0].length;
    }
    return html + esc(t.slice(last));
  }
  // prompt del equipo (+ lo que se escribió detrás): [admin@X] > · R1# · R1(config-if)# · <HUAWEI> · admin@sw:~$ · X.1 #
  var PROMPT = /^(\[[^\]\r\n]{1,80}\]\s?[>#]\s?|<[^>\s]{1,60}>|\[~?[^\]\s]{1,60}\]|[\w.\-\/@:() ~]{1,80}[#>$%]\s?)(.*)$/;
  function colorLine(t) {
    var m = t.match(PROMPT);
    if (m && m[1].length < 90 && !/\s{3,}/.test(m[1])) {
      var p = m[1], host = p.replace(/^[\[<]|[\]>#$%\s]+$/g, '').replace(/^~/, '');
      var i = p.indexOf(host);
      return span('dim', p.slice(0, i)) + span('host', host) + span('dim', p.slice(i + host.length)) + colorCmd(m[2]);
    }
    return colorOut(t);
  }

  /* ---------- salida: líneas completas y la última, que aún se escribe (normalmente el prompt) ---------- */
  var lines = [], cur = '', query = '', MAX = 6000;
  function strip(s) { return s.replace(/\x1b\[[0-9;?]*[ -\/]*[@-~]|\x1b[()][0-9A-Za-z]|\x1b[=>78]|\x07|\x1b\][^\x07]*\x07/g, ''); }
  function feed(text) {
    text = strip(text);
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (ch === '\n') { lines.push({t: cur}); cur = ''; }
      else if (ch === '\r') { if (text[i + 1] !== '\n') cur = ''; }
      else if (ch === '\b') cur = cur.slice(0, -1);
      else cur += ch;
    }
    if (lines.length > MAX) lines.splice(0, lines.length - MAX);
    render();
  }
  function noteLine(text, kind) { if (cur) { lines.push({t: cur}); cur = ''; } lines.push({t: text, n: kind || 'info'}); render(); }
  var pending = false;
  function render() {
    if (pending) return; pending = true;
    requestAnimationFrame(function () {
      pending = false;
      var atEnd = out.scrollHeight - out.scrollTop - out.clientHeight < 40, hits = 0;
      var html = lines.concat([{t: cur, live: true}]).map(function (l) {
        var h = l.n ? esc(l.t) : colorLine(l.t);
        if (query) { var r = mark(h); h = r.h; hits += r.n; }
        return '<div class="l' + (l.n ? ' n ' + l.n : '') + (!l.n && /^\s*(%|bad command|syntax error|Error:|failure:)/i.test(l.t) ? ' err' : '') + '">' + (h || '&#8203;') + '</div>';
      }).join('');
      out.innerHTML = html;
      if (query) $('cx-hits').textContent = T.hits.replace('{0}', hits);
      if (atEnd && !query) out.scrollTop = out.scrollHeight;
      updatePrompt();
    });
  }
  function mark(h) {  // resalta lo buscado sin romper las etiquetas
    var n = 0, q = esc(query).toLowerCase();
    var res = h.replace(/(<[^>]+>)|([^<]+)/g, function (all, tag, txt) {
      if (tag) return tag;
      var low = txt.toLowerCase(), o = '', i = 0, j;
      while ((j = low.indexOf(q, i)) >= 0) { o += txt.slice(i, j) + '<mark>' + txt.slice(j, j + q.length) + '</mark>'; i = j + q.length; n++; }
      return o + txt.slice(i);
    });
    return {h: res, n: n};
  }
  function updatePrompt() {
    var m = cur.match(PROMPT);
    $('cx-pr').textContent = m ? m[1].trim() : '›';
  }
  function plainText() { return lines.map(function (l) { return l.t; }).concat([cur]).join('\n'); }
  window.CXlines = function (n) { var a = lines.map(function (l) { return l.t; }); a.push(cur); return a.slice(-(n || 200)); };

  /* ---------- conexión ---------- */
  var ws = null, sid = null, waitingSecret = false;
  function state(s, cls) {
    var st = $('cx-st'); st.textContent = '● ' + T[s]; st.className = 'cx-st ' + (cls || '');
    if (C.embed && window.parent !== window) window.parent.postMessage({oc: 'console', state: s, nocred: C.nocred, sid: sid, dev: C.dev.id}, location.origin);
  }
  function connect() {
    if (ws && ws.readyState <= 1) ws.close();
    lines = []; cur = ''; sid = null; render(); state('connecting');
    var url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host +
      (C.watch ? '/console/watch/' + C.watch + '/ws' : '/devices/' + C.dev.id + '/console/ws?v=2&cols=200&rows=50');
    var sock = ws = new WebSocket(url), opened = false;
    sock.onopen = function () { opened = true; if (C.watch) { state('connected', 'ok'); noteLine(C.watchmsg, 'info'); } else inp.focus(); };
    sock.onmessage = function (e) {
      var m; try { m = JSON.parse(e.data); } catch (x) { feed(e.data); return; }
      if (m.t === 'o') feed(m.d);
      else if (m.t === 'n') noteLine(m.d, m.k);
      else if (m.t === 'p') { feed(m.d); waitingSecret = !!m.secret; inp.type = m.secret ? 'password' : 'text'; inp.focus(); }
      else if (m.t === 's') { sid = m.sid; state('connected', 'ok'); waitingSecret = false; inp.type = 'text'; }
      else if (m.t === 'g') guard(m.cmd, m.why);
      else if (m.t === 'w') watchers(m.who);
    };
    sock.onclose = function (e) {
      if (sock !== ws) return;
      state('disconnected', 'bad');
      if (!opened) noteLine(T.nows, 'bad');
      noteLine('[' + (e.code === 1008 ? T.denied : T.closed) + ']', 'info');
    };
  }
  function send(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }
  function watchers(who) { var w = $('cx-watch'); w.hidden = !who.length; w.textContent = T.watching.replace('{0}', who.join(', ')); }

  /* ---------- línea de comando ---------- */
  // historial por tipo de equipo (antes, por familia: en «generic» se mezclaban fabricantes distintos)
  var HK = 'h-' + (P.key || OS), hist = [];
  try { hist = JSON.parse(store(HK) || (OS !== 'generic' && store('h-' + OS)) || '[]'); } catch (e) { hist = []; }
  var hpos = -1, sel = 0, items = [];
  function run(cmd, force) {
    if (sid === null && ws && ws.readyState === 1) {  // usuario y contraseña del equipo antes de conectar
      send({t: 'c', d: cmd}); inp.value = ''; inp.type = 'text'; hideSug(); return;
    }
    if (cmd.trim() && hist[hist.length - 1] !== cmd) { hist.push(cmd); hist = hist.slice(-200); store(HK, JSON.stringify(hist)); }
    hpos = -1;
    send({t: 'c', d: cmd, force: !!force, ro: $('cx-ro').checked});
    inp.value = ''; hideSug(); showGhost();
  }
  function matches(v) {
    if (!v || waitingSecret) return [];
    var low = v.toLowerCase(), seen = {}, res = [];
    hist.slice().reverse().concat(P.suggest.map(function (s) { return s[0]; })).forEach(function (c) {
      if (seen[c] || c.toLowerCase().indexOf(low) !== 0 || c === v) return;
      seen[c] = 1;
      var d = P.suggest.find(function (s) { return s[0] === c; });
      res.push([c, d ? d[1] : '']);
    });
    return res.slice(0, 8);
  }
  function showGhost() {
    var v = inp.value, m = matches(v)[0];
    ghost.innerHTML = m ? '<span class="typed">' + esc(v) + '</span>' + esc(m[0].slice(v.length)) : '';
  }
  function showSug() {
    items = matches(inp.value); sel = 0;
    if (!items.length) { hideSug(); return; }
    sug.innerHTML = items.map(function (s, i) { return '<button type="button" role="option" class="' + (i === sel ? 'on' : '') + '" data-i="' + i + '">' + colorCmd(s[0]) + '<small>' + esc(s[1]) + '</small></button>'; }).join('') +
      '<div class="hint">Tab · ↑↓ · ' + esc(P.label || '') + '</div>';
    sug.hidden = false;
  }
  function hideSug() { sug.hidden = true; items = []; }
  function pick(i) { if (items[i]) { inp.value = items[i][0]; hideSug(); showGhost(); inp.focus(); } }
  sug.addEventListener('mousedown', function (e) { var b = e.target.closest('button'); if (b) { e.preventDefault(); pick(+b.dataset.i); } });
  inp.addEventListener('input', function () { showGhost(); showSug(); });
  inp.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); if (!sug.hidden && items.length && sel > 0) { pick(sel); return; } run(inp.value); }
    else if (e.key === 'Tab') { e.preventDefault(); var m = items[sel] || matches(inp.value)[0]; if (m) { inp.value = m[0]; hideSug(); showGhost(); } }
    else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      e.preventDefault();
      if (!sug.hidden && items.length) {
        sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        Array.prototype.forEach.call(sug.querySelectorAll('button'), function (b, i) { b.classList.toggle('on', i === sel); });
        return;
      }
      if (!hist.length) return;
      hpos = e.key === 'ArrowUp' ? (hpos < 0 ? hist.length - 1 : Math.max(0, hpos - 1)) : (hpos < 0 ? -1 : hpos + 1);
      if (hpos >= hist.length) hpos = -1;
      inp.value = hpos < 0 ? '' : hist[hpos]; showGhost();
    }
    else if (e.key === 'Escape') hideSug();
    else if (e.key === 'c' && e.ctrlKey && !inp.value) { e.preventDefault(); send({t: 'd', d: '\x03'}); }
  });
  inp.addEventListener('blur', function () { setTimeout(hideSug, 120); });
  $('cx-run').addEventListener('click', function () { run(inp.value); inp.focus(); });
  $('cx-tab').addEventListener('click', function () { var m = matches(inp.value)[0]; if (m) { inp.value = m[0]; showGhost(); } inp.focus(); });
  $('cx-hist').addEventListener('click', function () { inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowUp'})); inp.focus(); });
  $('cx-freq').innerHTML = P.frequent.map(function (f) { return '<button type="button" class="' + (f[1] ? 'dz' : '') + '" data-c="' + esc(f[0]) + '">' + esc(f[0]) + '</button>'; }).join('');
  $('cx-freq').addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) { run(b.dataset.c); inp.focus(); } });
  out.addEventListener('click', function () { if (!getSelection().toString()) inp.focus(); });

  /* ---------- guarda de comandos que cortan el servicio ---------- */
  var gcmd = null;
  function guard(cmd, why) { gcmd = cmd; $('cx-g-cmd').textContent = cmd; $('cx-g-why').textContent = why; $('cx-guard').hidden = false; $('cx-g-no').focus(); }
  $('cx-g-no').addEventListener('click', function () { $('cx-guard').hidden = true; gcmd = null; inp.focus(); });
  $('cx-g-yes').addEventListener('click', function () { $('cx-guard').hidden = true; if (gcmd) run(gcmd, true); gcmd = null; inp.focus(); });
  if (C.mode === 'ro') $('cx-ro').title = T.rolocked;

  /* ---------- barra superior ---------- */
  function toast(t) { var el = $('cx-toast'); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(function () { el.hidden = true; }, 3200); }
  window.CXtoast = toast;
  $('cx-find').addEventListener('click', function () { var s = $('cx-search'); s.hidden = !s.hidden; if (!s.hidden) $('cx-q').focus(); else { query = ''; render(); } });
  $('cx-q').addEventListener('input', function () { query = this.value.trim(); render(); });
  $('cx-q-x').addEventListener('click', function () { $('cx-search').hidden = true; query = ''; render(); });
  var fs = +(store('fs') || 13);
  function setFs(v) { fs = Math.max(10, Math.min(22, v)); cx.style.setProperty('--cx-fs', fs + 'px'); store('fs', fs); }
  setFs(fs);
  $('cx-fs-').addEventListener('click', function () { setFs(fs - 1); });
  $('cx-fs+').addEventListener('click', function () { setFs(fs + 1); });
  $('cx-copy').addEventListener('click', function () { navigator.clipboard.writeText(plainText()).then(function () { toast(T.copied); }); });
  $('cx-dl').addEventListener('click', function () {
    var a = document.createElement('a'), d = new Date();
    a.href = URL.createObjectURL(new Blob([plainText()], {type: 'text/plain'}));
    a.download = C.dev.name + '-' + d.toISOString().slice(0, 16).replace(/[:T]/g, '') + '.txt'; a.click();
  });
  var theme = +(store('theme') || 0);
  function setTheme(i) {
    theme = i % 3; cx.dataset.theme = theme; $('cx-theme-n').textContent = T.themes[theme]; store('theme', theme);
    $('cx-theme').title = T.themes[theme];
  }
  setTheme(theme);
  $('cx-theme').addEventListener('click', function () { setTheme(theme + 1); toast(T.themes[theme]); });
  $('cx-recon').addEventListener('click', connect);
  $('cx-full').addEventListener('click', function () { (cx.requestFullscreen ? cx.requestFullscreen() : Promise.resolve()); });
  $('cx-share').addEventListener('click', function () {
    if (sid === null) { toast(T.nosess); return; }
    fetch('/console/sessions/' + sid + '/share', {method: 'POST', credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (j) {
      if (!j.url) { toast(j.error || T.nosess); return; }
      navigator.clipboard.writeText(location.origin + j.url).then(function () { toast(T.share); }, function () { toast(location.origin + j.url); });
    });
  });
  var aiBtn = $('cx-ai');
  if (aiBtn) aiBtn.addEventListener('click', function () {
    var p = document.getElementById('ai-panel'), on = p.hidden; p.hidden = !on; aiBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    store('ai', on ? '1' : '0'); if (on && window.ocAi) window.ocAi.focus();
  });
  if (aiBtn && store('ai') === '1' && !C.embed) aiBtn.click();
  window.CXinsert = function (text) { inp.value = text; showGhost(); inp.focus(); };
  try { var pre = new URLSearchParams(location.search).get('cmd'); if (pre) window.CXinsert(pre.split('\n')[0]); } catch (e) { /* sin parámetro */ }
  window.CXcolor = colorCmd;
  window.addEventListener('message', function (e) {  // diálogo de la página o centro de consola
    if (e.origin !== location.origin || !e.data || e.data.oc !== 'console') return;
    if (e.data.cmd === 'reconnect') connect();
    if (e.data.cmd === 'fit') inp.focus();
    if (e.data.cmd === 'insert') window.CXinsert(e.data.text || '');
    if (e.data.cmd === 'run' && e.data.text) e.data.text.split('\n').forEach(function (l) { if (l.trim()) run(l); });
    if (e.data.cmd === 'share') $('cx-share').click();
    if (e.data.cmd === 'find') $('cx-find').click();
    if (e.data.cmd === 'theme') $('cx-theme').click();
    if (e.data.cmd === 'ai' && aiBtn) aiBtn.click();  // botón «Asistente» del panel de Operar › Consola
    if (e.data.cmd === 'fs-' || e.data.cmd === 'fs+') $('cx-' + e.data.cmd).click();  // letra, desde Operar › Consola
  });
  if (!C.embed) document.title = C.dev.name + ' · consola';
  connect();
})();
