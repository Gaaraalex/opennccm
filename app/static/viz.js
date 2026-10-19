/* Información emergente de las gráficas: cualquier elemento con data-tip muestra su texto junto al puntero
   (o junto al elemento si se llega con el teclado). Sin dependencias externas. */
(function () {
  var tip = document.createElement('div');
  tip.className = 'viz-tip';
  tip.setAttribute('role', 'tooltip');
  tip.hidden = true;
  document.body.appendChild(tip);

  function place(x, y) {
    var r = tip.getBoundingClientRect(), px = x + 14, py = y + 14;
    if (px + r.width > window.innerWidth - 8) px = x - r.width - 14;
    if (py + r.height > window.innerHeight - 8) py = y - r.height - 14;
    tip.style.left = Math.max(8, px) + 'px';
    tip.style.top = Math.max(8, py) + 'px';
  }
  function show(el, x, y) {
    tip.textContent = el.getAttribute('data-tip');
    tip.hidden = false;
    place(x, y);
  }
  document.addEventListener('pointerover', function (e) {
    var el = e.target.closest && e.target.closest('[data-tip]');
    if (el) show(el, e.clientX, e.clientY); else tip.hidden = true;
  });
  document.addEventListener('pointermove', function (e) {
    if (!tip.hidden) place(e.clientX, e.clientY);
  });
  document.addEventListener('pointerout', function (e) {
    var to = e.relatedTarget;
    if (!to || !to.closest || !to.closest('[data-tip]')) tip.hidden = true;
  });
  document.addEventListener('focusin', function (e) {
    var el = e.target.closest && e.target.closest('[data-tip]');
    if (el) { var r = el.getBoundingClientRect(); show(el, r.left + r.width / 2, r.bottom); } else tip.hidden = true;
  });
  document.addEventListener('scroll', function () { tip.hidden = true; }, true);
})();
