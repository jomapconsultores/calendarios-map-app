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
  server: { url: APP_URL, cleartext: false, androidScheme: 'https' },
  android: {
    // El WebView de Android no debe permitir contenido mixto ni depuración en release.
    allowMixedContent: false,
    webContentsDebuggingEnabled: false,
  },
  ios: {
    contentInset: 'always',
  },
  plugins: {
    StatusBar: { style: 'DARK', backgroundColor: '#2563eb' },
  },
};

export default config;
