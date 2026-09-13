# Install and launch Bellomberg

[Handbook](README.md) · [Next: configuration](configuration.md)

## Choose an installation path

The supported release workflow is the Windows desktop backed by an installed
Python package. The Electron installer is a frontend package, not a complete
Python runtime. The source workflow is the clearest starting point.

| Component | Requirement | Check |
| --- | --- | --- |
| Python | Package minimum 3.10; 3.12 recommended for the documented CI environment; Linux CI also checks 3.14 | `python --version` |
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
compatible Python backend is started or reused. Enter your PIN, choose English
or Italian and save the language. A new profile then opens the mandate page.
The empty portfolio is expected on a new installation. Keep the terminal open.
Do not create `portfolio.json` or copy a sample book: a new installation creates
its SQLite storage and takes its initial entries through F16.

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

The repository contains a `macos-source` CI job for Python 3.12, dependency
consistency, the offline suite, frontend bundles and a hidden Electron test with
a synthetic HTTP backend. It runs on the public repository and on explicit
workflow dispatch in a private repository. Check the result for the exact commit;
the presence of the job does not establish that a run succeeded.

A source installation on a **physical Mac has not been verified** by this local
Windows check. The synthetic Electron test does not certify the real Python
backend launch, Dock lifecycle or window rendering on macOS. Intel Macs and other
interpreter/architecture combinations require their own dependency checks.
Native packages must provide compatible wheels or build successfully.

There is no certified macOS application bundle, DMG, signature or notarization,
and no certified Linux installer. No separate macOS launcher is included.

For source development, the shell equivalents of the Python setup are:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e . pytest
.venv/bin/python -m pip check
cp .env.example .env
export BELLOMBERG_BACKEND_DIR="$PWD"
export BELLOMBERG_PYTHON="$PWD/.venv/bin/python"
```

Configure `.env`, then run these commands in the same terminal:

```bash
cd app
npm ci
npm run desktop:install
npm run dev
```

A Finder/Dock launch does not inherit temporary variables from an unrelated
terminal. Keep the explicit backend directory and virtual-environment interpreter
in the launch environment. Do not interpret these commands as a tested platform
claim.

The Windows Task Scheduler scripts are not portable: no `launchd` or cron setup
is provided. Prices, news, briefings and backups need explicit invocation there;
the Windows automatic backup retention is absent. F19 declares scheduler errors
instead of treating an unavailable scheduler as an empty task list. Excel/COM
recalculation and the operator's Excel conversion tools remain Windows-specific;
inspect a generated workbook's `values_baked` and `bake_error` instead of assuming
cached formula results exist.

[Continue with your first session](first-session.md) or
[troubleshoot a failed launch](troubleshooting.md).
