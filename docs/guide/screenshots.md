# Reproducing documentation screenshots

The capture tool builds and opens the actual Electron renderer in a hidden,
sandboxed window. An isolated local service supplies the invented data from
`app/tests/fixtures/documentation-demo.json`. It does not launch the application
backend, read your portfolio or call market-data or model providers. The visible
`DEMO · SYNTHETIC DATA` watermark is added by the capture tool; the app layout,
charts and controls are unchanged.

Install the source dependencies as described in [installation](installation.md).
Use an absolute path to an installed Python interpreter. WindowsApps aliases and
auto-installing launchers are refused. Close other documentation capture jobs
before starting; the tool builds into the local app build directories.

## Prepare the existing app fonts

```text
node app/tools/capture-docs.cjs --prepare-fonts
```

This step downloads the same public Google Fonts stylesheet used by the app and
its font resources into a new temporary directory. Every URL and byte digest is
pinned in the fixture. A redirect, changed resource or download failure stops
preparation. This preparation requires internet access; it is not an offline
installation. Keep the returned `font_cache` directory for the following command.

## Check the scenes

```text
node app/tools/capture-docs.cjs --check --python /absolute/path/to/python --font-cache /absolute/path/to/font-cache
```

Replace both paths with actual paths on your machine, including the drive letter
on Windows. The capture session serves those fonts from the verified local cache.
Only the isolated API and inventoried app files are permitted. Production ports,
unlisted network requests, downloads and new windows are refused. The checks
exercise Dashboard and Trade Entry in Italian and English, wait for fonts,
images and requests, and verify the watermark and required data reads. They save
no PNGs. The receipt states the checks performed; it is not an operating-system
network audit.

## Produce review candidates

```text
node app/tools/capture-docs.cjs --candidate --python /absolute/path/to/python --font-cache /absolute/path/to/font-cache
```

Candidates are written only into a new temporary directory. Each PNG has a text
capture of the page and a manifest binding its bytes to the fixture, capture
script, app version, source and built renderer. Source drift during the operation
fails the capture. The public PNG verifier checks structure and provenance; it
does not perform OCR or prove that every pixel is free of private content.

Review the actual PNGs and text before using them. Capture again after changing
app sources or the fixture. A successful candidate run does not publish or approve
the images. Release approval and the complete export checks are separate steps.

The deterministic guards can be run with `node app/tests/desktop/capture-docs.cjs`;
set `BB_DOCS_PYTHON` to the installed interpreter first. Add `--integration` and
set `BB_DOCS_FONT_CACHE` to exercise the complete hidden renderer workflow.
