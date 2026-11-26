# Windows x64 native ISA experiment (v19.2.1)

This branch is an isolated experiment. It does not change `main`, the upstream-sync workflow, the normal release workflow, or the Chinese mirror repository.

## Pinned source

All four groups use the exact upstream release source:

- Repository: `can1357/oh-my-pi`
- Tag: `v19.2.1`
- Commit: `b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac`

The current upstream `main` has moved beyond this tag while retaining package version 19.2.1. The workflow therefore checks out the tag explicitly; it does not build from the fork's current `main`.

## Experiment groups

| Group | Binary source | Rust target ISA | C/C++ ISA | LTO | CGU |
|---|---|---|---|---|---:|
| A0 | Official npm package | Official baseline | Official baseline | Official | Official |
| A1 | Self-built from the same tag | Unmodified upstream baseline recipe | Unmodified upstream baseline wrapper | Thin | 16 |
| B | Self-built experiment overlay | x86-64-v3 on all non-exec Windows-target Rustc actions | `-march=x86-64-v3` in the generated clang-cl wrapper | Thin | 16 |
| C | Self-built experiment overlay | x86-64-v3 on all non-exec Windows-target Rustc actions | `-march=x86-64-v3` in the generated clang-cl wrapper | Thin | 1 |

**Important A1 detail:** upstream v19.2.1 applies `-Ctarget-cpu=x86-64-v2` to the top-level `pi_natives` crate through its crate-level `rustc_flags` select. Its `extra_rustc_flags` transition does not globally apply v2 to dependency crates. A1 intentionally preserves that exact upstream baseline recipe so A0/A1 remains a fair official-package versus self-build comparison. It is not a newly invented global-v2 build.

For B/C, the overlay adds v3 through `@rules_rust//rust/settings:extra_rustc_flags`, which applies to the non-exec (target) configuration. Host build scripts and proc-macros remain compiled for the host.

The Windows C/C++ sources are not all represented as top-level Bazel `CppCompile` actions: several compile from Rust build scripts through the hermetic clang-cl wrapper. The gate therefore checks the target Rustc actions with `aquery` and separately checks the generated wrapper itself for `-march=x86-64-v3`.

## Build

The workflow is restricted to the `experiment/native-avx2` branch and triggers on experiment-file pushes. It uses a GitHub-hosted Ubuntu 22.04 runner with a local Bazel disk cache and two build jobs. Cold builds can exceed disk, memory or time limits; incomplete artifacts are diagnostic only.

1. Open the branch's **Windows x64 native ISA experiment** Actions run.
2. Confirm the workflow completed successfully.
3. Download the complete artifact only after all four groups pass.
4. Download and extract `native-avx2-v19.2.1-<run_id>`.

The artifact contains the four `.node` files, SHA-256 checksums, the exact source commit, Bazel/aquery evidence, the generated clang-cl wrapper, and a modern-only embedding check.

A0 is copied byte-for-byte from npm. A1/B/C are installed through the upstream `scripts/bazel-natives.ts` path, which applies the v19.2.1 post-link version stamp.

## Local Windows benchmark

Run this on the physical Windows x64 machine, not on a shared CI runner. The script creates a temporary detached Git worktree at the exact upstream tag, installs the locked Bun dependencies there, and never swaps files in the user's existing checkout or installed OMP.

Prerequisites:

- Windows x64 and PowerShell 7
- Git, Bun, and ripgrep (`rg.exe`) on PATH
- A CPU/OS combination exposing the full x86-64-v3 feature set; AVX2 alone is not a sufficient check
- Enough free disk space for a temporary source worktree and `bun install`

Example:

```powershell
pwsh -NoProfile -File .\scripts\native-avx2\bench-windows.ps1 -ArtifactsDir "D:\Downloads\native-avx2-v19.2.1-<run_id>" -Rounds 3
```

The benchmark runner rotates group order between rounds, forces `PI_NATIVE_VARIANT` to the intended variant, keeps exactly one `.node` file in the temporary worktree, and verifies the loader's actual path, release version, and SHA-256 before timing. It runs the upstream `bench/text.ts` and `bench/grep.ts` scripts. The latter requires `rg` on PATH.

Raw logs, environment information, checksums, and a zipped results bundle are written under the downloaded artifact directory's `results\` folder.

Keep the machine idle and use the same Windows power plan for all runs. Do not compare runs taken under materially different background CPU load or thermal conditions.

## Interpretation

- A0 vs A1: official npm addon versus a self-build using the same upstream baseline recipe.
- A1 vs B: effect of broad target-graph v3 code generation, including the C/C++ wrapper change.
- B vs C: effect of changing CGU from 16 to 1 while keeping v3 and ThinLTO.
- D/FatLTO, PGO, full EXE packaging, and formal release publication are intentionally out of scope.

The 3% / 5% / 10% thresholds are decision gates, not predicted performance gains. Keep the raw per-round logs; do not accept a result that is within run-to-run noise or accompanied by a functional regression.

## Audit hardening

The completed historical experiment remains an internal performance experiment, not a production attestation. Packaging pins the GitHub run, producer commit, workflow path, artifact ID and SHA-256 of the original archive, then verifies the extracted group hashes. This prevents replacing the archive together with its manifest; it does not independently certify the producer's integrity. A new native build is explicitly requested by changing `scripts/native-avx2/rebuild-request.txt` on this branch (no incidental one-hour rebuild on script edits). Pin new artifact/run metadata deliberately before repackaging; the historical archive expires after 30 days. A future native build uses a structured Rustc/CppCompile gate, pinned Bazelisk binary, fresh output directory and source tree, and atomic patch application. Cargo build-script subprocess and full baseline CPU-floor proof still require execution evidence before formal adoption. The offline EXE V2 benchmark is preferred; the legacy source benchmark now validates native hash inside each measured process and checks group grep-result consistency.

`native-avx2-checks.yml` validates shell/Python syntax, structured-gate fixtures and both atomic overlays against the pinned upstream source without compiling Rust. A future full gate also checks the explicit ThinLTO flag on the final addon and both baseline/modern executable wrapper bodies.
