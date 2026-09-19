"""The documentation account is invented, consistent, and reproducible offline."""
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs/guide/generate_documentation_demo.py"


class DocumentationDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert SCRIPT.is_file(), "The documented DEMO generator must be included in the source export"
        spec = importlib.util.spec_from_file_location("documentation_demo", SCRIPT)
        cls.generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.generator)

    def setUp(self):
        self.source = self.generator.load_source()
        self.fixture = self.generator.generate(self.source)

    def test_reproduction_does_not_modify_source_and_matches_committed_fixture(self):
        before = copy.deepcopy(self.source)
        self.assertEqual(self.fixture, self.generator.generate(self.source))
        self.assertEqual(before, self.source)
        expected = self.generator.decode_fixture(self.generator.strict_json(
            (ROOT / "app/tests/fixtures/documentation-demo.json").read_text(encoding="utf-8")))
        self.assertEqual(expected, self.fixture)

    def test_snapshot_is_a_fixed_point_and_codec_is_lossless(self):
        source = {**self.source, "fixture_template": copy.deepcopy(self.fixture)}
        self.assertEqual(self.generator.generate(source), self.fixture)
        encoded = self.generator.encode_fixture(self.fixture)
        decoded = self.generator.decode_fixture(encoded)
        self.assertEqual(decoded, self.fixture)
        self.assertEqual(json.dumps(decoded, ensure_ascii=False), json.dumps(self.fixture, ensure_ascii=False))
        # Authored snapshot after explicit timeline, display and VaR-precision corrections.
        canonical = json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(), "26b591ab79f03136f90a4d586cb019f0f4396eee8b195a24fa774f329a9d02df")

    def test_mandate_display_snapshot_matches_source_without_changing_values(self):
        # Read literal descriptions only: no application import, DB or provider.
        source = ast.parse((ROOT / "src/bellomberg/core/mandato_pm.py").read_text(encoding="utf-8"))
        assignments = {node.targets[0].id: node.value for node in source.body
                       if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
        fields = next(node.value for node in source.body
                      if isinstance(node, ast.AnnAssign) and node.target.id == "CAMPI")
        english = ast.literal_eval(assignments["_FIELD_DESCRIPTIONS_EN"])
        mandate = self.fixture["responses"]["GET /mandato"]
        for field in ("tipo_investimento", "stile"):
            call = next(value for key, value in zip(fields.keys, fields.values) if ast.literal_eval(key) == field)
            italian = ast.literal_eval(call.args[3])
            self.assertEqual(mandate["campi"][field]["descrizione"], italian)
            variants = next(item for item in mandate["_presentation_v1"]["texts"]
                            if item["path"] == ["campi", field, "descrizione"])
            self.assertEqual((variants["it"], variants["en"]), (italian, english[field]))
        original = self.source["fixture_template"]["responses"]["GET /mandato"]
        for key in ("valori", "esempio", "impronta"):
            self.assertEqual(mandate[key], original[key])

    def test_authored_demo_labels_and_risk_precision(self):
        r = self.fixture["responses"]
        market = r["GET /market/overview?country=US"]
        labels = {tuple(item["path"]): item["en"] for item in market["_presentation_v1"]["texts"]}
        self.assertEqual(labels[("commodities", 0, "name")], "Gold DEMO")
        self.assertEqual(labels[("commodities", 4, "name")], "Natural gas DEMO")
        beta = r["GET /portfolio/metrics/beta_reconcile"]
        self.assertNotIn(" (fix ", beta["definitions"]["portfolio_risk_spy"])
        twr = r["GET /portfolio/analytics/twr"]
        note = next(item for item in twr["_presentation_v1"]["texts"] if item["path"] == ["notes", 0])
        self.assertTrue(note["it"].startswith("1 movimento del ledger precede "))
        self.assertTrue(note["en"].startswith("1 ledger movement precedes "))
        preview = r["POST /mandato/anteprima"]
        self.assertIn("declared on unavailable date", preview["testo"])
        self.assertIn("broker: n/a; tax residence: n/a;", preview["testo"])
        var = r["GET /portfolio/analytics/var_contribution?confidence=0.05&lookback_days=252"]
        self.assertEqual(var["portfolio_var_eur_daily"], 72.88)
        self.assertAlmostEqual(var["portfolio_var_eur_daily"], sum(item["component_var_eur"] for item in var["items"]), places=2)

    def test_observation_times_precede_clock_but_future_schedule_and_expiries_remain(self):
        self.generator.validate_observation_times(self.fixture)
        r = self.fixture["responses"]
        tasks = r["GET /tasks/scheduled"]["tasks"]
        updater = next(t for t in tasks if t["TaskName"] == "Bellomberg-PriceUpdater")
        self.assertEqual(updater["LastRunTime"], "09/13/2026 10:05:00")
        self.assertEqual(updater["NextRunTime"], "09/13/2026 10:20:00")
        self.assertEqual(tasks[0]["NextRunTime"], "09/13/2026 23:00:00")
        self.assertIn("2026-10-16", json.dumps(r["GET /options/expiry_catalog/DEMO.V"]))
        self.assertTrue(r["GET /health"]["timestamp"].startswith("2026-09-13T10:08:"))
        note = next(t for t in r["GET /portfolio"]["_presentation_v1"]["texts"]
                    if t["path"] == ["totale_aperto_piu_realizzato_note"])["en"]
        self.assertIn("Synthetic DEMO", note)
        self.assertIn("no real account was measured", note)
        briefing = r["GET /news/briefing/current"]
        self.assertEqual(briefing["period"], "morning")
        cutoff = self.generator.datetime.fromisoformat(self.fixture["clock"]["now"])
        generated = self.generator.datetime.fromisoformat(briefing["generated_at"])
        self.assertEqual((cutoff - generated).total_seconds(), briefing["age_minutes"] * 60)
        edge = r["GET /signals/edge_scan?min_strength=45"]
        scanned = self.generator.datetime.fromisoformat(edge["cache"]["scansione_delle"])
        self.assertEqual((cutoff - scanned).total_seconds(), edge["cache"]["eta_s"])
        self.assertEqual(edge["generated"], edge["cache"]["scansione_delle"])
        self.assertIn("No empirical backtest is claimed", json.dumps(edge))
        self.assertIn("no live request", json.dumps(r["GET /news/economic-calendar?days_ahead=14"]))
        scheduled = copy.deepcopy(r)
        calendar = scheduled["GET /news/economic-calendar?days_ahead=14"]
        calendar["test_event"] = {"date": "2026-09-13T19:30:00", "timestamp": "2026-09-13T19:20:00"}
        self.generator._demo_timeline(scheduled)
        self.assertEqual(calendar["test_event"]["date"], "2026-09-13T19:30:00")
        self.assertEqual(calendar["test_event"]["timestamp"], "2026-09-13T09:20:00")
        for field, value in [("timestamp", "2026-09-13T10:11:41"),
                             ("observed_at", "2026-09-13T08:11:41Z"),
                             ("updated_at", "2026-09-13T10:11:41+02:00"),
                             ("LastRunTime", "09/13/2026 10:11:41")]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Future DEMO observation"):
                changed = copy.deepcopy(self.fixture)
                changed["responses"]["GET /health"][field] = value
                self.generator.validate_observation_times(changed)

    def test_references_are_independent_and_text_remains_scannable(self):
        text = "Privacy scanners can read this complete authored sentence. " * 4
        original = {"first": {"text": text}, "second": {"text": text}}
        encoded = self.generator.encode_fixture(original)
        self.assertIn(text, json.dumps(encoded, ensure_ascii=False))
        decoded = self.generator.decode_fixture(encoded)
        decoded["first"]["text"] = "changed"
        self.assertEqual(decoded["second"]["text"], text)

    def test_artifact_paths_use_a_neutral_synthetic_root(self):
        r = self.fixture["responses"]
        for field in ("pdf_path", "appendix_path"):
            path = self.generator.PureWindowsPath(r["GET /memos/4"][field])
            self.assertEqual(path.parts[:2], ("C:\\", "DemoTerminal"))
            self.assertEqual(path.suffix, ".pdf")
        price_path = r["GET /portfolio/analytics/liquidity"]["negozio_prezzi"]["origine"]
        self.assertEqual(self.generator.PureWindowsPath(price_path).parts[:2], ("C:\\", "DemoTerminal"))

    def test_demo_cohorts_preserve_equality_nulls_and_other_fields(self):
        keys = ["a" * 64, "b" * 64, "a" * 64, None]
        data = [{"comparison_key": key, "value": index, "api_key": "untouched test marker"}
                for index, key in enumerate(keys)]
        before = copy.deepcopy(data)
        self.generator._demo_cohort_ids(data)
        for i, row in enumerate(data):
            self.assertEqual({k: v for k, v in row.items() if k != "comparison_key"},
                             {k: v for k, v in before[i].items() if k != "comparison_key"})
            for j in range(len(data)):
                self.assertEqual(keys[i] == keys[j], row["comparison_key"] == data[j]["comparison_key"])
        self.assertIsNone(data[-1]["comparison_key"])
        mapped = copy.deepcopy(data)
        self.generator._demo_cohort_ids(data)
        self.assertEqual(mapped, data)
        for invalid in [["a" * 64, "demo-cohort-01"], [1], ["unknown"],
                        ["demo-cohort-02"], ["demo-cohort-00"]]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.generator._demo_cohort_ids([{"comparison_key": key} for key in invalid])

    def test_readable_serialization_separates_values_and_tables_only_save_bytes(self):
        original = {"numbers": [1, 2], "tiny": [{"a": 1}, {"a": 2}], "table": [
            {"long_documented_column_one": 1, "long_documented_column_two": 2},
            {"long_documented_column_one": 3, "long_documented_column_two": 4}]}
        encoded = self.generator.encode_fixture(original)
        text = self.generator.serialize_fixture(encoded)
        self.assertIn('[1, 2]', text)
        self.assertIn('\n"definitions":', text)
        self.assertNotIn('$demo_table', json.dumps(encoded["document"]["tiny"]))
        self.assertIn('$demo_table', encoded["document"]["table"])
        self.assertEqual(self.generator.decode_fixture(self.generator.strict_json(text)), original)

    def test_codec_rejects_invalid_references_cycles_and_ambiguous_tables(self):
        def envelope(document, definitions=None):
            return {"schema": "documentation-local-refs/1", "definitions": definitions or {}, "document": document}
        ref = {"$demo_ref": "aaaaaaaaaaaa"}
        bad = [envelope(ref), envelope({**ref, "extra": 1}),
               envelope(ref, {"aaaaaaaaaaaa": ref}),
               envelope(ref, {"aaaaaaaaaaaa": {"$demo_ref": "bbbbbbbbbbbb"}, "bbbbbbbbbbbb": ref}),
               envelope({}, {"aaaaaaaaaaaa": ref}),
               envelope({"$demo_table": {"columns": ["a", "a"], "rows": [[1, 2]]}}),
               envelope({"$demo_table": {"columns": ["a"], "rows": [[1, 2]]}}),
               envelope({"$demo_table": {"columns": ["$demo_ref"], "rows": [["aaaaaaaaaaaa"]]}}),
               envelope({"$demo_table": {"columns": ["a"], "rows": [[1]], "extra": 2}}),
               {"schema": "documentation-local-refs/2", "document": {}, "definitions": {}}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.generator.decode_fixture(value)
        for value in ['{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"a":NaN}']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.generator.strict_json(value)

    def test_one_book_reconciles_holdings_cash_trade_links_and_analytics(self):
        self.generator.validate(self.fixture)
        r = self.fixture["responses"]
        p = r["GET /portfolio"]
        self.assertEqual({x["ticker"]: x["quantita"] for x in p["positions"]}, {"DEMO.A": 80, "DEMO.B": 40})
        self.assertEqual(p["totale_valore_mercato_eur"], 6000)
        self.assertEqual(p["cash_disponibile_eur"], 4000)
        self.assertEqual(p["nav_total_eur"], 10000)
        trades = r["GET /trades"]["trades"]
        self.assertEqual(sum(t["quantita"] * t["prezzo"] for t in trades), 5500)
        executed = next(d for d in r["GET /decisions?limit=500"]["decisions"] if d["id"] == 22)
        self.assertEqual(executed["esecuzione"]["trade_ids"], [1, 3])
        self.assertEqual(executed["esecuzione"]["eur"], 3600)

    def test_observed_upside_uses_the_demo_quote_and_stale_quote_stays_unusable(self):
        models = self.fixture["responses"]["GET /fundamentals/models"]["models"]
        usable, blocked = models
        self.assertEqual(usable["market_quote"]["price"], 50)
        self.assertAlmostEqual(usable["upside_today_pct"], 11.8)
        self.assertEqual(usable["market_quote"]["status_at_read"], "ok")
        self.assertNotIn("status_at_read", usable["detail"]["market_quote"])
        self.assertEqual(blocked["market_quote"]["status_at_read"], "stale")
        self.assertIsNone(blocked["upside_today_pct"])

    def test_every_destination_has_a_working_english_scene_and_real_surface(self):
        self.assertEqual(self.fixture["languages"], ["en"])
        self.assertEqual(len(self.fixture["scenes"]), 31)
        self.assertEqual({s["route"] for s in self.fixture["scenes"]}, self.generator.PAGE_ROUTES)
        by_id = {s["id"]: s for s in self.fixture["scenes"]}
        self.assertIn("Settings", json.dumps(by_id["settings"]["steps"]))
        self.assertIn("[data-vol-3d]", by_id["vol-deck-tools"]["selector"])
        self.assertTrue(all(s["required"] for s in self.fixture["scenes"]))

    def test_corrupt_cross_page_evidence_is_rejected(self):
        mutations = [
            ("GET /portfolio", lambda p: p["positions"][0].__setitem__("quantita", 81)),
            ("GET /trades", lambda p: p["trades"][0].__setitem__("prezzo", 99)),
            ("GET /cash/movements", lambda p: p["movements"][0].__setitem__("amount_eur", 9501)),
            ("GET /market/quote?ticker=DEMO.A", lambda p: p.__setitem__("price", 51)),
            ("GET /portfolio/analytics/nav_history", lambda p: p.__setitem__("final_nav_eur", 6001)),
            ("GET /decisions?limit=500", lambda p: next(d for d in p["decisions"] if d["id"] == 22)["esecuzione"].__setitem__("eur", 3601)),
            ("POST /trade/preview", lambda p: p.__setitem__("cash_disponibile_eur", 4000)),
            ("GET /decisions?limit=500", lambda p: next(d for d in p["decisions"] if d["id"] == 22)["esecuzione"]["trades"][0].__setitem__("quantita", 61)),
            ("GET /portfolio/risk", lambda p: p["per_asset"]["DEMO.A"].__setitem__("weight_pct", 50)),
            ("GET /portfolio/factors?period=3y", lambda p: p["per_holding"]["DEMO.A"].__setitem__("weight", 0.5)),
            ("GET /portfolio/analytics/liquidity", lambda p: p["items"][0].__setitem__("position_eur", 3999)),
            ("GET /news/briefing/current", lambda p: p["portfolio_top"][0].__setitem__("pnl_pct", 99)),
        ]
        for endpoint, mutation in mutations:
            with self.subTest(endpoint=endpoint):
                fixture = copy.deepcopy(self.fixture)
                mutation(fixture["responses"][endpoint])
                with self.assertRaises(ValueError):
                    self.generator.validate(fixture)

    def test_check_mode_refuses_drift_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixture.json"
            self.assertEqual(self.generator.main(["--output", str(output)]), 0)
            expected = output.read_bytes()
            self.assertEqual(self.generator.main(["--output", str(output), "--check"]), 0)
            output.write_bytes(expected + b" ")
            self.assertEqual(self.generator.main(["--output", str(output), "--check"]), 1)
            self.assertEqual(output.read_bytes(), expected + b" ")


if __name__ == "__main__":
    unittest.main()
