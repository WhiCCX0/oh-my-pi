$ErrorActionPreference = 'Stop'
$group = 'H0'
if ($env:PACKAGE_ID -notmatch '^[0-9]+$' -or $env:PACKAGE_DIGEST -notmatch '^sha256:[a-f0-9]{64}$') {
    throw 'Invalid H0 artifact identity'
}
$names = @('H0.exe','H0-native-manifest.json','H0-LICENSE','H0-LICENSE-dictionary','H0.sha256')
$items = @(Get-ChildItem -LiteralPath $PWD -Force)
if ($items.Count -ne $names.Count -or @($items | Where-Object { $_.PSIsContainer -or $_.Name -cnotin $names }).Count) {
    throw 'Unexpected H0 package contents'
}
$before = [ordered]@{}
foreach ($name in $names) { $before[$name] = (Get-FileHash -LiteralPath $name -Algorithm SHA256).Hash.ToLowerInvariant() }
$manifest = Get-Content -Raw 'H0-native-manifest.json' | ConvertFrom-Json -AsHashtable
$exp = $manifest.experiment
$config = $exp.configuration
$expectedNative = $exp.groups.H0.sha256
if ($env:GITHUB_SHA -cne $exp.workflow_commit -or
    $manifest.source.commit -cne 'b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac' -or
    $exp.localization_commit -cne 'ac1616560eb7d04d2a58afe97dcec88f310a1ad8' -or
    $config.cpu -cne 'baseline' -or $config.target -cne 'win32-x64-baseline' -or
    $config.lto -cne 'fat' -or $config.cgu -ne 16 -or $config.llvm -cne '20.1.7' -or
    $expectedNative -notmatch '^[a-f0-9]{64}$') {
    throw 'H0 manifest mismatch'
}
if ((Get-Content -Raw 'H0.sha256').Trim() -cne ($before['H0.exe'] + '  H0.exe')) { throw 'H0 EXE checksum file mismatch' }

$root = Join-Path $env:RUNNER_TEMP ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
try {
    foreach ($key in @('HOME','USERPROFILE','LOCALAPPDATA','APPDATA','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME','XDG_CONFIG_HOME','PI_CODING_AGENT_DIR','PI_NATIVES_DIR','TEMP','TMP')) {
        $value = Join-Path $root $key
        New-Item -ItemType Directory -Path $value -Force | Out-Null
        [Environment]::SetEnvironmentVariable($key,$value,'Process')
    }
    $env:PI_NATIVE_VARIANT = 'baseline'
    $env:PI_DEBUG_STARTUP = '1'
    $copy = Join-Path $root 'H0.exe'
    Copy-Item -LiteralPath 'H0.exe' -Destination $copy
    if ((Get-FileHash -LiteralPath $copy).Hash.ToLowerInvariant() -cne $before['H0.exe']) { throw 'H0 copy hash mismatch' }

    $version = & $copy --version
    if ($LASTEXITCODE -ne 0 -or "$version" -notmatch '(?<![\d.])19\.2\.1(?![\d.])') { throw 'H0 version check failed' }
    $help = & $copy --help | Out-String
    if ($LASTEXITCODE -ne 0 -or $help -notmatch '用法' -or $help -notmatch '要发送的消息') { throw 'H0 localized help check failed' }
    $output = & $copy bash -c 'printf "native-ok\n"' 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'H0 native shell smoke failed' }
    $log = $output | Out-String
    $required = @([regex]::Matches($log,'(?m)^\[startup\] native:require:([^\r\n]+)\r?$'))
    $loaded = @([regex]::Matches($log,'(?m)^\[startup\] native:loadedPath:([^\r\n]+)\r?$'))
    $done = @([regex]::Matches($log,'(?m)^\[startup\] native:loadNative:done\r?$'))
    $addons = @(Get-ChildItem -LiteralPath $env:PI_NATIVES_DIR -Recurse -Filter '*.node' -File)
    $expectedFile = 'pi_natives.win32-x64-baseline.node'
    if ($log -notmatch 'native-ok' -or $required.Count -ne 1 -or $loaded.Count -ne 1 -or $done.Count -ne 1 -or
        $addons.Count -ne 1 -or $required[0].Groups[1].Value -cne $expectedFile -or $addons[0].Name -cne $expectedFile -or
        $loaded[0].Index -le $required[0].Index -or $loaded[0].Index -ge $done[0].Index) {
        throw 'H0 did not load exactly the expected baseline addon'
    }
    if (-not [string]::Equals((Resolve-Path -LiteralPath $loaded[0].Groups[1].Value).Path,$addons[0].FullName,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'H0 loaded path does not match the isolated addon file'
    }
    $loadedHash = (Get-FileHash -LiteralPath $addons[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($loadedHash -cne $expectedNative) { throw 'H0 loaded native addon SHA256 mismatch' }
    $status = 'windows-smoke-passed'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force
}
foreach ($name in $names) {
    if ((Get-FileHash -LiteralPath $name -Algorithm SHA256).Hash.ToLowerInvariant() -cne $before[$name]) {
        throw "Package file changed during smoke: $name"
    }
}
[ordered]@{
    group = $group; runId = $env:GITHUB_RUN_ID; sourceSha = $env:GITHUB_SHA
    artifactId = $env:PACKAGE_ID; artifactDigest = $env:PACKAGE_DIGEST
    files = $before; status = $status; loadedAddonSha256 = $loadedHash
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath 'smoke-receipt.json' -Encoding utf8NoBOM
Write-Host "H0 : $status"
