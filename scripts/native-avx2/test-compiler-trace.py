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

    def scoped_fixture(self, variant, override, source, output, defines=(), cargo=True):
        import json
        flags = ["-msse4.1", "-msse4.2"] if variant == "baseline" else ["/clang:-march=x86-64-v3"]
        root = self.fixture(flags, cargo=cargo)
        args = ["clang-cl", "--target=x86_64-pc-windows-msvc", *flags, override, *defines, "-Fo" + output, "-c", "--", source]
        (root / "exec.101").write_text('execve("/llvm/bin/clang-cl", ' + json.dumps(args) + ', 0x123 /* 2 vars */) = 0\n')
        return root

    def test_exact_blake3_probe(self):
        source = "/execroot/external/rules_rust++crate+crates__blake3-1.8.7/_bs.out_dir/flag_check.c"
        for variant in ("baseline", "modern"):
            result = gate.inspect(self.scoped_fixture(variant, "/arch:AVX512", source, source[:-2]), variant)
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["classified_isa_scopes"], {"blake3-compiler-capability-probe": 1})

    def test_blake3_real_unit_and_wrong_output_rejected(self):
        source = "/execroot/external/rules_rust++crate+crates__blake3-1.8.7/_bs.out_dir/flag_check.c"
        for candidate, output in [(source.replace("flag_check.c", "blake3_avx512.c"), source[:-2]), (source, "library.obj")]:
            result = gate.inspect(self.scoped_fixture("modern", "/arch:AVX512", candidate, output), "modern")
            self.assertTrue(any("Conflicting" in e for e in result["errors"]))

    def test_exact_opus_probe(self):
        source = "/execroot/external/rules_rust++crate+crates__opusic-sys-0.7.5/_bs.out_dir/build/CMakeFiles/CMakeScratch/TryCompile-4n9KlK/src.c"
        result = gate.inspect(self.scoped_fixture("modern", "/arch:AVX2", source, "CMakeFiles/cmTC_44c9d.dir/src.c.obj", ["-DAVX2_SUPPORTED"]), "modern")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["classified_isa_scopes"], {"opus-cmake-capability-probe": 1})

    def test_exact_opus_specializations(self):
        root = "/execroot/external/rules_rust++crate+crates__opusic-sys-0.7.5/_bs.cargo_runfiles/rules_rust++crate+crates__opusic-sys-0.7.5/opus/"
        for variant in ("baseline", "modern"):
            for unit in ("celt/x86/pitch_avx.c", "silk/x86/NSQ_del_dec_avx2.c", "silk/float/x86/inner_product_FLP_avx2.c"):
                result = gate.inspect(self.scoped_fixture(variant, "/arch:AVX2", root + unit, "CMakeFiles/opus.dir/" + unit + ".obj", ["-DOPUS_HAVE_RTCD", "-DOPUS_X86_MAY_HAVE_AVX2"]), variant)
                self.assertEqual(result["errors"], [])
                self.assertEqual(result["classified_isa_scopes"], {"opus-runtime-dispatch-translation-unit": 1})

    def test_reject_unscoped_opus_flags(self):
        root = "/execroot/external/rules_rust++crate+crates__opusic-sys-0.7.5/_bs.cargo_runfiles/rules_rust++crate+crates__opusic-sys-0.7.5/opus/"
        unit = "celt/x86/pitch_avx.c"
        good = ["-DOPUS_HAVE_RTCD", "-DOPUS_X86_MAY_HAVE_AVX2"]
        for override, source, defines, cargo in [
            ("/arch:AVX512", unit, good, True),
            ("/arch:AVX2", "celt/ordinary.c", good, True),
            ("/arch:AVX2", unit, good[:1], True),
            ("/arch:AVX2", unit, good, False),
        ]:
            result = gate.inspect(self.scoped_fixture("modern", override, root + source, "CMakeFiles/opus.dir/" + source + ".obj", defines, cargo), "modern")
            self.assertTrue(any("Conflicting" in e for e in result["errors"]))

    def test_truncated_compiler_argv(self):
        result = gate.parse_exec('execve("/llvm/bin/clang-cl", ["clang-cl", ...], 0x123 /* 2 vars */) = 0')
        self.assertEqual(result, ("/llvm/bin/clang-cl", None))


if __name__ == "__main__":
    unittest.main()
