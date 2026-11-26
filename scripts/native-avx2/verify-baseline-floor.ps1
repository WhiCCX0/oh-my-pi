#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ArtifactsDir,
    [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedA0Sha256,
    [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedA1Sha256
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or [Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture -ne [Runtime.InteropServices.Architecture]::X64) {
    throw 'Use x64 PowerShell 7 on Windows.'
}
if ([Environment]::OSVersion.Version.Build -lt 19041) { throw 'Windows 10 2004 or later is required for the native feature checks.' }
# Windows Kernel32 feature checks are independent of .NET AVX-disable switches.
# https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-isprocessorfeaturepresent
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class OmpBaselineCpuFeature {
    [DllImport("kernel32.dll", ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsProcessorFeaturePresent(uint feature);
}
'@
$features = [ordered]@{
    SSE3 = [OmpBaselineCpuFeature]::IsProcessorFeaturePresent(13)
    SSSE3 = [OmpBaselineCpuFeature]::IsProcessorFeaturePresent(36)
    SSE41 = [OmpBaselineCpuFeature]::IsProcessorFeaturePresent(37)
    SSE42 = [OmpBaselineCpuFeature]::IsProcessorFeaturePresent(38)
    AVX = [OmpBaselineCpuFeature]::IsProcessorFeaturePresent(39)
    AVX2 = [OmpBaselineCpuFeature]::IsProcessorFeaturePresent(40)
    AVX512F = [OmpBaselineCpuFeature]::IsProcessorFeaturePresent(41)
}
if (-not ($features.SSE3 -and $features.SSSE3 -and $features.SSE41 -and $features.SSE42)) {
    throw 'This host does not expose the required SSE4.2-class instruction set.'
}
if ($features.AVX -or $features.AVX2 -or $features.AVX512F) {
    throw 'This host exposes AVX. It cannot establish the no-AVX baseline smoke result; use an actual non-AVX CPU or a VM with AVX genuinely masked.'
}
$dir = (Resolve-Path -LiteralPath $ArtifactsDir).Path
$expected = @{ A0 = $ExpectedA0Sha256.ToLowerInvariant(); A1 = $ExpectedA1Sha256.ToLowerInvariant() }
$rows = @()
$root = Join-Path ([IO.Path]::GetTempPath()) ("omp-baseline-" + [guid]::NewGuid().ToString('N'))
$envNames = @('HOME','USERPROFILE','LOCALAPPDATA','APPDATA','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME','XDG_CONFIG_HOME','PI_CODING_AGENT_DIR','PI_NATIVES_DIR','PI_NATIVE_VARIANT','PI_DEBUG_STARTUP','TEMP','TMP')
$saved = @{}
foreach ($key in $envNames) { $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
New-Item -ItemType Directory -Path $root | Out-Null
try {
    foreach ($group in @('A0','A1')) {
        $source = Join-Path $dir "$group.exe"
        $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -ne $expected[$group]) { throw "$group EXE does not match independently trusted SHA256" }
        $groupRoot = Join-Path $root $group
        New-Item -ItemType Directory -Path $groupRoot | Out-Null
        foreach ($key in $envNames) {
            if ($key -in @('PI_NATIVE_VARIANT','PI_DEBUG_STARTUP')) { continue }
            $value = Join-Path $groupRoot $key
            New-Item -ItemType Directory -Path $value | Out-Null
            [Environment]::SetEnvironmentVariable($key, $value, 'Process')
        }
        $env:PI_NATIVE_VARIANT = 'baseline'
        $env:PI_DEBUG_STARTUP = '1'
        $exe = Join-Path $groupRoot "$group.exe"
        Copy-Item -LiteralPath $source -Destination $exe
        if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected[$group]) { throw "$group copied EXE hash mismatch" }
        $version = & $exe --version 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or $version -notmatch '(?<![\d.])19\.2\.1(?![\d.])') { throw "$group version failed: $version" }
        $help = & $exe --help 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or $help -notmatch '用法') { throw "$group help failed" }
        $nativeLog = & $exe bash -c 'printf "native-ok\n"' 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or $nativeLog -notmatch 'native-ok') { throw "$group native shell failed: $nativeLog" }
        $loads = @([regex]::Matches($nativeLog, '(?m)^\[startup\] native:require:([^\r\n]+)\r?$'))
        $done = @([regex]::Matches($nativeLog, '(?m)^\[startup\] native:loadNative:done\r?$'))
        if ($loads.Count -ne 1 -or $done.Count -ne 1 -or $done[0].Index -le $loads[0].Index -or $loads[0].Groups[1].Value -ne 'pi_natives.win32-x64-baseline.node') { throw "$group baseline load proof failed" }
        $addons = @(Get-ChildItem -LiteralPath $env:PI_NATIVES_DIR -Recurse -File -Filter '*.node')
        if ($addons.Count -ne 1 -or $addons[0].Name -ne 'pi_natives.win32-x64-baseline.node') { throw "$group unexpected native addon set" }
        $addonHash = (Get-FileHash -LiteralPath $addons[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $manifest = Get-Content -LiteralPath (Join-Path $dir "$group-native-manifest.json") -Raw | ConvertFrom-Json -AsHashtable
        if ($addonHash -ne $manifest.experiment.groups[$group].sha256) { throw "$group addon hash mismatch" }
        $smoke = & $exe --smoke-test 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or $smoke -notmatch 'smoke-test: ok') { throw "$group smoke failed: $smoke" }
        $rows += [ordered]@{group=$group;exeSha256=$hash;addonSha256=$addonHash;version=$version.Trim();smoke='pass'}
    }
    $report = [ordered]@{
        testedAt=[DateTimeOffset]::UtcNow.ToString('o')
        cpu=(Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name)
        os=[Environment]::OSVersion.Version.ToString()
        features=$features
        results=$rows
        scope='SSE4.2/no-AVX host: version, help, native loader and smoke paths; not proof of every runtime path'
    }
    $out = Join-Path $dir ("baseline-floor-" + [guid]::NewGuid().ToString('N') + ".json")
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $out -Encoding utf8
    Write-Host "Baseline smoke passed on no-AVX host. Evidence: $out"
} finally {
    foreach ($key in $envNames) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
