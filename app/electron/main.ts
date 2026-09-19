import { app, BrowserWindow, shell, dialog } from 'electron';
import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import http from 'http';
import { fileURLToPath, pathToFileURL } from 'url';
import { externalWebUrl, isAppDocument, apiPort, defaultPython } from './security';

// ES Modules polyfill per __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let pythonBackend: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;
let backendOwned = false; // true se l'abbiamo spawnato noi

const PROJECT_ROOT = process.env.BELLOMBERG_BACKEND_DIR
  ? path.resolve(process.env.BELLOMBERG_BACKEND_DIR)
  : !app.isPackaged ? path.dirname(app.getAppPath()) : null;
const API_PORT = apiPort(process.env.BELLOMBERG_API_PORT);
process.env.BELLOMBERG_DESKTOP_API_URL = `http://127.0.0.1:${API_PORT}`;
let quitting = false;

function stopOwnedBackend() {
  const child = pythonBackend;
  pythonBackend = null;
  const owned = backendOwned;
  backendOwned = false;
  if (child && owned && child.exitCode === null) {
    try { child.kill(); } catch (error) { console.error('[backend] cleanup failed:', error); }
  }
}

function backendError(message: string) {
  console.error('[Bellomberg]', message);
  if (!quitting) dialog.showErrorBox('Bellomberg — backend non disponibile / backend unavailable', message);
}

// Launch ID univoco per ogni avvio di Electron: il renderer lo confronta
// con quello salvato in localStorage per forzare il PIN ad ogni rilancio.
// Reload (F5/Ctrl+R) all'interno della stessa sessione NON richiede ri-login.
process.env.BELLOMBERG_LAUNCH_ID = String(Date.now());

// Check if a backend is already running on 8765 (e.g. dal terminale o seconda istanza)
function pingBackend(): Promise<boolean> {
  return new Promise(resolve => {
    const req = http.get(`http://127.0.0.1:${API_PORT}/health`, { timeout: 800 }, res => {
      let body = '';
      res.on('data', chunk => { body += chunk.toString(); if (body.length > 65536) req.destroy(); });
      res.on('end', () => {
        try {
          const health = JSON.parse(body);
          resolve(res.statusCode === 200 && health.status === 'ok'
            && typeof health.brand === 'string' && typeof health.version === 'string');
        } catch { resolve(false); }
      });
      res.on('error', () => resolve(false));
      res.on('close', () => resolve(false));
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

async function startPythonBackend() {
  // Il processo principale parte prima del backend, che custodisce la lingua scelta: ogni avviso
  // dice la stessa cosa in italiano e poi in inglese; i dettagli tecnici compaiono una volta sola.
  const bilingue = (it: string, en: string, ...dettagli: string[]) =>
    [it, en, dettagli.join('\n')].filter(Boolean).join('\n\n');
  try {
    if (backendOwned && pythonBackend?.exitCode === null) return;
    const alreadyUp = await pingBackend();
    if (alreadyUp) {
      console.log('[Bellomberg] Backend already running on port', API_PORT, '— skipping spawn');
      backendOwned = false;
      return;
    }
    if (!PROJECT_ROOT) {
      backendError(bilingue(
        'L’installer contiene solo l’app desktop, non il backend. Installa il backend Python e imposta BELLOMBERG_BACKEND_DIR e BELLOMBERG_PYTHON, oppure avvia il backend separatamente. Consulta SETUP_APP.md.',
        'The installer contains only the desktop app, not the backend. Install the Python backend and set BELLOMBERG_BACKEND_DIR and BELLOMBERG_PYTHON, or start the backend separately. See SETUP_APP.md.'));
      return;
    }
    const configuredPython = process.env.BELLOMBERG_PYTHON;
    const choice = configuredPython
      ? { python: configuredPython, source: 'BELLOMBERG_PYTHON', tried: [] }
      : defaultPython(process.platform, PROJECT_ROOT, file => fs.existsSync(file));
    const python = choice.python;
    if (configuredPython && /[\\/]/.test(configuredPython)
      && !fs.existsSync(path.resolve(PROJECT_ROOT, configuredPython))) {
      backendError(bilingue(
        'Il percorso in BELLOMBERG_PYTHON non esiste. Correggi il percorso dell’interprete.',
        'The path in BELLOMBERG_PYTHON does not exist. Fix the interpreter path.',
        'Percorso / Path: ' + configuredPython));
      return;
    }
    const script = path.join(PROJECT_ROOT, 'bellomberg_api.py');
    if (!fs.existsSync(script)) {
      backendError(bilingue(
        'Backend non trovato. Imposta BELLOMBERG_BACKEND_DIR alla cartella del backend installato.',
        'Backend not found. Set BELLOMBERG_BACKEND_DIR to the folder of the installed backend.',
        'Percorso cercato / Path checked: ' + script));
      return;
    }
    const interpreter = python + ' (' + choice.source + ')'
      + (choice.tried.length ? '; ambiente virtuale non trovato / virtual environment not found: ' + choice.tried.join(', ') : '');
    console.log('[Bellomberg] Interprete backend:', interpreter);
    const child = spawn(python, [script], {
      cwd: PROJECT_ROOT,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    pythonBackend = child;
    backendOwned = true;
    let stderrTail = '';
    child.stdout?.on('data', d => console.log('[backend]', d.toString()));
    child.stderr?.on('data', d => {
      stderrTail = (stderrTail + d.toString()).slice(-4096);
      console.error('[backend ERR]', d.toString());
    });
    child.once('error', error => {
      if (pythonBackend === child) { pythonBackend = null; backendOwned = false; }
      backendError(bilingue(
        'Avvio di Python fallito. Controlla BELLOMBERG_PYTHON e le dipendenze del backend.',
        'Python failed to start. Check BELLOMBERG_PYTHON and the backend dependencies.',
        'Errore / Error: ' + error.message,
        'Interprete / Interpreter: ' + interpreter));
    });
    child.on('exit', (code, signal) => {
      console.log('[Bellomberg] backend exited code=', code, 'signal=', signal);
      if (pythonBackend === child) {
        pythonBackend = null; backendOwned = false;
        const lastError = stderrTail.trim().split(/\r?\n/).pop()?.slice(-500);
        backendError(bilingue(
          'Il backend Python è terminato. Controlla i log e riavvia l’app.',
          'The Python backend exited. Check the logs and restart the app.',
          'Codice di uscita / Exit code: ' + (code === null ? 'non disponibile / unavailable' : String(code)),
          'Segnale / Signal: ' + (signal || 'nessuno ricevuto / none received'),
          'Interprete / Interpreter: ' + interpreter,
          'Ultimo errore su stderr / Last stderr error: ' + (lastError || 'nessuno ricevuto / none received')));
      }
    });
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline && pythonBackend === child && !quitting) {
      if (await pingBackend()) return;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    if (pythonBackend === child && !quitting) {
      stopOwnedBackend();
      backendError(bilingue(
        'Il backend non è pronto dopo 30 secondi. Controlla la configurazione e i log Python, poi riavvia l’app.',
        'The backend was not ready after 30 seconds. Check the configuration and the Python logs, then restart the app.'));
    }
  } catch (e) {
    backendError(bilingue(
      'Avvio del backend fallito.',
      'The backend failed to start.',
      'Errore / Error: ' + String(e)));
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    title: 'Bellomberg',
    backgroundColor: '#0a0e1a',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.mjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [
        '--bellomberg-launch-id=' + process.env.BELLOMBERG_LAUNCH_ID,
        '--bellomberg-api-port=' + API_PORT,
      ],
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    const safe = externalWebUrl(url);
    if (safe) shell.openExternal(safe).catch(error => console.error('[external link]', error));
    else console.warn('[external link] unsupported protocol blocked');
    return { action: 'deny' };
  });

  // Zoom shortcuts:
  //   - Ctrl/Cmd + = (or +)     -> zoom in
  //   - Ctrl/Cmd + -            -> zoom out
  //   - Ctrl/Cmd + 0            -> reset
  //   - Ctrl/Cmd + mouse wheel  -> zoom in/out (wheel up = in, wheel down = out)
  const ZOOM_STEP = 0.5;
  const ZOOM_STEP_WHEEL = 0.25;
  const ZOOM_MIN = -3;
  const ZOOM_MAX = 5;

  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return;
    if (!(input.control || input.meta)) return;
    const wc = mainWindow!.webContents;
    const cur = wc.getZoomLevel();
    if (input.key === '+' || input.key === '=') {
      wc.setZoomLevel(Math.min(ZOOM_MAX, cur + ZOOM_STEP));
      event.preventDefault();
    } else if (input.key === '-' || input.key === '_') {
      wc.setZoomLevel(Math.max(ZOOM_MIN, cur - ZOOM_STEP));
      event.preventDefault();
    } else if (input.key === '0') {
      wc.setZoomLevel(0);
      event.preventDefault();
    }
  });

  // Ctrl + mouse wheel zoom: inject a JS listener in the page after load
  mainWindow.webContents.on('did-finish-load', () => {
    mainWindow!.webContents.executeJavaScript(`
      (function() {
        if (window.__bellombergWheelZoom) return;
        window.__bellombergWheelZoom = true;
        window.addEventListener('wheel', (e) => {
          if (e.ctrlKey || e.metaKey) {
            e.preventDefault();
            // Send IPC-like signal via custom event the main process polls
            // Simpler: dispatch zoom request to console for main to intercept
            const delta = e.deltaY < 0 ? +1 : -1;
            console.log('__bellomberg_zoom__:' + delta);
          }
        }, { passive: false });
      })();
    `).catch(() => {});
  });

  // Listen to the console-log signal from the page and apply zoom
  mainWindow.webContents.on('console-message', ({ message }) => {
    if (!message.startsWith('__bellomberg_zoom__:')) return;
    const delta = parseInt(message.split(':')[1]);
    if (!isFinite(delta)) return;
    const wc = mainWindow!.webContents;
    const cur = wc.getZoomLevel();
    const next = delta > 0
      ? Math.min(ZOOM_MAX, cur + ZOOM_STEP_WHEEL)
      : Math.max(ZOOM_MIN, cur - ZOOM_STEP_WHEEL);
    wc.setZoomLevel(next);
  });

  // Dev/prod detection.
  const isDev = !app.isPackaged;
  const VITE_DEV_URL = isDev ? process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173' : null;
  const indexPath = path.join(__dirname, '..', 'dist', 'index.html');
  const entry = VITE_DEV_URL || pathToFileURL(indexPath).href;
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!isAppDocument(url, entry)) event.preventDefault();
  });
  mainWindow.webContents.on('will-redirect', (event, url) => {
    if (!isAppDocument(url, entry)) event.preventDefault();
  });
  mainWindow.webContents.session.setPermissionRequestHandler((contents, permission, callback, details) => {
    callback(permission === 'notifications' && contents === mainWindow?.webContents
      && details.isMainFrame && isAppDocument(details.requestingUrl, entry));
  });
  mainWindow.webContents.session.setPermissionCheckHandler((contents, permission, _origin, details) => {
    return permission === 'notifications' && contents === mainWindow?.webContents
      && details.isMainFrame && isAppDocument(details.requestingUrl || contents.getURL(), entry);
  });

  if (VITE_DEV_URL) {
    console.log('[Bellomberg] Loading DEV url:', VITE_DEV_URL);
    mainWindow.loadURL(VITE_DEV_URL);
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    console.log('[Bellomberg] Loading PROD file:', indexPath);
    mainWindow.loadFile(indexPath);
  }
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    if (mainWindow?.isMinimized()) mainWindow.restore();
    mainWindow?.focus();
  });
  app.whenReady().then(async () => {
    await startPythonBackend();
    if (!quitting) createWindow();
  });
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    stopOwnedBackend();
    app.quit();
  }
});

app.on('before-quit', () => {
  quitting = true;
  stopOwnedBackend();
});

app.on('activate', async () => {
  if (BrowserWindow.getAllWindows().length === 0 && !quitting) {
    await startPythonBackend();
    if (!quitting) createWindow();
  }
});
