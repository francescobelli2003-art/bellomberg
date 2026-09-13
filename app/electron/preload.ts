import { contextBridge } from 'electron';

// Sandboxed preloads receive custom configuration through argv, not process.env.
const argument = (name: string) => process.argv.find(value => value.startsWith(name + '='))?.slice(name.length + 1);
const port = argument('--bellomberg-api-port');
const launchId = argument('--bellomberg-launch-id');
if (!port || !/^\d+$/.test(port) || Number(port) < 1024 || Number(port) > 65535 || !launchId) {
  throw new Error('Desktop preload configuration is missing or invalid');
}

// Native platform controls the macOS drag region without guessing from the user agent.
window.addEventListener('DOMContentLoaded', () => {
  document.documentElement.dataset.platform = process.platform;
}, { once: true });

contextBridge.exposeInMainWorld('bellomberg', {
  apiUrl: 'http://127.0.0.1:' + port,
  version: '0.4.0',
  // Launch ID univoco per ogni avvio di Electron - usato da LoginGate
  // per forzare il PIN ogni volta che l'app viene rilanciata.
  launchId,
});
