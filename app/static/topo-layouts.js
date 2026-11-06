/* Distribuciones del Mapa de la red (menú «Distribución»). Solo calculan posiciones: el dibujo está en topology.html.
   compute(modo, nodos, enlaces, opciones) devuelve:
     pos   {id: {x, y, lab: 'below'|'right'|'left'|'above', sc}}   posición, lado del nombre y tamaño del círculo
     decor [{t: 'text'|'ellipse'|'rect'|'line'|'pill'|'image', ...}] dibujos de fondo (filas, anillos, recuadros…)
     route 'line'|'down'|'right'|'center'|'cols'                    cómo se dibujan los enlaces
     tree  Set de enlaces del árbol (los demás van como curvas punteadas)
     hideN / hideE  Set de equipos y enlaces que no se dibujan
     fold  {id: {n, open}}                                           píldoras del árbol plegable
   Los modos: free (1), down (2), right (3), radial (4), sites (5), roles (6), circle (7), fold (8), plan (9), mine (10). */
(function () {
  'use strict';
  const LW = n => Math.max(40, String(n.label || '').length * 7 + 8);  // ancho aproximado del nombre (12 px)
  const ek = e => e.a + '|' + e.b;

  function graph(nodes, links) {
    const adj = {}, deg = {};
    nodes.forEach(n => { adj[n.id] = []; deg[n.id] = 0; });
    links.forEach(e => {
      if (!(e.a in adj) || !(e.b in adj)) return;
      adj[e.a].push(e.b); adj[e.b].push(e.a); deg[e.a]++; deg[e.b]++;
    });
    Object.values(adj).forEach(l => l.sort());
    return {adj, deg};
  }
  function comps(nodes, adj) {
    const seen = new Set(), out = [];
    nodes.forEach(n => {
      if (seen.has(n.id)) return;
      const c = [], q = [n.id]; seen.add(n.id);
      while (q.length) { const x = q.shift(); c.push(x); adj[x].forEach(y => { if (!seen.has(y)) { seen.add(y); q.push(y); } }); }
      out.push(c);
    });
    return out.sort((a, b) => b.length - a.length);
  }
  function central(ids, deg, byId) {
    return ids.slice().sort((a, b) => (deg[b] - deg[a]) || String(byId[a].label).localeCompare(byId[b].label))[0];
  }
  // árbol por saltos (BFS) desde la raíz, solo con los equipos permitidos
  function bfs(root, adj, allow) {
    const parent = {[root]: null}, depth = {[root]: 0}, kids = {[root]: []}, order = [root], q = [root];
    while (q.length) {
      const x = q.shift();
      adj[x].forEach(y => {
        if (y in parent || (allow && !allow.has(y))) return;
        parent[y] = x; depth[y] = depth[x] + 1; kids[y] = []; kids[x].push(y); order.push(y); q.push(y);
      });
    }
    return {root, parent, depth, kids, order};
  }
  function treeEdges(t, links) {
    const s = new Set();
    links.forEach(e => { if (t.parent[e.a] === e.b || t.parent[e.b] === e.a) s.add(ek(e)); });
    return s;
  }
  // árbol prolijo: las hojas en fila, cada padre al medio de sus hijos (slot = espacio entre hojas)
  function tidy(t, slot) {
    const at = {}; let cur = 0;
    (function walk(x) {
      const k = t.kids[x];
      if (!k.length) { at[x] = cur; cur += slot; return; }
      k.forEach(walk);
      at[x] = (at[k[0]] + at[k[k.length - 1]]) / 2;
    })(t.root);
    return {at, span: cur};
  }
  function bounds(ids, pos) {
    const xs = ids.map(i => pos[i].x), ys = ids.map(i => pos[i].y);
    return {x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys)};
  }
  // las redes sueltas (sin enlace con la principal) se ponen debajo, cada una como árbol pequeño
  function placeRest(rest, G, byId, pos, below, center) {
    let x = below.x0, y = below.y1 + 170, rowH = 0;
    const maxW = Math.max(900, below.x1 - below.x0);
    rest.forEach(c => {
      const t = bfs(central(c, G.deg, byId), G.adj, new Set(c)), td = tidy(t, 150);
      const w = Math.max(td.span, 150), h = (Math.max(...c.map(i => t.depth[i])) + 1) * 120;
      if (x > below.x0 && x + w > below.x0 + maxW) { x = below.x0; y += rowH + 80; rowH = 0; }
      c.forEach(i => { pos[i] = {x: x + td.at[i], y: y + t.depth[i] * 120, lab: 'below', sc: 1}; });
      x += w + 90; rowH = Math.max(rowH, h);
    });
  }

  // 2 y 8: escalonada hacia abajo (un salto por fila) y árbol plegable
  function down(nodes, links, G, byId, opt, foldOpen) {
    const cs = comps(nodes, G.adj), main = cs[0], root = opt.center && main.includes(opt.center) ? opt.center : central(main, G.deg, byId);
    let allow = new Set(main), fold = null, hideN = new Set();
    let t = bfs(root, G.adj, allow);
    if (foldOpen) {  // solo se ven el central y lo que cuelga de las ramas abiertas
      const vis = new Set([root]);
      (function open(x) { if (!foldOpen.has(x)) return; t.kids[x].forEach(k => { vis.add(k); open(k); }); })(root);
      fold = {};
      t.order.forEach(x => { if (vis.has(x) && t.kids[x].length) fold[x] = {n: count(t, x), open: foldOpen.has(x)}; });
      nodes.forEach(n => { if (!vis.has(n.id)) hideN.add(n.id); });
      const full = t; t = {root, parent: {}, depth: {}, kids: {}, order: []};
      full.order.forEach(x => { if (!vis.has(x)) return; t.parent[x] = full.parent[x]; t.depth[x] = full.depth[x]; t.order.push(x);
        t.kids[x] = foldOpen.has(x) ? full.kids[x].slice() : []; });
      allow = vis;
    }
    const RH = 140, td = tidy(t, foldOpen ? 160 : 100), pos = {};
    t.order.forEach(x => { pos[x] = {x: td.at[x], y: t.depth[x] * RH, lab: 'below', sc: 1}; });
    // fila apretada: los equipos en posición impar bajan para que los nombres no se pisen
    const rows = {};
    t.order.forEach(x => { (rows[t.depth[x]] = rows[t.depth[x]] || []).push(x); });
    Object.values(rows).forEach(r => {
      r.sort((a, b) => pos[a].x - pos[b].x);
      for (let i = 1; i < r.length; i++) {
        if (pos[r[i]].x - pos[r[i - 1]].x < 140 && pos[r[i - 1]].y === pos[r[0]].y) pos[r[i]].y += 52;
      }
    });
    const decor = [], b = bounds(t.order, pos), maxD = Math.max(...t.order.map(x => t.depth[x]));
    for (let d = 1; d <= maxD; d++) decor.push({t: 'text', cls: 'lay-row', x: b.x0 - 110, y: d * RH + 4, txt: d === 1 ? opt.T.hop1 : opt.T.hops.replace('{0}', d), anchor: 'end'});
    if (!foldOpen) placeRest(cs.slice(1), G, byId, pos, b);
    else nodes.forEach(n => { if (!(n.id in pos)) pos[n.id] = {x: 0, y: 0, lab: 'below', sc: 1}; });
    return {pos, decor, route: 'down', tree: treeEdges(t, links), hideN, hideE: new Set(), fold, center: root};
  }
  function count(t, x) { let n = 0; (function w(y) { t.kids[y].forEach(k => { n++; w(k); }); })(x); return n; }

  // 3: escalonada de izquierda a derecha, con el nombre al lado
  function right(nodes, links, G, byId, opt) {
    const cs = comps(nodes, G.adj), main = cs[0], root = opt.center && main.includes(opt.center) ? opt.center : central(main, G.deg, byId);
    const t = bfs(root, G.adj, new Set(main)), td = tidy(t, 58), pos = {};
    const maxD = Math.max(...t.order.map(x => t.depth[x])), colX = [0];
    for (let d = 1; d <= maxD; d++) {
      const prev = t.order.filter(x => t.depth[x] === d - 1).map(x => LW(byId[x]));
      colX[d] = colX[d - 1] + 38 + Math.max(...prev) + 90;
    }
    t.order.forEach(x => { pos[x] = {x: colX[t.depth[x]], y: td.at[x], lab: 'right', sc: 1}; });
    const decor = [], b = bounds(t.order, pos);
    for (let d = 1; d <= maxD; d++) decor.push({t: 'text', cls: 'lay-row', x: colX[d], y: b.y0 - 44, txt: d === 1 ? opt.T.hop1 : opt.T.hops.replace('{0}', d), anchor: 'start'});
    placeRest(cs.slice(1), G, byId, pos, b);
    return {pos, decor, route: 'right', tree: treeEdges(t, links), hideN: new Set(), hideE: new Set(), center: root};
  }

  // 4: radial, un anillo por salto; cada rama en su sector (el ángulo es el promedio del de sus hojas)
  function radial(nodes, links, G, byId, opt) {
    const cs = comps(nodes, G.adj), main = cs[0], root = opt.center && main.includes(opt.center) ? opt.center : central(main, G.deg, byId);
    const t = bfs(root, G.adj, new Set(main)), leaves = t.order.filter(x => !t.kids[x].length && x !== root);
    const L = Math.max(leaves.length, 1), maxD = Math.max(1, ...t.order.map(x => t.depth[x]));
    // cada hoja necesita unos 150 px en su anillo; el primer anillo no baja de 160
    const RR = Math.max(160, Math.min(400, L * 125 / (2 * Math.PI))), ang = {};
    let i = 0;
    (function walk(x) {
      const k = t.kids[x];
      if (!k.length) { ang[x] = 2 * Math.PI * (i++) / L - Math.PI / 2; return; }
      k.forEach(walk);
      ang[x] = k.reduce((s, y) => s + ang[y], 0) / k.length;
    })(root);
    const pos = {};
    t.order.forEach(x => {
      const r = t.depth[x] * RR;
      pos[x] = {x: Math.cos(ang[x]) * r * 1.35, y: Math.sin(ang[x]) * r, lab: 'below', sc: x === root ? 1.3 : 1};
    });
    const decor = [];
    for (let d = 1; d <= maxD; d++) decor.push({t: 'ellipse', cls: 'lay-ring', cx: 0, cy: 0, rx: d * RR * 1.35, ry: d * RR});
    placeRest(cs.slice(1), G, byId, pos, bounds(t.order, pos));
    return {pos, decor, route: 'line', tree: null, hideN: new Set(), hideE: new Set(), center: root};
  }

  // 6: columnas por función
  function roles(nodes, links, G, byId, opt) {
    const col = n => ['router', 'firewall'].includes(n.role) ? 0 : n.role === 'switch' ? 1 : n.role === 'ap' ? 2 : 3;
    const cs = comps(nodes, G.adj), order = {};
    let k = 0;
    cs.forEach(c => { bfs(central(c, G.deg, byId), G.adj, new Set(c)).order.forEach(x => { order[x] = k++; }); });
    const groups = [[], [], [], []];
    nodes.forEach(n => groups[col(n)].push(n.id));
    const used = groups.map((g, i) => [g, i]).filter(([g]) => g.length);
    const pos = {}, decor = [];
    let x = 0;
    used.forEach(([g, i], ci) => {
      g.sort((a, b) => order[a] - order[b]);
      const w = Math.max(...g.map(id => LW(byId[id])));
      if (i === 0) x += w + 40;  // el nombre de los routers va a la izquierda
      g.forEach((id, j) => { pos[id] = {x, y: j * 68, lab: i === 0 ? 'left' : 'right', sc: 1, col: ci}; });
      decor.push({t: 'text', cls: 'lay-col', x: i === 0 ? x : x - 24, y: -50, txt: opt.T.cols[i] + ' · ' + g.length, anchor: i === 0 ? 'end' : 'start'});
      x += (i === 0 ? 260 : w + 300);
    });
    return {pos, decor, route: 'cols', tree: null, hideN: new Set(), hideE: new Set()};
  }

  // 7: todos en un círculo, en el orden de sus ramas; el círculo crece con la cantidad de enlaces
  function circle(nodes, links, G, byId, opt) {
    const cs = comps(nodes, G.adj), ord = [];
    cs.forEach((c, i) => {
      const root = i === 0 && opt.center && c.includes(opt.center) ? opt.center : central(c, G.deg, byId), t = bfs(root, G.adj, new Set(c));
      (function dfs(x) { ord.push(x); t.kids[x].forEach(dfs); })(root);
    });
    const N = ord.length, R = Math.max(260, N * 100 / (2 * Math.PI)), pos = {};
    ord.forEach((id, i) => {
      const a = 2 * Math.PI * i / N - Math.PI / 2, c = Math.cos(a), s = Math.sin(a);
      pos[id] = {x: c * R, y: s * R, sc: 1 + Math.min(G.deg[id], 10) * 0.05,
                 lab: c > 0.35 ? 'right' : c < -0.35 ? 'left' : s < 0 ? 'above' : 'below'};
    });
    return {pos, decor: [], route: 'center', tree: null, hideN: new Set(), hideE: new Set()};
  }

  // 5: un recuadro por sitio (la parte del nombre que se repite o la red); entre sitios una sola línea con la cantidad
  const GENERIC = /^[A-Z]{1,3}\d*$/;
  function words(label) {
    return String(label || '').split(/[\s\-_.]+/).filter(Boolean).filter(w => !GENERIC.test(w)).map(w => w.replace(/\d+$/, '')).filter(Boolean);
  }
  function siteOf(nodes, G, byId, opt) {
    const two = {}, one = {}, net = {}, out = {};
    const k2 = n => { const w = words(n.label); return w.length > 1 ? (w[0] + ' ' + w[1]).toLowerCase() : ''; };
    const k1 = n => { const w = words(n.label); return w.length ? w[0].toLowerCase() : ''; };
    const kn = n => { const m = /^(\d+\.\d+\.\d+)\.\d+$/.exec(n.ip || (n.kind === 'dev' ? n.sub : '') || ''); return m ? m[1] + '.0/24' : ''; };
    nodes.forEach(n => { [[two, k2(n)], [one, k1(n)], [net, kn(n)]].forEach(([m, k]) => { if (k) m[k] = (m[k] || 0) + 1; }); });
    const disp = {};
    nodes.forEach(n => {
      let key = '';
      if (k2(n) && two[k2(n)] > 1) key = k2(n);
      else if (k1(n) && one[k1(n)] > 1) key = k1(n);
      else if (kn(n) && net[kn(n)] > 1) key = kn(n);
      if (key && !disp[key]) {
        const w = words(n.label), cut = key.split(' ').length;
        disp[key] = /\//.test(key) ? key : w.slice(0, cut).join(' ').replace(/^./, c => c.toUpperCase());
      }
      out[n.id] = key;
    });
    const center = central(nodes.map(n => n.id), G.deg, byId);
    const coreKey = '_core';
    nodes.forEach(n => { if (!out[n.id]) out[n.id] = coreKey; });
    disp[coreKey] = opt.T.core;
    // nombres y equipos cambiados por el administrador
    const S = opt.sites || {}, ren = S.names || {}, mov = S.assign || {};
    Object.keys(mov).forEach(id => { if (id in out) { out[id] = 'm:' + mov[id]; disp['m:' + mov[id]] = mov[id]; } });
    Object.keys(disp).forEach(k => { if (ren[k]) disp[k] = ren[k]; });
    return {of: out, disp, center};
  }
  function sites(nodes, links, G, byId, opt) {
    const s = siteOf(nodes, G, byId, opt), groups = {};
    nodes.forEach(n => { (groups[s.of[n.id]] = groups[s.of[n.id]] || []).push(n.id); });
    const keys = Object.keys(groups).sort((a, b) => groups[b].length - groups[a].length || String(s.disp[a]).localeCompare(s.disp[b]));
    const pos = {}, decor = [], box = {};
    let x = 0, y = 0, rowH = 0;
    const MAXW = 1500, CW = 170, CH = 116;
    keys.forEach(k => {
      const ids = groups[k], cols = Math.max(1, Math.min(4, Math.ceil(Math.sqrt(ids.length * 1.6)))), rows = Math.ceil(ids.length / cols);
      const w = cols * CW + 40, h = rows * CH + 60;
      if (x > 0 && x + w > MAXW) { x = 0; y += rowH + 70; rowH = 0; }
      // dentro del sitio: el árbol por saltos ordena a los equipos para que los enlazados queden cerca
      const t = bfs(central(ids, G.deg, byId), G.adj, new Set(ids)), ord = t.order.concat(ids.filter(i => !t.order.includes(i)));
      ord.forEach((id, i) => { pos[id] = {x: x + 20 + CW / 2 + (i % cols) * CW, y: y + 64 + Math.floor(i / cols) * CH, lab: 'below', sc: 1}; });
      box[k] = {x, y, w, h};
      decor.push({t: 'rect', cls: 'lay-site', x, y, w, h, rx: 14});
      decor.push({t: 'text', cls: 'lay-site-t', x: x + 16, y: y + 24, txt: s.disp[k], site: k});
      decor.push({t: 'text', cls: 'lay-site-n', x: x + 16, y: y + 41, txt: ids.length === 1 ? opt.T.dev1 : opt.T.devs.replace('{0}', ids.length)});
      x += w + 60; rowH = Math.max(rowH, h);
    });
    const hideE = new Set(), agg = {};
    links.forEach(e => {
      const sa = s.of[e.a], sb = s.of[e.b];
      if (sa === undefined || sb === undefined || sa === sb) return;
      hideE.add(ek(e));
      const k = [sa, sb].sort().join('§'); agg[k] = (agg[k] || 0) + 1;
    });
    Object.entries(agg).forEach(([k, n]) => {
      const [a, b] = k.split('§'), A = box[a], B = box[b];
      const ax = A.x + A.w / 2, ay = A.y + A.h / 2, bx = B.x + B.w / 2, by = B.y + B.h / 2;
      const clip = (bx_, X, Y, tx, ty) => {  // del centro del recuadro a su borde, hacia el otro
        const dx = tx - X, dy = ty - Y, sx = dx ? (bx_.w / 2) / Math.abs(dx) : Infinity, sy = dy ? (bx_.h / 2) / Math.abs(dy) : Infinity, f = Math.min(sx, sy, 1);
        return [X + dx * f, Y + dy * f];
      };
      const p = clip(A, ax, ay, bx, by), q = clip(B, bx, by, ax, ay);
      decor.push({t: 'line', cls: 'lay-agg', x1: p[0], y1: p[1], x2: q[0], y2: q[1], w: 1.5 + Math.min(n, 8) * 0.9});
      decor.push({t: 'pill', x: (p[0] + q[0]) / 2, y: (p[1] + q[1]) / 2, txt: n === 1 ? opt.T.link1 : opt.T.links.replace('{0}', n)});
    });
    return {pos, decor, route: 'line', tree: null, hideN: new Set(), hideE, site: s};
  }

  // 1: libre (fuerzas, la de siempre: la calcula topology.html) y un paso final que separa los nombres que se pisan
  function separate(nodes, pos) {
    const ids = nodes.map(n => n.id).filter(id => pos[id]);
    for (let it = 0; it < 40; it++) {
      let moved = false;
      for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
        const a = pos[ids[i]], b = pos[ids[j]];
        if (Math.abs(a.y - b.y) > 30) continue;
        const wa = LW(nodes.find(n => n.id === ids[i])), wb = LW(nodes.find(n => n.id === ids[j]));
        const need = (wa + wb) / 2 + 8, d = b.x - a.x;
        if (Math.abs(d) >= need) continue;
        const push = (need - Math.abs(d)) / 2 + 1, s = d >= 0 ? 1 : -1;
        a.x -= push * s; b.x += push * s; moved = true;
      }
      if (!moved) break;
    }
  }

  // 9: sobre un plano (posiciones guardadas en fracciones del plano); los que no tienen esperan en una bandeja
  function plan(nodes, links, G, byId, opt) {
    const P = opt.plan || {}, PW = 1600, PH = P.w && P.h ? PW * P.h / P.w : 1000, pos = {}, decor = [], tray = [];
    decor.push(P.url ? {t: 'image', href: P.url, x: 0, y: 0, w: PW, h: PH} : {t: 'rect', cls: 'lay-plan', x: 0, y: 0, w: PW, h: PH, rx: 8});
    if (!P.url) decor.push({t: 'text', cls: 'lay-plan-t', x: PW / 2, y: PH / 2, txt: opt.T.noplan, anchor: 'middle'});
    nodes.forEach(n => {
      const p = (P.pos || {})[n.id];
      if (p) pos[n.id] = {x: p[0] * PW, y: p[1] * PH, lab: 'below', sc: 1}; else tray.push(n.id);
    });
    const hideE = new Set();
    if (tray.length) {
      const TX = PW + 60, cols = 2, CW = 170, CH = 104, th = Math.ceil(tray.length / cols) * CH + 70;
      decor.push({t: 'rect', cls: 'lay-tray', x: TX, y: 0, w: cols * CW + 30, h: th, rx: 12});
      decor.push({t: 'text', cls: 'lay-site-t', x: TX + 16, y: 24, txt: opt.T.tray.replace('{0}', tray.length)});
      decor.push({t: 'text', cls: 'lay-site-n', x: TX + 16, y: 41, txt: opt.T.tray_h});
      tray.forEach((id, i) => { pos[id] = {x: TX + 15 + CW / 2 + (i % cols) * CW, y: 70 + Math.floor(i / cols) * CH, lab: 'below', sc: 1, tray: true}; });
      const ts = new Set(tray);
      links.forEach(e => { if (ts.has(e.a) || ts.has(e.b)) hideE.add(ek(e)); });
    }
    return {pos, decor, route: 'line', tree: null, hideN: new Set(), hideE, plan: {w: PW, h: PH, tray: tray.length}};
  }

  function compute(mode, nodes, links, opt) {
    const G = graph(nodes, links), byId = {};
    nodes.forEach(n => { byId[n.id] = n; });
    const base = {decor: [], route: 'line', tree: null, hideN: new Set(), hideE: new Set()};
    if (!nodes.length) return Object.assign(base, {pos: {}});
    if (mode === 'down') return down(nodes, links, G, byId, opt, null);
    if (mode === 'fold') return down(nodes, links, G, byId, opt, opt.foldOpen || new Set());
    if (mode === 'right') return right(nodes, links, G, byId, opt);
    if (mode === 'radial') return radial(nodes, links, G, byId, opt);
    if (mode === 'sites') return sites(nodes, links, G, byId, opt);
    if (mode === 'roles') return roles(nodes, links, G, byId, opt);
    if (mode === 'circle') return circle(nodes, links, G, byId, opt);
    if (mode === 'plan') return plan(nodes, links, G, byId, opt);
    // free y mine: posiciones que vienen de fuera (fuerzas o lo guardado)
    const pos = {};
    nodes.forEach(n => { pos[n.id] = {x: n.fx, y: n.fy, lab: 'below', sc: 1}; });
    if (mode === 'free') separate(nodes, pos);
    if (mode === 'mine') {
      const M = opt.mine || {};
      nodes.forEach(n => { const p = M[n.id]; if (p) { pos[n.id].x = p[0]; pos[n.id].y = p[1]; pos[n.id].pin = true; } });
    }
    return Object.assign(base, {pos});
  }

  // trazo de un enlace según la distribución (a y b con x, y, lab y el ancho del nombre)
  function path(route, a, b, inTree, ctr) {
    if (route === 'down' && inTree) {
      const [p, c] = a.y <= b.y ? [a, b] : [b, a], my = p.y + (c.y - p.y) / 2;
      return `M${p.x},${p.y}V${my}H${c.x}V${c.y}`;
    }
    if (route === 'right' && inTree) {
      const [p, c] = a.x <= b.x ? [a, b] : [b, a], sx = p.x + 38 + LW(p) + 6, ex = c.x - 26, mx = (sx + ex) / 2;
      return `M${sx},${p.y}H${mx}V${c.y}H${ex}`;
    }
    if ((route === 'down' || route === 'right') && !inTree) {  // redundantes: curva punteada
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
      return `M${a.x},${a.y}Q${mx - dy / d * 50},${my + dx / d * 50} ${b.x},${b.y}`;
    }
    if (route === 'center') {
      const c = ctr || {x: 0, y: 0}, mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      return `M${a.x},${a.y}Q${c.x + (mx - c.x) * 0.35},${c.y + (my - c.y) * 0.35} ${b.x},${b.y}`;
    }
    if (route === 'cols') {
      if (a.col === b.col) {  // misma columna: arco al costado, del lado contrario al nombre
        const side = a.lab === 'left' ? 1 : -1, off = side * (40 + Math.abs(a.y - b.y) * 0.25);
        return `M${a.x},${a.y}C${a.x + off},${a.y} ${b.x + off},${b.y} ${b.x},${b.y}`;
      }
      const [l, r] = a.x <= b.x ? [a, b] : [b, a], sx = l.x + (l.lab === 'right' ? 38 + LW(l) + 6 : 24), ex = r.x - 24, mx = (sx + ex) / 2;
      return `M${sx},${l.y}C${mx},${l.y} ${mx},${r.y} ${ex},${r.y}`;
    }
    return `M${a.x},${a.y}L${b.x},${b.y}`;
  }

  window.TopoLayouts = {compute, path, siteOf: (nodes, links, opt) => {
    const G = graph(nodes, links), byId = {}; nodes.forEach(n => { byId[n.id] = n; }); return siteOf(nodes, G, byId, opt);
  }, LW};
})();
