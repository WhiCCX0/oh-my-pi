import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("trace_gate", Path(__file__).with_name("check-compiler-trace.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class TraceTests(unittest.TestCase):
    def fixture(self, flags, cargo=True, response=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        parent = "/tools/cargo_build_script_runner" if cargo else "/tools/other"
        (root / "exec.100").write_text(
            f'execve("{parent}", ["runner"], 0x123 /* 2 vars */) = 0\n'
            'clone(child_stack=NULL, flags=SIGCHLD) = 101\n'
        )
        args = ["clang-cl", "--target=x86_64-pc-windows-msvc", *flags, "/c", "file.c"]
        if response:
            args.append("@hidden.rsp")
        import json
        (root / "exec.101").write_text(
            'execve("/llvm/bin/clang-cl", ' + json.dumps(args) + ', 0x123 /* 2 vars */) = 0\n'
        )
        return root

    def test_observed_cargo_baseline(self):
        result = gate.inspect(self.fixture(["-msse4.1", "-msse4.2", "-mavx2"]), "baseline")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["cargo_descendants"], 1)
        self.assertEqual(result["specialized_invocations"], 1)

    def test_observed_cargo_modern(self):
        result = gate.inspect(self.fixture(["/clang:-march=x86-64-v3"]), "modern")
        self.assertEqual(result["errors"], [])

    def test_conflicting_modern(self):
        result = gate.inspect(self.fixture(["/clang:-march=x86-64-v3", "/arch:AVX512"]), "modern")
        self.assertTrue(any("Conflicting" in error for error in result["errors"]))

    def test_no_cargo_ancestry(self):
        result = gate.inspect(self.fixture(["-msse4.1", "-msse4.2"], cargo=False), "baseline")
        self.assertTrue(any("descended" in error for error in result["errors"]))

    def test_unexpanded_response_file(self):
        result = gate.inspect(self.fixture(["/clang:-march=x86-64-v3"], response=True), "modern")
        self.assertTrue(any("response file" in error for error in result["errors"]))

    def test_truncated_compiler_argv(self):
        result = gate.parse_exec('execve("/llvm/bin/clang-cl", ["clang-cl", ...], 0x123 /* 2 vars */) = 0')
        self.assertEqual(result, ("/llvm/bin/clang-cl", None))


if __name__ == "__main__":
    unittest.main()
