"""Small fixtures for the structured action-graph gate."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("gate", Path(__file__).with_name("check-aquery.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def fixture(variant="modern", units=16):
    cpu = "x86-64-v3" if variant == "modern" else "x86-64-v2"
    actions = []
    labels = ["//crates/natives:pi_natives", "//crates/natives:oauth_callback_relay", "//dep:a", "//dep:b", "//dep:c"]
    for i, label in enumerate(labels, 1):
        flags = ["--target", gate.TRIPLE, "-Ctarget-cpu=" + cpu, "-Ccodegen-units=" + str(1 if i == 2 else units)]
        if i == 1:
            flags += ["-C", "lto=thin"]
        actions.append({"targetId": i, "configurationId": 1, "mnemonic": "Rustc",
                        "arguments": ["wrapper", "@flags"], "paramFiles": [{"execPath": "flags", "arguments": flags}]})
    actions.append({"targetId": 3, "configurationId": 1, "mnemonic": "CppCompile",
                    "arguments": ["external/+msvc_cc_repository+msvc_cc/bin/clang-cl", "test.cc"]})
    return {"targets": [{"id": i, "label": label} for i, label in enumerate(labels, 1)],
            "configuration": [{"id": 1, "platformName": "win32-x64"}], "actions": actions}


class GateTests(unittest.TestCase):
    def test_clean(self):
        for variant, units in (("baseline", 16), ("modern", 16), ("modern", 1)):
            errors, rust, cpp = gate.inspect(fixture(variant, units), variant, units)
            self.assertEqual(errors, [])
            self.assertEqual((rust, cpp), (5, 1))

    def test_wrong_flags(self):
        graph = fixture()
        graph["actions"][2]["paramFiles"][0]["arguments"] += ["-C", "target-cpu=x86-64-v2"]
        graph["actions"][-1]["arguments"] += ["/arch:AVX512"]
        errors, _, _ = gate.inspect(graph, "modern", 16)
        self.assertTrue(any("CPU flags" in e for e in errors))
        self.assertTrue(any("ISA override" in e for e in errors))

    def test_baseline_leak(self):
        graph = fixture("baseline")
        graph["actions"][2]["paramFiles"][0]["arguments"] += ["-Ctarget-feature=+avx2"]
        self.assertTrue(gate.inspect(graph, "baseline", 16)[0])

    def test_unexpanded_param(self):
        graph = fixture()
        graph["actions"][0]["arguments"] = ["wrapper"]
        with self.assertRaises(ValueError):
            gate.inspect(graph, "modern", 16)


if __name__ == "__main__":
    unittest.main()
