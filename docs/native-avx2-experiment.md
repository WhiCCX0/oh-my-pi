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


### Rebuild and signed same-run packaging

A deliberate change to `scripts/native-avx2/rebuild-request.txt` triggers the pinned full native experiment. A successful build passes its immutable artifact ID and archive digest to the same workflow run's packaging jobs; no historical run ID is required. The old comparison runs remain unchanged. A0 now uses the pinned formal npm signature and provenance verifier before extracting the upstream baseline. Packaging retains the original pinned localization snapshot for a fair comparison. After all four Windows loader/hash smoke tests, the collection job requests GitHub build-provenance attestations for the four EXEs, manifests and checksum list. Attestation establishes the GitHub producer identity and subject hashes, not CPU compatibility or absence of malicious source. Verify locally with `gh attestation verify A0.exe --repo WhiCCX0/oh-my-pi` (repeat for all groups). For local native builds, set `OMP_NATIVE_VERIFY_SCRIPT` to the verified `localization/zh-CN/verify-native.py` at commit `ac1616560eb7d04d2a58afe97dcec88f310a1ad8` and use npm 11.17.0. Archived downloads expire under GitHub retention policy, but a new requested build no longer depends on them.


### Actual compiler-child evidence and baseline floor

The trace option (default for an explicit rebuild request) uses a fresh Bazel action cache, batch mode and local spawn on an isolated, read-only experiment runner. It records actual successful Windows clang-driver argument vectors, identifies Cargo build-script ancestry and rejects missing or contradictory defaults, missing ancestry, and unresolved response files. Raw strace output is temporary; only compiler evidence JSON is retained. This is execution evidence, not a proof of every runtime-dispatched specialized C/assembly path. Reported specialized ISA flags must be reviewed, and a real no-AVX Windows environment remains necessary for the minimum-floor smoke. The non-tracing mode preserves the ordinary sandboxed/cached build for routine experiments.

The default branch has a dispatch entry after the formal audit PR is merged. In Actions select the experiment workflow and branch `experiment/native-avx2`; enable trace for a fresh proof run. Editing `rebuild-request.txt` is still an explicit alternative.

For the baseline floor, independently verify the artifact provenance and obtain the two EXE SHA256 values from trusted attestations. Run `pwsh -NoProfile -File scripts/native-avx2/verify-baseline-floor.ps1 -ArtifactsDir <dir> -ExpectedA0Sha256 <trusted-A0-hash> -ExpectedA1Sha256 <trusted-A1-hash>` on a genuine SSE4.2-class Windows x64 host exposing no AVX (or a VM with AVX genuinely masked). The script checks Windows Kernel32 feature flags rather than relying on .NET AVX settings, refuses an AVX-capable environment, isolates configuration, verifies exact baseline loading and produces a JSON report. A passing smoke covers exercised paths only, not arbitrary native instructions or all runtime-dispatch branches.


### Second-review scope and provenance

All four EXEs now use the same pinned r6 localization snapshot (ac1616560eb7d04d2a58afe97dcec88f310a1ad8). The separate A0 packaging runner re-downloads the official package and independently validates it against the original signed npm provenance before comparing bytes. The build manifest records the A0 digest, trace mode and trace evidence digests. Each Windows smoke job records exact package artifact ID, archive digest, all five file hashes and loaded addon hash. Collection rejects extra files and signs only the four explicit EXEs, manifests, checksum list and receipts. This attestation means smoke-accepted experiment output, not a certification of full ISA coverage or no-AVX runtime compatibility. Verify with `gh attestation verify A0.exe --repo WhiCCX0/oh-my-pi --signer-workflow WhiCCX0/oh-my-pi/.github/workflows/native-avx2-package.yml --source-ref refs/heads/experiment/native-avx2 --source-digest <exact-run-commit>`; verify the manifest similarly. Confirm signer workflow using the returned attestation identity if the caller identity is reported instead. The legacy source benchmark requires an independent manifest SHA256 and checks the addon before dlopen. Prefer the offline EXE benchmark after independently checking the exact EXE attestations. The no-AVX floor test remains a separate hardware-dependent gate.

The pinned upstream graph actually contains 689 Windows Rustc and zero CppCompile actions (previous run logs). The explicit Cargo-only mode therefore does not claim C/C++ coverage; the traced run is required to establish actual compiler-child evidence. Non-trace builds are smoke-only. The experiment-only loader path marker is injected into the disposable packaging checkout, and the original native manifest is preserved byte-for-byte for independent benchmark validation.

### Third-review trace correction

The generated Bazel msvc_cc clang-cl shell wrapper adds architecture defaults before execing the LLVM driver in the same PID. The trace checker now pairs the known wrapper with its next successful exec of the pinned LLVM repository driver, counts both wrapper invocations and confirmed handoffs, and checks ISA arguments only on actual driver invocations. Missing handoffs, wrong targets/drivers, truncated argv and missing ISA defaults remain blocking. Both overlay and provenance policy now use the same second-review main commit. This fixes an observed trace false positive, not the independent minimum CPU compatibility proof.
