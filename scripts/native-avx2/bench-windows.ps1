[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArtifactsDir,

    [ValidateRange(1, 10)]
    [int]$Rounds = 4,

    [ValidateRange(1, 500)]
    [int]$GrepIterations = 50
)

$ErrorActionPreference = "Stop"
$TagCommit = "b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac"
$TagName = "v19.2.1"
$runId = [guid]::NewGuid().ToString("N")
$worktree = Join-Path $env:TEMP ("omp-native-avx2-" + $runId)
$worktreeAdded = $false
$pushedLocation = $false

$repoRoot = (& git -C $PSScriptRoot rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
    throw "Run this script from a Git checkout of WhiCCX0/oh-my-pi."
}
$repoRoot = $repoRoot.Trim()
$artifactRoot = (Resolve-Path -LiteralPath $ArtifactsDir).Path
$manifest = Get-Content -Raw -LiteralPath (Join-Path $artifactRoot "manifest.json") | ConvertFrom-Json -AsHashtable
if ($manifest.source.commit -ne $TagCommit) { throw "Artifact source commit mismatch" }

$groups = @{
    A0 = @{
        Directory = "A0-official-baseline"
        File = "pi_natives.win32-x64-baseline.node"
        Variant = "baseline"
    }
    A1 = @{
        Directory = "A1-self-built-baseline"
        File = "pi_natives.win32-x64-baseline.node"
        Variant = "baseline"
    }
    B = @{
        Directory = "B-v3-thin-cgu16"
        File = "pi_natives.win32-x64-modern.node"
        Variant = "modern"
    }
    C = @{
        Directory = "C-v3-thin-cgu1"
        File = "pi_natives.win32-x64-modern.node"
        Variant = "modern"
    }
}

foreach ($key in $groups.Keys) {
    $candidate = Join-Path (Join-Path $artifactRoot $groups[$key].Directory) $groups[$key].File
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Missing $key addon: $candidate"
    }
    $actualHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $manifest.experiment.groups[$key].sha256) { throw "Artifact checksum mismatch: $key" }
}

if (-not (Get-Command bun -ErrorAction SilentlyContinue)) {
    throw "Bun is required and must be on PATH."
}
if (-not (Get-Command rg -ErrorAction SilentlyContinue)) {
    throw "ripgrep is required: bench/grep.ts invokes 'rg' as its external comparison implementation."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required for the isolated upstream worktree."
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "This experiment requires 64-bit Windows."
}

# Guard against an AVX2-only VM or a CPU/OS combination missing core v3 features.
# This is a sanity check, not a complete CPUID audit of F16C/MOVBE/PREFETCHW.
$intrinsicsAssembly = [System.Runtime.Intrinsics.X86.Avx2].Assembly
$requiredFeatures = @("Avx", "Avx2", "Fma", "Bmi1", "Bmi2", "Lzcnt", "Popcnt", "Sse3", "Ssse3", "Sse41", "Sse42")
$featureMap = [ordered]@{}
foreach ($feature in $requiredFeatures) {
    $type = $intrinsicsAssembly.GetType("System.Runtime.Intrinsics.X86.$feature")
    if ($null -eq $type) {
        $featureMap[$feature] = "unavailable"
        continue
    }
    $property = $type.GetProperty("IsSupported")
    if ($null -eq $property) {
        $featureMap[$feature] = "unavailable"
        continue
    }
    $featureMap[$feature] = [bool]$property.GetValue($null)
}
$missingFeatures = @($requiredFeatures | Where-Object { $featureMap[$_] -ne $true })
if ($missingFeatures.Count -gt 0) {
    throw "The current CPU/OS does not report required x86-64-v3 sanity features: $($missingFeatures -join ', '). Do not force-load the modern addon in this environment."
}

$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$bunVersion = (& bun --version 2>&1 | Out-String).Trim()
if ($bunVersion -ne "1.4.5") { throw "Use Bun 1.4.5 for the pinned experiment; found $bunVersion" }
$rgVersion = (& rg --version 2>&1 | Select-Object -First 1 | Out-String).Trim()
$powerPlan = (& powercfg /getactivescheme 2>&1 | Out-String).Trim()

$resultsDir = Join-Path $artifactRoot ("results-" + $runId)
New-Item -ItemType Directory -Force -Path $resultsDir | Out-Null

$envNames = @(
    "PI_NATIVE_VARIANT",
    "PI_NATIVES_DIR",
    "EXPECTED_NATIVE_FILE",
    "EXPECTED_NATIVE_VARIANT",
    "EXPECTED_NATIVE_VERSION",
    "EXPECTED_NATIVE_SHA256",
    "GREP_BENCH_ITERATIONS",
    "__PI_NATIVE_VARIANT_CACHE"
)
$oldEnv = @{}
foreach ($name in $envNames) {
    $oldEnv[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    # Reuse the local Git object store where possible, but check out the exact
    # upstream release commit into a detached temporary worktree.
    & git -C $repoRoot cat-file -e "$TagCommit^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & git -C $repoRoot fetch --no-tags https://github.com/can1357/oh-my-pi.git "refs/tags/$TagName"
        if ($LASTEXITCODE -ne 0) {
            throw "Could not fetch upstream tag $TagName."
        }
    }
    $resolvedCommit = (& git -C $repoRoot rev-parse "$TagCommit^{commit}").Trim()
    if ($resolvedCommit -ne $TagCommit) {
        throw "Resolved upstream commit $resolvedCommit does not match pinned commit $TagCommit."
    }

    & git -C $repoRoot worktree add --detach $worktree $TagCommit
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create temporary worktree at $TagCommit."
    }
    $worktreeAdded = $true

    $verifySource = Join-Path $repoRoot "scripts/native-avx2/verify-loaded-addon.ts"
    $verifyTargetDir = Join-Path $worktree "scripts/native-avx2"
    New-Item -ItemType Directory -Force -Path $verifyTargetDir | Out-Null
    Copy-Item -LiteralPath $verifySource -Destination (Join-Path $verifyTargetDir "verify-loaded-addon.ts") -Force

    Push-Location $worktree
    $pushedLocation = $true

    $installLog = Join-Path $resultsDir "bun-install.log"
    & bun install --frozen-lockfile *> $installLog
    if ($LASTEXITCODE -ne 0) {
        throw "bun install failed. See $installLog"
    }

    $nativeDir = Join-Path $worktree "packages/natives/native"
    $orders = @(
        @("A0", "A1", "B", "C"),
        @("A1", "B", "C", "A0"),
        @("B", "C", "A0", "A1"),
        @("C", "A0", "A1", "B")
    )
    $runRows = [System.Collections.Generic.List[object]]::new()
    $grepSignatures = @{}

    for ($round = 1; $round -le $Rounds; $round++) {
        $order = $orders[($round - 1) % $orders.Count]
        foreach ($key in $order) {
            $group = $groups[$key]
            $sourceFile = Join-Path (Join-Path $artifactRoot $group.Directory) $group.File
            $targetFile = Join-Path $nativeDir $group.File

            # The disposable worktree must contain exactly one candidate .node
            # file. Modern cannot silently fall back to baseline, and vice versa.
            Get-ChildItem -LiteralPath $nativeDir -Filter "*.node" -File -ErrorAction SilentlyContinue |
                Remove-Item -Force
            Copy-Item -LiteralPath $sourceFile -Destination $targetFile -Force
            $present = @(Get-ChildItem -LiteralPath $nativeDir -Filter "*.node" -File)
            if ($present.Count -ne 1 -or $present[0].Name -ne $group.File) {
                throw "Physical isolation failed for $key; found: $($present.Name -join ', ')"
            }

            $env:PI_NATIVE_VARIANT = $group.Variant
            $env:EXPECTED_NATIVE_FILE = $group.File
            $env:EXPECTED_NATIVE_VARIANT = $group.Variant
            $env:EXPECTED_NATIVE_VERSION = "19.2.1"
            $env:EXPECTED_NATIVE_SHA256 = $manifest.experiment.groups[$key].sha256
            $env:GREP_BENCH_ITERATIONS = [string]$GrepIterations
            $env:PI_NATIVES_DIR = Join-Path $env:TEMP ("omp-native-cache-" + $runId + "-" + $key + "-" + $round)
            Remove-Item -LiteralPath $env:PI_NATIVES_DIR -Recurse -Force -ErrorAction SilentlyContinue
            New-Item -ItemType Directory -Force -Path $env:PI_NATIVES_DIR | Out-Null
            Remove-Item Env:__PI_NATIVE_VARIANT_CACHE -ErrorAction SilentlyContinue

            $prefix = "round-{0:D2}-{1}" -f $round, $key
            $verifyLog = Join-Path $resultsDir "$prefix-verify.log"
            $textLog = Join-Path $resultsDir "$prefix-text.log"
            $grepLog = Join-Path $resultsDir "$prefix-grep.log"

            & bun scripts/native-avx2/verify-loaded-addon.ts *> $verifyLog
            if ($LASTEXITCODE -ne 0) {
                throw "Native loader verification failed for $key. See $verifyLog"
            }

            $sha256 = (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash.ToLowerInvariant()

            & bun --preload ./scripts/native-avx2/verify-loaded-addon.ts packages/natives/bench/text.ts *> $textLog
            if ($LASTEXITCODE -ne 0) {
                throw "Text benchmark failed for $key. See $textLog"
            }

            & bun --preload ./scripts/native-avx2/verify-loaded-addon.ts packages/natives/bench/grep.ts *> $grepLog
            if ($LASTEXITCODE -ne 0) {
                throw "Grep benchmark failed for $key. See $grepLog"
            }

            $matches = @([regex]::Matches((Get-Content -Raw -LiteralPath $grepLog), '(?m)Native grep:\s+[^\r\n]*\((\d+) (?:files|matches)\)') | ForEach-Object { $_.Groups[1].Value })
            if ($matches.Count -eq 0) { throw "Missing grep correctness metrics for $key" }
            $signature = $matches -join ','
            if ($grepSignatures.ContainsKey($round) -and $grepSignatures[$round] -ne $signature) { throw "Grep correctness mismatch in round $round for $key" }
            $grepSignatures[$round] = $signature
            $runRows.Add([pscustomobject]@{
                Round = $round
                Group = $key
                Variant = $group.Variant
                NativeFile = $group.File
                SHA256 = $sha256
                VerifyLog = [IO.Path]::GetFileName($verifyLog)
                TextLog = [IO.Path]::GetFileName($textLog)
                GrepLog = [IO.Path]::GetFileName($grepLog)
            })
            Write-Host "Completed round $round group $key ($($group.Variant)); SHA256 $sha256"
        }
    }

    $runRows | Export-Csv -NoTypeInformation -Encoding utf8 -Path (Join-Path $resultsDir "runs.csv")

    $metadata = [ordered]@{
        timestamp = (Get-Date).ToString("o")
        upstreamTag = $TagName
        upstreamCommit = $TagCommit
        cpu = $cpu.Name
        physicalCores = $cpu.NumberOfCores
        logicalProcessors = $cpu.NumberOfLogicalProcessors
        windowsVersion = [Environment]::OSVersion.VersionString
        powershellVersion = $PSVersionTable.PSVersion.ToString()
        bunVersion = $bunVersion
        ripgrepVersion = $rgVersion
        activePowerPlan = $powerPlan
        x86FeatureSanityCheck = $featureMap
        rounds = $Rounds
        grepIterations = $GrepIterations
        groupOrder = $orders
    }
    $metadata | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 -Path (Join-Path $resultsDir "environment.json")

    $zipPath = Join-Path $artifactRoot ("native-avx2-results-" + $runId + ".zip")
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path (Join-Path $resultsDir "*") -DestinationPath $zipPath -CompressionLevel Optimal -Force

    Write-Host ""
    Write-Host "Benchmark complete."
    Write-Host "Raw results: $resultsDir"
    Write-Host "Results bundle: $zipPath"
}
finally {
    if ($pushedLocation) {
        Pop-Location
    }

    foreach ($name in $envNames) {
        if ($null -eq $oldEnv[$name]) {
            Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue
        }
        else {
            [Environment]::SetEnvironmentVariable($name, $oldEnv[$name], "Process")
        }
    }

    Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter ("omp-native-cache-" + $runId + "-*") | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    if ($worktreeAdded) {
        & git -C $repoRoot worktree remove --force $worktree
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Temporary worktree cleanup was incomplete: $worktree"
        }
        & git -C $repoRoot worktree prune
    }
}
