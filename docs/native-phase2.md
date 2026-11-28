# Phase 2: H0 baseline Fat LTO

## Fixed experiment boundary

- Upstream source: can1357/oh-my-pi commit b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac (v19.2.1).
- Localization overlay: ac1616560eb7d04d2a58afe97dcec88f310a1ad8.
- Phase 1 experiment snapshot: 090ade3669821d60b5aa161e47984342ecb85c1f.
- This Phase 2 work is isolated on phase2/h0-baseline-fat-lto; it does not modify main or rerun the Phase 1 matrix.

## Candidate definitions

| Group | CPU target | LTO | CGU | Role |
|---|---|---|---:|---|
| A1 | baseline | Thin | 16 | Existing self-built baseline control |
| H0 | baseline | Fat | 16 | New single-variable LTO comparison |
| H16 | x86-64-v3 | Fat | 16 | Existing Phase 1 Fat LTO comparison |

H0's overlay changes exactly one upstream setting in bazel/defs.bzl: the native addon transition's LTO value from thin to fat. It does not apply the Phase 1 modern-ISA overlay. The build uses //:natives-win32-x64-baseline, the baseline clang-cl wrapper, CGU=16, and the pinned Rust/LLVM toolchain.

## H0 build gates

1. Exact upstream commit and clean checkout.
2. Overlay anchor occurs exactly once; the generated patch contains only the LTO transition change.
3. Structured Bazel aquery requires baseline CPU settings, CGU=16 and final addon LTO=fat.
4. Actual compiler execution trace checks baseline C/C++ ISA defaults and rejects unexpected global ISA overrides.
5. Rustc must match Phase 1: 1.102.0-nightly, commit f5cedb83a788ea9581097abc36a2a8503a7f541c, internal LLVM 23.1.5; external clang/lld must both be 20.1.7.
6. Record build resource log, aquery, compiler trace, Rustc/clang/lld versions, PE imports, embed verification, native SHA-256 and byte size.
7. Package the exact addon into the pinned v19.2.1 Windows EXE, then independently verify Windows startup, localized help, native shell execution and the loaded addon's SHA-256.

The H0 size is recorded, not gated to a predicted value. The roughly 8 MiB Phase 1 H16-vs-B size difference is a sanity reference, not a guaranteed H0-vs-A1 result.

## Phase 2 measurement

The intended comparison remains A1/H0/H16, 15 interleaved rounds each, using the same Phase 1 input set, metric definitions and Windows 11 i9-14900KF host. Do not start the 45-round run until the exact V3.2.4 source script and its eight-metric schema are available and reviewed. The repository's current bench-windows.ps1 is a different four-group 4/8-round harness and is not a substitute for that V3.2.4 script.

No PGO, training profile, ISA change or other build variable is included in Phase 2.
