# Bellomberg desktop setup

The desktop application uses a separately installed Python backend. The Windows installer contains the interface, not Python, API keys, portfolio data, or backend source code. See the root README for backend installation and configuration.

## Requirements

- Node.js 22.12 or newer; CI uses Node 22.23.2.
- An installed Bellomberg Python backend and its full requirements. Installing only FastAPI is not sufficient.
- Windows for the NSIS installer. Other desktop targets have not been validated by this release workflow.

## Install desktop dependencies

Run these commands from the repository's `app` directory:

```powershell
npm ci
npm run desktop:install
```

Electron 42 and later download their binary on first use rather than during npm's postinstall. The second command invokes Electron's explicit installer. A successful `npm ci` alone does not verify that `electron.exe` exists.

## Run from source

Configure the backend directory and the Python executable from its virtual environment. Use absolute paths for your installation:

```powershell
$env:BELLOMBERG_BACKEND_DIR = 'C:\path\to\Bellomberg'
$env:BELLOMBERG_PYTHON = 'C:\path\to\Bellomberg\.venv\Scripts\python.exe'
npm run dev
```

The source checkout is the default backend directory during development. Python defaults to `python` on Windows and `python3` elsewhere; setting the executable explicitly avoids using the wrong environment. The desktop reuses an already running backend and stops only a backend process it started itself.

The default API address is `http://127.0.0.1:8765`. `BELLOMBERG_API_PORT` can select an integer port from 1024 to 65535 when supported by the installed backend. Vite uses port 5173 and refuses to silently move to another port.

For an installed desktop application, set these variables before starting the executable, or start the Python backend separately first. Missing Python, a missing backend directory, and failed startup are reported explicitly. Keep private configuration and data in the backend installation, outside the desktop package.

## Verify and build

```powershell
npm run test:release
npm run build:bundles
npm run test:desktop
npm run build
```

Release tests exercise chat races, stream completion, safe URLs and missing financial data using synthetic inputs. The desktop smoke test opens a hidden Electron window with isolated browser storage and a temporary mock HTTP server; it does not connect to the operational backend or start Python. It verifies the production renderer, preload, sandbox and CSP, not installation of the external Python backend.

The installer is written to `app/release/`. No publishing command is run. The executable is unsigned unless the maintainer separately configures code signing; Windows can show a publisher warning.
