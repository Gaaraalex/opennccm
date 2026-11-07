/* Aviso «revisa los comandos antes de usarlos en producción» (templates/_warn.html): «Ver el aviso» abre el aviso
   completo y la casilla obligatoria habilita los botones que aplican comandos (data-needs-ack) de su formulario o
   diálogo. Un botón con data-blocked (por ejemplo, una variable sin valor) sigue desactivado aunque la casilla esté
   marcada. Sirve también dentro de los paneles laterales, cuyos botones del pie van fuera del formulario. */
(function () {
  function scopeOf(el) { return (el.form || el.closest('form')) || el.closest('dialog, .sheet') || document; }
  function buttonsFor(scope) {
    return Array.prototype.filter.call(document.querySelectorAll('[data-needs-ack]'), function (b) {
      return b.form === scope || (scope.contains && scope.contains(b));
    });
  }
  function sync(scope) {
    var ack = scope.querySelector ? scope.querySelector('[data-ack]') : null;
    if (!ack) return;
    buttonsFor(scope).forEach(function (b) { b.disabled = !ack.checked || b.hasAttribute('data-blocked'); });
  }
  window.ocAckSync = function (el) { sync(scopeOf(el)); };
  function init(root) { Array.prototype.forEach.call((root || document).querySelectorAll('[data-ack]'), function (a) { sync(scopeOf(a)); }); }
  document.addEventListener('change', function (e) { if (e.target.matches && e.target.matches('[data-ack]')) sync(scopeOf(e.target)); });
  document.addEventListener('click', function (e) {
    var o = e.target.closest && e.target.closest('[data-warn-open]'); if (!o) return;
    var d = document.getElementById('warn-dlg'); if (d && d.showModal) d.showModal();
  });
  // un botón «enviar» que llega a enviarse sin la casilla (por ejemplo, con Enter): no se envía
  document.addEventListener('submit', function (e) {
    var a = e.target.querySelector && e.target.querySelector('[data-ack]');
    if (a && !a.checked) { e.preventDefault(); a.focus(); }
  }, true);
  // «Corregir N» y «Corregir» de una regla (ficha del equipo): el mismo diálogo, con todas o con una sola corrección
  function fixSync(d) {
    var rows = Array.prototype.filter.call(d.querySelectorAll('.cfix-r'), function (r) { return !r.hidden; });
    var on = rows.filter(function (r) { return r.querySelector('input').checked; }), n = on.length;
    var h = d.querySelector('[data-h-n]'), go = d.querySelector('[data-fix-go]'), gh = d.querySelector('[data-gh]:not(.cfix-r)');
    if (h) h.textContent = n === 1 ? h.dataset.hOne : h.dataset.hN.replace('{n}', n);
    if (go) { go.textContent = n === 1 ? go.dataset.lOne : go.dataset.lN.replace('{n}', n); if (n) go.removeAttribute('data-blocked'); else go.setAttribute('data-blocked', ''); }
    if (gh && on[0]) gh.href = on[0].dataset.gh;
    sync(d.querySelector('form') || d);
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-fix]'); if (!b) return;
    e.preventDefault(); e.stopPropagation();  // el botón está dentro del <summary>: que no pliegue la política
    var d = document.getElementById(b.dataset.fix); if (!d) return;
    var only = b.dataset.only;
    Array.prototype.forEach.call(d.querySelectorAll('.cfix-r'), function (r) { var show = !only || r.dataset.rule === only; r.hidden = !show; r.querySelector('input').checked = show; r.querySelector('input').disabled = !show; });
    var a = d.querySelector('[data-ack]'); if (a) a.checked = false;
    fixSync(d); d.showModal();
  }, true);
  document.addEventListener('change', function (e) { var d = e.target.closest && e.target.closest('dialog.cfix'); if (d && e.target.closest('.cfix-r')) fixSync(d); });
  document.addEventListener('oc-sheet', function (e) { init(e.detail); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { init(); }); else init();
})();
