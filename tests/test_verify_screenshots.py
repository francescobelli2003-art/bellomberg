"""Synthetic PNG bytes and public capture provenance; no renderer or private data."""
from copy import deepcopy
import hashlib
import json
import struct
import zlib

import pytest

from docs.guide import verify_screenshots as screenshots


def chunk(kind, body=b""):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(width=2, height=2, color=6, *, filters=None, stream=None, extra=b"", depth=8, interlace=0):
    channels = 4 if color == 6 else 3
    raw = b"".join(bytes([f]) + bytes(width * channels) for f in (filters or [0] * height))
    header = struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw) if stream is None else stream) + extra + chunk(b"IEND")


def capture_tree():
    image_path = "docs/assets/screenshots/demo-en.png"
    dom_path = "docs/assets/screenshots/demo-en.dom.txt"
    fixture_path = "app/tests/fixtures/documentation-demo.json"
    capture_path = "app/tools/capture-docs.cjs"
    tree = {"app/package.json": b'{"version":"0.8.0"}\n',
            "app/src/main.ts": b"export const demo = true;\n",
            fixture_path: b'{"id":"documentation-demo","version":1}\n',
            capture_path: b"// Synthetic capture script\n",
            image_path: png(), dom_path: "DEMO · SYNTHETIC DATA\nSynthetic balance form\n".encode()}
    digest = lambda path: hashlib.sha256(tree[path]).hexdigest()
    manifest = {"schema_version": 1,
                "fixture": {"id": "documentation-demo", "version": 1, "path": fixture_path, "sha256": digest(fixture_path)},
                "capture": {"path": capture_path, "sha256": digest(capture_path)},
                "app_version": "0.8.0",
                "build": {"source_sha256": screenshots.build_source_digest(tree), "bundle_sha256": "a" * 64},
                "images": [{"path": image_path, "sha256": digest(image_path), "dom_path": dom_path,
                            "dom_sha256": digest(dom_path), "width": 2, "height": 2, "language": "en", "route": "/trades"}]}
    tree[screenshots.MANIFEST_PATH] = json.dumps(manifest, ensure_ascii=False).encode()
    return tree, manifest


@pytest.mark.parametrize("color", [2, 6])
@pytest.mark.parametrize("filter_type", [0, 1, 2, 3, 4])
def test_rgb_rgba_and_all_standard_scanline_filters(color, filter_type):
    raw = png(color=color, filters=[filter_type, filter_type])
    result = screenshots.inspect_png(raw)
    assert result["width"] == result["height"] == 2
    assert result["bytes"] == len(raw)
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["color_type"] == color


@pytest.mark.parametrize("bad", [
    b"not a PNG", png()[:-1], png() + b"trailing", png()[:-4] + b"XXXX",
    png(extra=chunk(b"tEXt", b"Author\x00SECRET_SYNTHETIC")),
    png(extra=chunk(b"iTXt", b"metadata")), png(extra=chunk(b"iCCP", b"profile")),
    png(extra=chunk(b"PLTE", b"\x00\x00\x00")), png(depth=16), png(color=0), png(interlace=1),
    png(width=0), png(height=0), png(filters=[5, 0]), png(filters=[0]),
    png(stream=zlib.compress(b"\x00" * 18) + b"hidden"),
    png(stream=zlib.compress(b"\x00" * 18) + zlib.compress(b"hidden")),
    png(stream=zlib.compress(b"\x00" * 18)[:-1]), png(stream=zlib.compress(b"\x00" * 19)),
    png(stream=zlib.compress(b"\x00" * 1_000_000)),
], ids=["signature", "truncated", "trailing", "crc", "text", "itxt", "icc", "palette", "depth",
        "grayscale", "interlace", "width", "height", "filter", "short-raster", "zlib-trailing",
        "second-stream", "zlib-truncated", "extra-raster", "bomb"])
def test_structural_or_hidden_data_is_rejected(bad):
    with pytest.raises(screenshots.ScreenshotError):
        screenshots.inspect_png(bad)


def test_chunk_order_and_extreme_dimensions_are_rejected_without_allocating_pixels():
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0))
    raw = png()
    for bad in (raw[:8] + chunk(b"IDAT", zlib.compress(bytes(18))) + header + chunk(b"IEND"),
                raw[:8] + header + header + raw[33:],
                raw[:8] + chunk(b"IHDR", struct.pack(">IIBBBBB", 0xFFFFFFFF, 0xFFFFFFFF, 8, 6, 0, 0, 0)) + raw[33:]):
        with pytest.raises(screenshots.ScreenshotError):
            screenshots.inspect_png(bad)


def test_contiguous_idat_chunks_form_one_exact_zlib_stream():
    raw = png()
    length = struct.unpack_from(">I", raw, 33)[0]
    compressed = raw[41:41 + length]
    split = raw[:33] + chunk(b"IDAT", compressed[:3]) + chunk(b"IDAT", compressed[3:]) + chunk(b"IEND")
    result = screenshots.inspect_png(split)
    assert result["decoded_bytes"] == 18
    assert result["bytes"] == len(raw) + 12


def test_valid_png_still_cannot_exceed_the_two_mib_file_budget():
    raw = bytes(512 * (1 + 1024 * 4))
    large = png(width=1024, height=512, stream=zlib.compress(raw, level=0))
    assert len(large) > 2 * 1024 * 1024
    with pytest.raises(screenshots.ScreenshotError, match="budget"):
        screenshots.inspect_png(large)


def test_manifest_binds_image_dom_source_capture_and_version():
    tree, manifest = capture_tree()
    before = deepcopy(tree)
    receipts = screenshots.verify_tree(tree)
    row = manifest["images"][0]
    assert set(receipts) == {row["path"]}
    assert receipts[row["path"]]["bytes"] == len(tree[row["path"]])
    assert receipts[row["path"]]["manifest_sha256"] == hashlib.sha256(tree[screenshots.MANIFEST_PATH]).hexdigest()
    assert tree == before


@pytest.mark.parametrize("change", ["png", "dom", "dom_missing", "watermark", "capture", "fixture", "source",
                                   "version", "width", "language", "duplicate", "unknown_png", "renamed_png",
                                   "missing_manifest", "traversal", "bundle_hash"])
def test_manifest_drift_or_missing_provenance_never_passes(change):
    tree, manifest = capture_tree()
    entry = manifest["images"][0]
    if change == "png": tree[entry["path"]] = png(color=2)
    elif change == "dom": tree[entry["dom_path"]] += b"changed"
    elif change == "dom_missing": del tree[entry["dom_path"]]
    elif change == "watermark":
        tree[entry["dom_path"]] = b"Synthetic data, missing watermark"
        entry["dom_sha256"] = hashlib.sha256(tree[entry["dom_path"]]).hexdigest()
    elif change in ("capture", "fixture"): tree[manifest[change]["path"]] += b"changed"
    elif change == "source": tree["app/src/new.ts"] = b"new source"
    elif change == "version": manifest["app_version"] = "other"
    elif change == "width": entry["width"] = 3
    elif change == "language": entry["language"] = "unknown"
    elif change == "duplicate": manifest["images"].append(deepcopy(entry))
    elif change == "unknown_png": tree["other.png"] = png()
    elif change == "renamed_png": tree["other.txt"] = png()
    elif change == "traversal": entry["dom_path"] = "docs/assets/screenshots/../../../private.txt"
    elif change == "bundle_hash": manifest["build"]["bundle_sha256"] = "not a SHA256"
    tree[screenshots.MANIFEST_PATH] = json.dumps(manifest).encode()
    if change == "missing_manifest": del tree[screenshots.MANIFEST_PATH]
    with pytest.raises(screenshots.ScreenshotError):
        screenshots.verify_tree(tree)


def test_duplicate_json_keys_and_bool_dimensions_are_rejected():
    tree, manifest = capture_tree()
    tree[screenshots.MANIFEST_PATH] = tree[screenshots.MANIFEST_PATH].replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
    with pytest.raises(screenshots.ScreenshotError): screenshots.verify_tree(tree)
    manifest["images"][0]["width"] = True
    tree[screenshots.MANIFEST_PATH] = json.dumps(manifest).encode()
    with pytest.raises(screenshots.ScreenshotError): screenshots.verify_tree(tree)


def test_source_digest_is_checkout_portable_but_binary_and_added_sources_are_bound():
    tree, _ = capture_tree()
    first = screenshots.build_source_digest(tree)
    changed = {path: data.replace(b"\n", b"\r\n") if path.endswith((".ts", ".json", ".cjs")) else data for path, data in tree.items()}
    assert screenshots.build_source_digest(changed) == first
    changed["docs/assets/screenshots/unused.dom.txt"] = b"ignored by build hash"
    assert screenshots.build_source_digest(changed) == first
    changed["app/public/icon.png"] = png()
    assert screenshots.build_source_digest(changed) != first


def test_empty_inventory_is_explicit_and_cli_never_reads_runtime(tmp_path, capsys, monkeypatch):
    from pathlib import Path
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "private.db").write_bytes(b"DO NOT READ")
    observed = []
    read_bytes = Path.read_bytes
    def measured_read(path):
        observed.append(path)
        assert not path.resolve().is_relative_to((tmp_path / "data").resolve())
        return read_bytes(path)
    monkeypatch.setattr(Path, "read_bytes", measured_read)
    assert screenshots.main(["--root", str(tmp_path)]) == 0
    assert "0 screenshots" in capsys.readouterr().out
    tree, _ = capture_tree()
    for name, data in tree.items():
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    assert screenshots.main(["--root", str(tmp_path)]) == 0
    assert "1 screenshots" in capsys.readouterr().out
    (tmp_path / "docs/assets/screenshots/demo-en.png").write_bytes(b"changed")
    assert screenshots.main(["--root", str(tmp_path)]) == 1
    assert any(path.name == "demo-en.dom.txt" for path in observed)
    assert any(path.name == "main.ts" for path in observed)
