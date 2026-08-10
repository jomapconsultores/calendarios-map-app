/* ------------------------------------------------------------
 * Desarrollado por Marco Antonio Posligua San Martín
 * ------------------------------------------------------------
 * Instalación en el teléfono: Android e iOS.
 *
 * Los dos sistemas instalan la aplicación de forma distinta y hay que tratarlos
 * distinto, no con un mensaje genérico:
 *
 *   ANDROID (Chrome, Edge, Samsung Internet) dispara `beforeinstallprompt`. Se
 *   guarda ese evento y se muestra un botón: al pulsarlo aparece el diálogo
 *   nativo de instalación. Un botón que instala de verdad.
 *
 *   iOS (Safari) NO tiene esa API — Apple no la implementa. La única vía es
 *   Compartir → «Añadir a pantalla de inicio», así que ahí lo que corresponde
 *   es explicar el gesto, no ofrecer un botón que no puede funcionar.
 *
 * El aviso no se repite: si el usuario lo descarta se recuerda la decisión, y
 * no aparece nunca cuando la aplicación ya está instalada.
 * ------------------------------------------------------------ */
(function () {
  'use strict';

  var CLAVE_DESCARTADO = 'instalarDescartadoEn';
  var DIAS_ANTES_DE_VOLVER_A_PREGUNTAR = 30;
  var eventoInstalacion = null;

  function yaInstalada() {
    // `standalone` es la propiedad propia de Safari en iOS; el resto de
    // navegadores responden al media query.
    return window.navigator.standalone === true ||
           window.matchMedia('(display-mode: standalone)').matches ||
           window.matchMedia('(display-mode: minimal-ui)').matches;
  }

  function esIOS() {
    var ua = window.navigator.userAgent;
    // El iPad moderno se identifica como Mac: se distingue por ser táctil.
    return /iPad|iPhone|iPod/.test(ua) ||
           (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  }

  function esSafari() {
    var ua = window.navigator.userAgent;
    return /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
  }

  function fueDescartadoHacePoco() {
    try {
      var cuando = parseInt(localStorage.getItem(CLAVE_DESCARTADO) || '0', 10);
      if (!cuando) return false;
      var dias = (Date.now() - cuando) / 86400000;
      return dias < DIAS_ANTES_DE_VOLVER_A_PREGUNTAR;
    } catch (e) { return false; }
  }

  function descartar() {
    try { localStorage.setItem(CLAVE_DESCARTADO, String(Date.now())); } catch (e) {}
    var banner = document.getElementById('instalarBanner');
    if (banner) banner.classList.remove('mostrar');
  }

  function construirBanner(titulo, texto, botonHtml) {
    var banner = document.getElementById('instalarBanner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'instalarBanner';
      document.body.appendChild(banner);
    }
    banner.innerHTML =
      '<div class="ib-texto"><div class="ib-titulo">' + titulo + '</div>' + texto + '</div>' +
      botonHtml +
      '<button class="ib-no" type="button" aria-label="Cerrar">✕</button>';
    banner.querySelector('.ib-no').addEventListener('click', descartar);
    banner.classList.add('mostrar');
    return banner;
  }

  // ---- ANDROID: instalación real con un botón ----
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();               // se pospone para enseñarlo cuando convenga
    eventoInstalacion = e;
    if (yaInstalada() || fueDescartadoHacePoco()) return;

    var banner = construirBanner(
      'Instalar Calendarios MAP',
      'Ábrela desde la pantalla de inicio, a pantalla completa y con notificaciones.',
      '<button class="ib-si" type="button">Instalar</button>');

    banner.querySelector('.ib-si').addEventListener('click', function () {
      banner.classList.remove('mostrar');
      eventoInstalacion.prompt();
      eventoInstalacion.userChoice.then(function (resultado) {
        if (resultado.outcome !== 'accepted') descartar();
        eventoInstalacion = null;
      });
    });
  });

  window.addEventListener('appinstalled', function () {
    descartar();
    var banner = document.getElementById('instalarBanner');
    if (banner) banner.classList.remove('mostrar');
  });

  // ---- iOS: no hay API, se explica el gesto ----
  document.addEventListener('DOMContentLoaded', function () {
    if (!esIOS() || !esSafari() || yaInstalada() || fueDescartadoHacePoco()) return;
    // Se espera un poco para no interrumpir nada más entrar.
    setTimeout(function () {
      construirBanner(
        'Añádela a tu iPhone',
        'Pulsa <strong>Compartir</strong> <span aria-hidden="true">􀈂</span> abajo y elige ' +
        '<strong>«Añadir a pantalla de inicio»</strong>.',
        '');
    }, 4000);
  });
})();
