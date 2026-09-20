# Reproducing documentation screenshots

The capture tool builds and opens the actual Electron renderer in a hidden,
sandboxed window. An isolated local service supplies the invented data from
`app/tests/fixtures/documentation-demo.json`. It does not launch the application
backend, read your portfolio or call market-data or model providers. The visible
`DEMO · SYNTHETIC DATA` watermark is added by the capture tool; the app layout,
charts and controls are unchanged. The tool also counts the Plotly drawing calls
of the page to know when charts have finished; Plotly itself still draws them.

Install the source dependencies as described in [installation](installation.md).
Use an absolute path to an installed Python interpreter. WindowsApps aliases and
auto-installing launchers are refused. Git must be available on `PATH`: the tool
reads the state of the source tree from it. Close other documentation capture
jobs before starting; the tool builds into the local app build directories.

## Reproduce the synthetic account

```text
python docs/guide/generate_documentation_demo.py --check
```

The versioned source book in `docs/guide/fixtures/documentation-demo-source.json`
points to the canonical fixture. The generator rebuilds holdings, cash and linked
trade calculations and checks their cross-page reconciliation. Analytics,
histories and scenes are preserved authored synthetic snapshots, **not regenerated**.
Changing the account requires explicitly updating any coupled snapshots.
Omit `--check` to write the regenerated fixture; check mode writes nothing.

The fixture uses `documentation-local-refs/1`: readable JSON definitions,
`$demo_ref` references within that file and `$demo_table` column/row arrays.
The Python generator and capture loader reconstruct identical values and field
order. No archive, network reference or compressed text is involved. Duplicate
JSON keys, missing or cyclic references, ambiguous markers, duplicate columns
and invalid row widths are rejected, including defects in unused definitions.

The declared `clock.now` and `clock.timezone` fix renderer dates and labels before
the page loads. Capture timers keep real time. The current snapshot captures
English scenes only; a frozen clock does not refresh any market observation.

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
unlisted network requests, downloads and new windows are refused.

### What the fixture declares

`languages` lists the languages to capture, `["it", "en"]` when it is absent.
Every scene is captured once in each of them. The fixture `version` stays 1: the
public screenshot verifier accepts only that version.

Each scene has:

- `id`: unique; lower-case letters, digits and hyphens, starting with a letter.
  It names the PNG.
- `route`: a destination registered in `app/src/lib/navigation.ts`. Any other
  route is refused when the fixture loads.
- `selector`: the element that must exist before the frame is taken.
- `required`: the response keys the scene must request. Besides them, a scene
  may read only the routes of the app shell listed in `shell`.
- `steps`: up to 16 actions performed in order: `{"click": selector}`,
  `{"fill": {"id": "value"}}` for inputs and text areas, `{"select": {"id": "value"}}`
  for select elements, `{"wait": selector}` and `{"scroll": selector}`, which
  scrolls the element into view, aligned to the top as far as the page scrolls.
- `viewport` (optional): `{"width": 1440, "height": 900}` in CSS pixels, width
  800 to 2560 and height 600 to 1600; the default is 1440 x 1000.
- `note` (optional): what the scene shows. It is copied into the receipt.

The earlier scene form is still accepted: `clicks` (up to three selectors) run
first, then `fields` fill inputs or selects by id. A scene uses either `steps` or
that form, not both.

A selector is made of a tag, `#id`, `.class`, `[name]` or `[name="value"]`
parts, with a single space for descendants, for example
`button[aria-label="Open panel"]` or `.journal-notes button`. Names are lower
case; attribute values are quoted and contain no quotes, backslashes or square
brackets. Child and sibling combinators, lists, `*` and pseudo-classes are refused.

When a scene has steps, the loaded page must settle before the first one. Each
step then waits up to 15 seconds for its target. A click, wait or scroll target
must be present and rendered, and a click target must not be disabled or inside
an element with `aria-busy="true"`. The fields of a fill or select must exist
and not be disabled, and a select must offer the value. After the action the
page must settle again before the next step starts. A page is settled when no
request is pending, no element is busy, the charts are ready, and none of this
has changed for half a second; a page that does not settle within 15 seconds
fails the check.

### What the local service answers

Response keys are `"<METHOD> <path>"` or `"<METHOD> <path>?<query>"`, with
`GET`, `POST`, `PUT` or `DELETE`. A key without a method, such as `"/portfolio"`,
means `GET /portfolio`. The query is the one the app sends, with its parameters
sorted by name and encoded as `URLSearchParams` encodes them: a comma becomes
`%2C`, for example `GET /options/vol_surface/DEMO?expiries=2026-10-16%2C2026-11-20&include_context=false`.
An unsorted or differently encoded query is refused when the fixture loads.

For each request the service looks for the method, path and query first, then
for the method and path. `GET /preferences` is answered with the language the app
sends and cannot be declared. A `POST`, `PUT` or `DELETE` is answered only when
its key is declared, always with the declared body: the service stores nothing.
The body it received, up to 1 MiB, is recorded in the receipt. A preflight check
is answered only for a request that would be answered. Every other request is
refused (HTTP 409, or 413 for a larger body), and any refused request fails the
check. The selector does not make a click safe; this refusal does.

### Readiness and the frame

Each scene then waits for fonts, images and requests, and verifies the viewport,
the watermark, the final selector and the data reads the scene requires. Charts
are ready when every `Plotly.newPlot` and `Plotly.react` call has finished
without error, and every visible Plotly chart is drawn, shows no "WebGL is not
supported" panel and keeps its WebGL contexts. Plotly loaded before the tool
starts observing it fails the scene. A scene that reads a response key it does not declare,
beyond the app shell, fails.

Hardware acceleration is disabled in the capture window. On the Windows machine
where this was measured, WebGL ran on the Microsoft Basic Render Driver and a
Plotly 3D surface drawn in the hidden window reached the captured pixels. The
offline tests repeat that measurement on the machine that runs them.

Before each screenshot attempt the watermark takes a slightly different yellow
that no earlier attempt of the run used. A frame without that exact colour is
treated as older than the checked page and captured again; ten such frames in a
row fail the check. Every attempt needs an unused colour, so a fixture holds at most 51
captures (scenes times languages); a larger one is refused when it loads. The
checks save no PNGs. The receipt lists, per scene, the
steps performed, the chart counts and the requests made, and lists capture inputs
that differ from the checked-out commit; it is not an operating-system network
audit.

## Produce review candidates

```text
node app/tools/capture-docs.cjs --candidate --python /absolute/path/to/python --font-cache /absolute/path/to/font-cache
```

The candidate run refuses to start while a capture input (the app sources, the
fixture or the capture script) has uncommitted changes or is untracked. Commit
first, so that the manifest binds the PNGs to sources a commit contains.

Candidates are written only into a new temporary directory. Each PNG has a text
capture of the page and a manifest binding its bytes to the fixture, capture
script, app version, source and built renderer. Source drift during the operation
fails the capture. The public PNG verifier checks structure and provenance; it
does not perform OCR or prove that every pixel is free of private content.

Review the actual PNGs and text before using them. The 2026-09-19 PM-ratified
package is retained with its exact PNG, DOM, manifest and private ratification
bytes as a historical view. After source changes it is labelled historical and
does not claim to show the current renderer. New candidate captures still require
clean committed inputs and a live source digest, followed by visual ratification.
A successful candidate run does not publish or approve the images. Release
approval and the complete export checks are separate steps.

The deterministic guards can be run with `node app/tests/desktop/capture-docs.cjs`;
set `BB_DOCS_PYTHON` to the installed interpreter first. They do not build the
app or contact other hosts. One starts the local service on 127.0.0.1; another
opens a hidden Electron window on local test pages and checks the step order, the
wait for a slow chart and that a Plotly 3D surface reaches the captured frame. Add `--integration` and set
`BB_DOCS_FONT_CACHE` to exercise the complete hidden renderer workflow.
