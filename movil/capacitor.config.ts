/* ------------------------------------------------------------
 * Desarrollado por Marco Antonio Posligua San Martín
 * ------------------------------------------------------------ */

// Empaquetado móvil de Calendarios MAP.
//
// La aplicación ya es una web instalable (PWA). El contenedor Capacitor existe
// para poder publicarla en Google Play y App Store, que exigen un paquete
// firmado: la app nativa abre la MISMA aplicación desplegada, de modo que no hay
// dos versiones que mantener y una corrección en el servidor llega al teléfono
// sin volver a publicar en la tienda.
//
// El destino es el despliegue en Coolify: https://calendario.pensamiento-libre.org
// Queda fijado aquí para que el paquete apunte a producción aunque el
// repositorio no tenga configurada la variable APP_URL. Para compilar contra
// otro destino (una prueba, un dominio nuevo) basta con definirla:
//   set APP_URL=https://otro.ejemplo.com  (Windows)
//   APP_URL=https://otro.ejemplo.com npm run apk:release

import type { CapacitorConfig } from '@capacitor/cli';

const APP_URL = process.env.APP_URL || 'https://calendario.pensamiento-libre.org';

const config: CapacitorConfig = {
  appId: 'ec.map.calendarios',
  appName: 'Calendarios MAP',
  webDir: 'www',

  // El contenedor abre la MISMA aplicación desplegada, no una copia empaquetada.
  //
  // No se declara `allowNavigation` con los dominios de Google y Microsoft a
  // propósito: Google RECHAZA su pantalla de acceso dentro de un WebView
  // incrustado (error `disallowed_useragent`), así que permitirla sólo cambiaría
  // un flujo que se abre en el navegador —y funciona— por uno que falla. Conectar
  // Google Calendar y Microsoft To-Do es tarea del administrador y se hace una
  // vez desde un navegador de escritorio; el resto del sistema no lo necesita.
  server: {
    url: APP_URL,
    cleartext: false,
    androidScheme: 'https',
  },

  // Marca el navegador para que el servidor pueda distinguir la aplicación
  // instalada del navegador normal (útil en los registros y para ajustes de
  // interfaz que sólo aplican dentro del contenedor).
  appendUserAgent: 'CalendariosMAP',

  // Sin color de fondo declarado, al abrir se ve un destello blanco antes de
  // que cargue la web. Con el color de la marca, la transición es limpia.
  backgroundColor: '#4f46e5',

  android: {
    // El WebView de Android no debe permitir contenido mixto ni depuración en release.
    allowMixedContent: false,
    webContentsDebuggingEnabled: false,
    backgroundColor: '#4f46e5',
  },

  ios: {
    // `always` mantiene el contenido por debajo de la muesca y de la barra de
    // gestos; el resto del ajuste lo hace static/css/movil.css con env(safe-area-*).
    contentInset: 'always',
    backgroundColor: '#4f46e5',
    scrollEnabled: true,
    // La app abre un sitio externo: restringir la navegación a dominios
    // declarados rompería los enlaces salientes (mapas, enlaces de reunión).
    limitsNavigationsToAppBoundDomains: false,
  },

  plugins: {
    StatusBar: { style: 'DARK', backgroundColor: '#4f46e5' },
    // En iOS el teclado tapa el campo enfocado si no se le dice que empuje el
    // contenido. `native` es el modo que respeta los formularios largos de las
    // fichas del directorio y del cronograma.
    Keyboard: { resize: 'native', style: 'LIGHT', resizeOnFullScreen: true },
    SplashScreen: {
      launchShowDuration: 1200,
      backgroundColor: '#4f46e5',
      showSpinner: false,
      androidSplashResourceName: 'splash',
    },
  },
};

export default config;
