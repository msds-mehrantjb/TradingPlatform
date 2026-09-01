"""Regression cover for the two faults that silently emptied the dataset export.

Both bugs aborted ``export_backtest_dataset_from_store`` *after* it had created its
output directory, so each failed daily refresh left an empty dataset directory behind
and the training pipeline quietly starved.

1. A manifest key that is absent yields ``Path("")``, which normalises to ``Path(".")``.
   That directory exists, so an ``exists()`` guard passed and the subsequent ``open()``
   raised ``PermissionError``.
2. Manifests record absolute paths. Moving the project out of its old OneDrive location
   left every historical manifest pointing at files that no longer exist, which zeroed
   the base coverage and tripped the history-preservation guard.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.main import read_jsonl_if_exists, resolve_manifest_file


class ReadJsonlIfExistsTest(unittest.TestCase):
    def test_blank_path_returns_empty_instead_of_raising(self) -> None:
        self.assertEqual(read_jsonl_if_exists(Path("")), [])

    def test_directory_path_returns_empty_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_jsonl_if_exists(Path(tmp)), [])

    def test_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_jsonl_if_exists(Path(tmp) / "absent.jsonl"), [])

    def test_real_file_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            path.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")

            self.assertEqual(read_jsonl_if_exists(path), [{"a": 1}, {"a": 2}])


class ResolveManifestFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dataset_dir = Path(self._tmp.name) / "SPY" / "20260717T204525Z"
        self.dataset_dir.mkdir(parents=True)
        self.manifest_path = self.dataset_dir / "manifest.json"
        self.manifest_path.write_text(json.dumps({}), encoding="utf-8")
        self.candles = self.dataset_dir / "continuous_1m.jsonl"
        self.candles.write_text('{"close": 1.0}\n', encoding="utf-8")

    def test_recorded_path_is_used_when_it_still_exists(self) -> None:
        base_files = {"continuous1mJsonl": str(self.candles)}

        resolved = resolve_manifest_file(base_files, "continuous1mJsonl", str(self.manifest_path))

        self.assertEqual(resolved, self.candles)

    def test_relocated_repository_falls_back_to_the_manifest_directory(self) -> None:
        stale = r"C:\Users\someone\OneDrive\docs\Trading\backend\data\backtests\SPY\20260717T204525Z\continuous_1m.jsonl"
        base_files = {"continuous1mJsonl": stale}

        resolved = resolve_manifest_file(base_files, "continuous1mJsonl", str(self.manifest_path))

        self.assertEqual(resolved, self.candles)
        self.assertEqual(read_jsonl_if_exists(resolved), [{"close": 1.0}])

    def test_absent_key_recovers_the_file_by_its_conventional_name(self) -> None:
        resolved = resolve_manifest_file({}, "continuous1mJsonl", str(self.manifest_path))

        self.assertEqual(resolved, self.candles)

    def test_absent_key_with_no_file_on_disk_yields_a_blank_path(self) -> None:
        resolved = resolve_manifest_file({}, "continuous15mJsonl", str(self.manifest_path))

        self.assertEqual(str(resolved), ".")
        self.assertEqual(read_jsonl_if_exists(resolved), [])

    def test_unknown_key_without_a_conventional_name_yields_a_blank_path(self) -> None:
        resolved = resolve_manifest_file({}, "somethingElseJsonl", str(self.manifest_path))

        self.assertEqual(read_jsonl_if_exists(resolved), [])

    def test_missing_manifest_path_does_not_raise(self) -> None:
        resolved = resolve_manifest_file({"continuous1mJsonl": "/nowhere/continuous_1m.jsonl"}, "continuous1mJsonl", None)

        self.assertEqual(read_jsonl_if_exists(resolved), [])


if __name__ == "__main__":
    unittest.main()
