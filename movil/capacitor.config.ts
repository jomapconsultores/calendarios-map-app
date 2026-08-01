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
// APP_URL es la dirección del despliegue. Se define al construir:
//   set APP_URL=https://mi-sistema.ejemplo.com  (Windows)
//   APP_URL=https://mi-sistema.ejemplo.com npm run apk:release
// En CI llega desde el secreto/variable APP_URL del repositorio.

import type { CapacitorConfig } from '@capacitor/cli';

const APP_URL = process.env.APP_URL || '';

if (!APP_URL) {
  // Se avisa en vez de fallar: 'cap add android' debe poder correr sin la URL,
  // pero un APK sin ella solo mostraría la pantalla de cortesía de www/.
  console.warn('[calendario] APP_URL no definida: el paquete quedará sin apuntar al despliegue.');
}

const config: CapacitorConfig = {
  appId: 'ec.map.calendarios',
  appName: 'Calendarios MAP',
  webDir: 'www',
  // Sin APP_URL no se declara `server`: así el paquete abre www/index.html, que
  // explica qué falta en vez de quedarse en blanco.
  ...(APP_URL ? { server: { url: APP_URL, cleartext: false, androidScheme: 'https' } } : {}),
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
