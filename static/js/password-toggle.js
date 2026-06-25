// Ojito 👁️ para mostrar/ocultar contraseñas.
// Se engancha automáticamente a TODO input[type=password] de la página:
//   1er click  -> muestra la clave (👁️ → 🙈)
//   2º  click  -> la oculta de nuevo (🙈 → 👁️)
(function () {
  function attach(input) {
    if (input.dataset.pwToggle) return;        // evitar duplicar
    input.dataset.pwToggle = '1';

    // Contenedor relativo para posicionar el botón encima del input
    var wrap = document.createElement('div');
    wrap.style.position = 'relative';
    wrap.style.width = '100%';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    // Espacio a la derecha para que el texto no quede bajo el ojito
    input.style.paddingRight = '2.6rem';

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.tabIndex = -1;                          // no robar el foco al tabular
    btn.textContent = '👁️';
    btn.setAttribute('aria-label', 'Mostrar contraseña');
    btn.title = 'Mostrar contraseña';
    btn.style.cssText =
      'position:absolute;top:50%;right:.55rem;transform:translateY(-50%);' +
      'background:none;border:0;padding:0 .25rem;margin:0;cursor:pointer;' +
      'font-size:1.15rem;line-height:1;opacity:.6;user-select:none;';

    btn.addEventListener('click', function () {
      var reveal = input.type === 'password';
      input.type = reveal ? 'text' : 'password';
      btn.textContent = reveal ? '🙈' : '👁️';
      var label = reveal ? 'Ocultar contraseña' : 'Mostrar contraseña';
      btn.setAttribute('aria-label', label);
      btn.title = label;
      btn.style.opacity = reveal ? '1' : '.6';
      input.focus();
    });

    wrap.appendChild(btn);
  }

  function init() {
    document.querySelectorAll('input[type="password"]').forEach(attach);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
