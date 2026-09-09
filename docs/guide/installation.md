# Install and launch Bellomberg

[Handbook](README.md) · [Next: configuration](configuration.md)

## Choose an installation path

The supported release workflow is the Windows desktop backed by an installed
Python package. The Electron installer is a frontend package, not a complete
Python runtime. The source workflow is the clearest starting point.

| Component | Requirement | Check |
| --- | --- | --- |
| Python | 3.10 or later; CI uses 3.12 | `python --version` |
| Node.js | 22.12 or later | `node --version` |
| npm | Included with Node.js | `npm --version` |
| Git | A working Git installation | `git --version` |
| Disk | Local writable project and runtime directories | Avoid live cloud synchronization of SQLite |
| Network | Needed for dependencies, market providers and AI calls | Your accounts control access and billing |

Use the repository's **Code → HTTPS** address. There is no separate download link
assumed by this guide. Do not run the clone command with the literal placeholder.

## Windows source setup

Open PowerShell where you want the project, then run one line at a time:

```powershell
git clone <repository-url> bellomberg
cd bellomberg
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pytest
Copy-Item .env.example .env
```

The editable installation points the virtual environment at this checkout.
Success means pip completes without an installation error. You can check
dependency consistency with:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

Edit the new `.env` with a text editor. Set a valid PIN before launching.
Configure keys and model choices as described in [configuration](configuration.md).
Do not run `Copy-Item` over an existing private configuration when updating.

Install the interface:

```powershell
cd app
npm ci
npm run desktop:install
cd ..
```

The Electron download is explicit. Successful `npm ci` alone is not proof that
the Electron executable has been installed.

Launch from the checkout root:

```powershell
$env:BELLOMBERG_BACKEND_DIR = (Get-Location).Path
$env:BELLOMBERG_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
cd app
npm run dev
```

Expected result: Vite remains running, Electron opens the login screen, and a
compatible Python backend is started or reused. Enter your PIN. The empty
portfolio is expected on a new installation. Keep the terminal open.

## Run the backend separately

This is useful when you want its logs visible. From the checkout root:

```powershell
.\.venv\Scripts\bellomberg-api.exe
```

The API binds to `127.0.0.1:8765`. The desktop can reuse it. In a second terminal,
set the same `BELLOMBERG_BACKEND_DIR` and `BELLOMBERG_PYTHON`, then launch the app
as above. New terminals do not inherit the first terminal's temporary variables.
Do not start a second backend when one already owns the port.

Vite normally uses `localhost:5173` and refuses to silently move to another port.
Use the hostname it prints; `localhost` and `127.0.0.1` can resolve differently.
The desktop is the documented product workflow; visiting the development URL is
not a certification of an installed desktop's backend configuration.

## Installed desktop and builds

From `app/`, `npm run build:bundles` builds the web/Electron code and
`npm run build` additionally packages the Windows installer in `app/release/`.
Use an installer only from the release you intend to run. No URL is assumed.

Before launching an installed desktop, arrange for these environment variables
to be available to that process:

- `BELLOMBERG_BACKEND_DIR`: absolute path to the installed backend checkout.
- `BELLOMBERG_PYTHON`: absolute path to its virtual-environment interpreter.

Starting Python separately is another way to provide the backend. The installer
does not bundle your Python environment, credentials, database or research.
Unsigned builds may trigger Windows publisher warnings; verify the source and
release checksums before deciding to run them.

## macOS and Linux

Python packaging and the frontend source can be developed on other operating
systems. This release does not certify a macOS application bundle, DMG,
notarization, Linux installer or Windows scheduled tasks on those platforms.
No separate macOS launcher is included in this distribution.

For source development, the shell equivalents of the Python setup are:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e . pytest
cp .env.example .env
export BELLOMBERG_BACKEND_DIR="$PWD"
export BELLOMBERG_PYTHON="$PWD/.venv/bin/python"
```

Then configure `.env`, install the `app/` dependencies and Electron as on
Windows. Host-specific native dependencies and desktop behavior need their own
validation. Do not interpret these equivalent commands as a tested platform claim.

[Continue with your first session](first-session.md) or
[troubleshoot a failed launch](troubleshooting.md).
