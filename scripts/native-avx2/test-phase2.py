#!/usr/bin/env python3
"""Static regression tests for the single-variable H0 Phase 2 overlay."""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("phase2-overlay.py")
spec = importlib.util.spec_from_file_location("phase2_overlay", SCRIPT)
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


class Phase2Tests(unittest.TestCase):
    def test_h0_config_is_one_variable(self):
        self.assertEqual(overlay.CONFIG, {
            "cpu": "baseline", "target": "win32-x64-baseline", "lto": "fat", "cgu": 16, "llvm": "20.1.7"
        })

    def test_overlay_changes_only_lto_transition(self):
        before = '_ADDON_RUSTC_FLAGS = ["-Ccodegen-units=16"]\n\nreturn {\n' + overlay.OLD + '}\n'
        after = overlay.patched_defs(before)
        self.assertEqual(after, before.replace(overlay.OLD, overlay.NEW))
        self.assertEqual(after.count('"@rules_rust//rust/settings:lto": "fat"'), 1)
        self.assertIn('"-Ccodegen-units=16"', after)

    def test_overlay_fails_closed_on_missing_or_duplicate_anchor(self):
        with self.assertRaises(ValueError):
            overlay.patched_defs('lto = "thin"')
        with self.assertRaises(ValueError):
            overlay.patched_defs(overlay.OLD + overlay.OLD)

    def test_build_script_uses_existing_structured_aquery_gate(self):
        script = Path(__file__).with_name("phase2-build.sh").read_text()
        self.assertIn("check-aquery.py", script)
        self.assertIn("--variant baseline --codegen-units 16 --lto fat", script)
        self.assertIn("natives-win32-x64-baseline", script)

    def test_build_script_is_baseline_fat_and_single_job(self):
        script = Path(__file__).with_name("phase2-build.sh").read_text()
        self.assertIn("natives-win32-x64-baseline", script)
        self.assertIn("--lto fat", script)
        self.assertIn("jobs=1", script)
        self.assertNotIn('apply-overlay.py" --source', script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
