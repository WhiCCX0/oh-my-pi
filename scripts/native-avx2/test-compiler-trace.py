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

    def wrapper_fixture(self, flags, driver="/execroot/_main/external/+llvm_msvc_tools_repository+llvm_msvc_tools/bin/clang-cl", separate_pid=False, wrapper_target=True):
        import json
        root = self.fixture(flags)
        wrapper = "/execroot/_main/external/+msvc_cc_repository+msvc_cc/bin/clang-cl"
        args = ["clang-cl"] + (["--target=x86_64-pc-windows-msvc"] if wrapper_target else []) + ["/c", "file.c"]
        def line(path, argv):
            return "execve(" + json.dumps(path) + ", " + json.dumps(argv) + ", 0x123 /* 2 vars */) = 0\n"
        (root / "exec.101").write_text(line(wrapper, args))
        if driver is not None:
            argv = ["clang-cl", "--target=x86_64-pc-windows-msvc", *flags, "/c", "file.c"]
            with (root / ("exec.102" if separate_pid else "exec.101")).open("a") as f:
                f.write(line(driver, argv))
        return root

    def test_wrapper_handoff_baseline(self):
        result = gate.inspect(self.wrapper_fixture(["-msse4.1", "-msse4.2"]), "baseline")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["compiler_invocations"], 1)
        self.assertEqual(result["wrapper_invocations"], 1)
        self.assertEqual(result["wrapper_handoffs"], 1)
        self.assertIn("msvc_cc/bin/clang-cl", result["records"][0]["wrapper"])

    def test_wrapper_handoff_modern_without_explicit_target(self):
        result = gate.inspect(self.wrapper_fixture(["/clang:-march=x86-64-v3"], wrapper_target=False), "modern")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["wrapper_handoffs"], 1)

    def test_wrapper_does_not_exempt_missing_isa(self):
        for variant in ("baseline", "modern"):
            with self.subTest(variant=variant):
                result = gate.inspect(self.wrapper_fixture([]), variant)
                self.assertTrue(any("missing" in e and "default" in e for e in result["errors"]))
                self.assertEqual(result["wrapper_handoffs"], 1)

    def test_wrapper_without_handoff(self):
        result = gate.inspect(self.wrapper_fixture([], driver=None), "baseline")
        self.assertTrue(any("no observed LLVM driver handoff" in e for e in result["errors"]))

    def test_wrapper_wrong_driver(self):
        result = gate.inspect(self.wrapper_fixture(["-msse4.1", "-msse4.2"], driver="/other/bin/clang-cl"), "baseline")
        self.assertTrue(any("did not exec the expected" in e for e in result["errors"]))

    def test_wrapper_other_pid_does_not_count(self):
        result = gate.inspect(self.wrapper_fixture(["-msse4.1", "-msse4.2"], separate_pid=True), "baseline")
        self.assertTrue(any("no observed LLVM driver handoff" in e for e in result["errors"]))

    def test_truncated_compiler_argv(self):
        result = gate.parse_exec('execve("/llvm/bin/clang-cl", ["clang-cl", ...], 0x123 /* 2 vars */) = 0')
        self.assertEqual(result, ("/llvm/bin/clang-cl", None))


if __name__ == "__main__":
    unittest.main()
