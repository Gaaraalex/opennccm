/* Red animada (portada del panel y pantalla de ingreso), igual que en el prototipo de diseño.
   Uso: <canvas data-net> (portada: nodos solo en la parte derecha) o <canvas data-net="big"> (toda la superficie). */
(function () {
  'use strict';
  var RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
  window.ocNet = function (c, big) {
    if (!c) return;
    var raf = 0;
    function draw() {
      cancelAnimationFrame(raf);
      var ctx = c.getContext('2d'), dpr = Math.min(2, devicePixelRatio || 1);
      var W = c.clientWidth, H = c.clientHeight;
      if (!W) return;
      c.width = W * dpr; c.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var sig = getComputedStyle(document.documentElement).getPropertyValue('--signal').trim() || '#c6f432';
      var line = 'rgba(255,255,255,.09)', node = 'rgba(255,255,255,.22)';
      var seed = 7, rnd = function () { return (seed = (seed * 16807) % 2147483647) / 2147483647; };
      var N = big ? 26 : 18, x0 = big ? 0 : 0.45 * W, pts = [];
      for (var i = 0; i < N; i++) pts.push({x: x0 + rnd() * (W - x0), y: rnd() * H, r: 2 + rnd() * 3, vx: (rnd() - 0.5) * 0.15, vy: (rnd() - 0.5) * 0.15});
      var lit = [2, 7, 11], pulses = [{a: 2, b: 5, t: 0}, {a: 7, b: 11, t: 0.5}];
      function frame() {
        ctx.clearRect(0, 0, W, H);
        for (var i = 0; i < N; i++) {
          for (var j = i + 1; j < N; j++) {
            var d = Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y);
            if (d < (big ? 170 : 140)) {
              ctx.strokeStyle = line; ctx.lineWidth = 1.2;
              ctx.beginPath(); ctx.moveTo(pts[i].x, pts[i].y); ctx.lineTo(pts[j].x, pts[j].y); ctx.stroke();
            }
          }
        }
        pts.forEach(function (p, i) {
          var on = lit.indexOf(i) >= 0;
          ctx.fillStyle = on ? sig : node;
          ctx.beginPath(); ctx.arc(p.x, p.y, on ? p.r + 2 : p.r, 0, 7); ctx.fill();
          if (!RM) { p.x += p.vx; p.y += p.vy; if (p.y < 0 || p.y > H) p.vy *= -1; if (p.x < x0 || p.x > W) p.vx *= -1; }
        });
        if (!RM) {
          pulses.forEach(function (p) {
            p.t += 0.006;
            if (p.t > 1) { p.t = 0; p.a = lit[Math.floor(rnd() * 3)]; p.b = Math.floor(rnd() * N); }
            var a = pts[p.a], b = pts[p.b];
            ctx.fillStyle = sig; ctx.globalAlpha = 1 - p.t;
            ctx.beginPath(); ctx.arc(a.x + (b.x - a.x) * p.t, a.y + (b.y - a.y) * p.t, 2.5, 0, 7); ctx.fill();
            ctx.globalAlpha = 1;
          });
        }
        if (!RM && c.isConnected) raf = requestAnimationFrame(frame);
      }
      frame();
    }
    draw();
    addEventListener('resize', draw);
  };
  document.querySelectorAll('canvas[data-net]').forEach(function (c) { window.ocNet(c, c.dataset.net === 'big'); });
})();
