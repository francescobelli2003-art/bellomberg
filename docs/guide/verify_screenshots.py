"""Verify narrow PNG structure and public synthetic-capture provenance.

This is not OCR or a secret scan of pixels. Publication additionally requires
private, visually ratified path/image/manifest hashes. Standard library only.
Source hashes normalize UTF-8 CRLF to LF; PNG, DOM and manifest hashes use exact
bytes. bundle_sha256 records capture provenance, not a claimed local rebuild.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib

MANIFEST_PATH = "docs/assets/screenshots/manifest.json"
SCREENSHOT_DIR = "docs/assets/screenshots/"
FIXTURE_PATH = "app/tests/fixtures/documentation-demo.json"
CAPTURE_PATH = "app/tools/capture-docs.cjs"
WATERMARK = "DEMO · SYNTHETIC DATA"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 2 * 1024 * 1024
MAX_SIDE = 8192
MAX_PIXELS = 16 * 1024 * 1024
SOURCE_DIRS = ("app/src/", "app/electron/", "app/public/", "app/resources/")
ROOT_SOURCE_SUFFIXES = {".json", ".js", ".cjs", ".mjs", ".ts", ".html", ".css"}
ROOT_SOURCE_NAMES = {".npmrc", ".nvmrc", ".browserslistrc"}
# PM-ratified capture of 2026-09-19. Only these exact manifest bytes may be
# described as historical after the app sources change; never as a live rebuild.
HISTORICAL_MANIFEST_SHA256 = "af18462e50732f2c90c328f538f477180dbedc8006e9ce4c6a5a61fe163b49ab"
HISTORICAL_CAPTURE_DATE = "2026-09-19"


class ScreenshotError(ValueError):
    """Fail closed without including captured text or bytes in diagnostics."""


def _require(condition, message):
    if not condition:
        raise ScreenshotError(message)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical_source_bytes(data):
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return data if b"\x00" in data else data.replace(b"\r\n", b"\n")


def _source_path(path):
    return (path.startswith(SOURCE_DIRS)
            or (path.startswith("app/") and path.count("/") == 1
                and (Path(path).suffix in ROOT_SOURCE_SUFFIXES or Path(path).name in ROOT_SOURCE_NAMES)))


def build_source_digest(tree):
    """Version1 recipe: sorted [path,SHA256(canonical bytes)] compact ASCII JSON."""
    rows = [[path, _sha(canonical_source_bytes(tree[path]))]
            for path in sorted(tree) if _source_path(path)]
    return _sha(json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("ascii"))


def inspect_png(data):
    """Accept only noninterlaced 8-bit RGB/RGBA IHDR, contiguous IDAT, IEND.

    Check every CRC, a single bounded zlib stream, exact decoded row lengths and
    PNG scanline filters0..4. No ancillary chunks, palettes or trailing payload.
    Every encoded byte is structurally accounted for; pixel meaning is not read.
    """
    _require(isinstance(data, bytes) and len(data) <= MAX_PNG_BYTES, "PNG exceeds the 2 MiB file budget")
    _require(data.startswith(PNG_SIGNATURE), "Invalid PNG signature")
    pos, index, compressed, ended = len(PNG_SIGNATURE), 0, bytearray(), False
    width = height = color = None
    seen_idat = False
    while pos < len(data):
        _require(len(data) - pos >= 12, "Truncated PNG chunk")
        length = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        end = pos + length + 12
        _require(end <= len(data), "Truncated PNG chunk payload")
        body = data[pos + 8:pos + 8 + length]
        crc = struct.unpack_from(">I", data, pos + 8 + length)[0]
        _require(zlib.crc32(kind + body) & 0xFFFFFFFF == crc, "Invalid PNG CRC")
        if index == 0:
            _require(kind == b"IHDR" and length == 13, "PNG must start with one IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            _require(0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE and width * height <= MAX_PIXELS,
                     "PNG dimensions exceed the bounded raster contract")
            _require(depth == 8 and color in (2, 6) and compression == filtering == interlace == 0,
                     "PNG must be noninterlaced 8-bit RGB or RGBA")
        elif kind == b"IDAT":
            _require(not ended, "PNG IDAT after IEND")
            seen_idat = True
            compressed.extend(body)
        elif kind == b"IEND":
            _require(length == 0 and seen_idat and end == len(data), "PNG IEND must be empty and final")
            ended = True
        else:
            raise ScreenshotError("PNG contains an unapproved chunk")
        pos, index = end, index + 1
    _require(ended, "PNG IEND missing")
    row_size = 1 + width * (4 if color == 6 else 3)
    expected = height * row_size
    try:
        decoder = zlib.decompressobj()
        raster = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error as exc:
        raise ScreenshotError("Invalid PNG zlib stream") from exc
    _require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and len(raster) == expected,
             "PNG raster size or zlib stream boundary mismatch")
    _require(all(raster[row * row_size] <= 4 for row in range(height)), "Invalid PNG scanline filter")
    return {"sha256": _sha(data), "bytes": len(data), "width": width, "height": height,
            "color_type": color, "decoded_bytes": expected}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ScreenshotError("Nonfinite JSON value")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScreenshotError("Invalid UTF-8 JSON") from exc


def _keys(value, expected, name):
    _require(isinstance(value, dict) and set(value) == set(expected), "Invalid " + name + " fields")


def _path(value, prefix, suffix):
    _require(isinstance(value, str) and value.startswith(prefix) and value.endswith(suffix)
             and "\\" not in value and ":" not in value
             and not any(ord(char) < 32 or char in "*?" for char in value)
             and not any(part in ("", ".", "..") for part in value.split("/")), "Invalid capture path")
    return value


def _digest(value):
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "Invalid SHA256")
    return value


def _file_hash(tree, path, digest, *, source=False):
    _require(path in tree, "Capture companion or source missing: " + path)
    data = tree[path]
    _require(_sha(canonical_source_bytes(data) if source else data) == _digest(digest), "Capture hash drift: " + path)
    return data


def verify_tree(tree, manifest_path=MANIFEST_PATH):
    """Validate PNG/DOM inventory and hashes against the public manifest.

    Returns structural receipts, not publication approval. Unlisted PNGs (also
    renamed PNG bytes), missing companions and changed source inputs fail closed.
    """
    candidates = {path for path, data in tree.items() if path.lower().endswith(".png") or data.startswith(PNG_SIGNATURE)}
    if manifest_path not in tree:
        _require(not candidates, "PNG inventory has no capture manifest")
        return {}
    manifest = _json(tree[manifest_path])
    _keys(manifest, ("schema_version", "fixture", "capture", "app_version", "build", "images"), "capture manifest")
    _require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1, "Unsupported capture manifest version")
    fixture, capture, build = manifest["fixture"], manifest["capture"], manifest["build"]
    _keys(fixture, ("id", "version", "path", "sha256"), "fixture")
    _keys(capture, ("path", "sha256"), "capture script")
    _keys(build, ("source_sha256", "bundle_sha256"), "build")
    _require(fixture["id"] == "documentation-demo" and type(fixture["version"]) is int and fixture["version"] == 1,
             "Unsupported synthetic fixture contract")
    _require(fixture["path"] == FIXTURE_PATH and capture["path"] == CAPTURE_PATH, "Unknown fixture or capture script path")
    _file_hash(tree, fixture["path"], fixture["sha256"], source=True)
    _file_hash(tree, capture["path"], capture["sha256"], source=True)
    _require("app/package.json" in tree, "Application version source missing")
    package = _json(tree["app/package.json"])
    _require(isinstance(manifest["app_version"], str) and manifest["app_version"]
             and isinstance(package, dict) and package.get("version") == manifest["app_version"], "Application version drift")
    _digest(build["bundle_sha256"])
    captured_source = _digest(build["source_sha256"])
    current_source = build_source_digest(tree)
    historical = _sha(tree[manifest_path]) == HISTORICAL_MANIFEST_SHA256
    _require(current_source == captured_source or historical, "Build source digest drift")
    _require(isinstance(manifest["images"], list) and manifest["images"], "Empty or invalid screenshot inventory")
    receipts, dom_paths = {}, set()
    for item in manifest["images"]:
        _keys(item, ("path", "sha256", "dom_path", "dom_sha256", "width", "height", "language", "route"), "screenshot")
        path = _path(item["path"], SCREENSHOT_DIR, ".png")
        dom_path = _path(item["dom_path"], SCREENSHOT_DIR, ".dom.txt")
        _require(path not in receipts and dom_path not in dom_paths, "Duplicate screenshot or DOM companion")
        _require(dom_path == path[:-4] + ".dom.txt", "DOM companion must have the screenshot stem")
        _require(item["language"] in ("it", "en") and isinstance(item["route"], str)
                 and item["route"].startswith("/") and len(item["route"]) <= 240, "Invalid capture language or route")
        for field in ("width", "height"):
            _require(type(item[field]) is int and item[field] > 0, "Invalid manifest dimensions")
        info = inspect_png(_file_hash(tree, path, item["sha256"]))
        _require((info["width"], info["height"]) == (item["width"], item["height"]), "Capture dimensions drift")
        dom = _file_hash(tree, dom_path, item["dom_sha256"])
        try:
            text = dom.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ScreenshotError("DOM companion is not UTF-8") from exc
        _require("\x00" not in text and WATERMARK in text, "DOM companion lacks the synthetic watermark")
        receipts[path] = {**info, "manifest_sha256": _sha(tree[manifest_path]), "dom_path": dom_path,
                          "source_status": "historical" if historical and current_source != captured_source else "current",
                          "captured_source_sha256": captured_source, "current_source_sha256": current_source,
                          "dom_sha256": item["dom_sha256"], "language": item["language"], "route": item["route"]}
        dom_paths.add(dom_path)
    _require(set(receipts) == candidates, "PNG inventory differs from capture manifest")
    return receipts


def load_tree(root):
    """Read only public app sources/capture inputs and the screenshot directory."""
    root = Path(root).resolve(strict=True)
    _require(root.is_dir(), "Verification root is not a directory")
    tree = {}

    def read(path):
        _require(not path.is_symlink() and path.resolve().is_relative_to(root), "Linked capture source refused")
        if path.is_file():
            tree[path.relative_to(root).as_posix()] = path.read_bytes()

    def folder(path):
        if not path.exists():
            return
        _require(not path.is_symlink() and path.resolve().is_relative_to(root), "Linked capture directory refused")
        for child in path.iterdir():
            if child.is_dir():
                folder(child)
            else:
                read(child)

    for name in SOURCE_DIRS + (SCREENSHOT_DIR,):
        folder(root / name)
    app = root / "app"
    if app.is_dir():
        for path in app.iterdir():
            if _source_path(path.relative_to(root).as_posix()):
                read(path)
    for name in (FIXTURE_PATH, CAPTURE_PATH):
        read(root / name)
    return tree


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    try:
        receipts = verify_tree(load_tree(args.root))
    except (OSError, ScreenshotError) as exc:
        print("Screenshot verification failed: " + str(exc))
        return 1
    historical = sum(row["source_status"] == "historical" for row in receipts.values())
    if historical:
        print(f"{historical} historical screenshots captured by {HISTORICAL_CAPTURE_DATE}; "
              "they do not depict the current renderer")
    print(f"{len(receipts)} screenshots structurally verified; pixels require visual review, not secret-scanner certification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
