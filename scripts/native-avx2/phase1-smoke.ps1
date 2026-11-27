$ErrorActionPreference = 'Stop'
$g = $env:GROUP
if ($g -cnotmatch '^(D|E|H)(1|16)$' -or $env:PACKAGE_ID -notmatch '^[0-9]+$' -or $env:PACKAGE_DIGEST -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Invalid candidate identity' }
$names = @("$g.exe","$g-native-manifest.json","$g-LICENSE","$g-LICENSE-dictionary","$g.sha256")
$items = @(Get-ChildItem -LiteralPath $PWD -Force)
if ($items.Count -ne $names.Count -or @($items | Where-Object { $_.PSIsContainer -or $_.Name -cnotin $names }).Count) { throw 'Unexpected package contents' }
$before = [ordered]@{}
foreach ($n in $names) { $before[$n] = (Get-FileHash -LiteralPath $n -Algorithm SHA256).Hash.ToLowerInvariant() }
$m = Get-Content -Raw "$g-native-manifest.json" | ConvertFrom-Json -AsHashtable
$expected = $m.experiment.groups[$g].sha256
if ($m.experiment.workflow_commit -cne $env:GITHUB_SHA -or $m.source.commit -cne 'b55cd1bb664d8ab46ddc77f2a34eb47555dbf1ac' -or $expected -notmatch '^[a-f0-9]{64}$') { throw 'Manifest mismatch' }
$status = 'local-hardware-required'
$hash = $null
# Do NOT execute a full Raptor Lake ISA addon on an unspecified hosted CPU.
# D is a local-only candidate, not an accepted Windows runtime smoke.
if ($g -notlike 'D*') {
  $root = Join-Path $env:RUNNER_TEMP ([guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $root | Out-Null
  try {
    foreach ($k in @('HOME','USERPROFILE','LOCALAPPDATA','APPDATA','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME','XDG_CONFIG_HOME','PI_CODING_AGENT_DIR','PI_NATIVES_DIR','TEMP','TMP')) {
      $v = Join-Path $root $k
      New-Item -ItemType Directory -Path $v -Force | Out-Null
      [Environment]::SetEnvironmentVariable($k,$v,'Process')
    }
    $env:PI_NATIVE_VARIANT = 'modern'
    $env:PI_DEBUG_STARTUP = '1'
    $copy = Join-Path $root "$g.exe"
    Copy-Item -LiteralPath "$g.exe" -Destination $copy
    if ((Get-FileHash -LiteralPath $copy).Hash.ToLowerInvariant() -cne $before["$g.exe"]) { throw 'Copy hash mismatch' }
    $version = & $copy --version
    if ($LASTEXITCODE -ne 0 -or "$version" -notmatch '(?<![\d.])19\.2\.1(?![\d.])') { throw 'Version failed' }
    $help = & $copy --help | Out-String
    if ($LASTEXITCODE -ne 0 -or $help -notmatch '用法' -or $help -notmatch '要发送的消息') { throw 'Help failed' }
    $out = & $copy bash -c 'printf "native-ok\n"' 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Native shell failed' }
    $log = $out | Out-String
    $markers = @([regex]::Matches($log,'(?m)^\[startup\] native:require:([^\r\n]+)\r?$'))
    $done = @([regex]::Matches($log,'(?m)^\[startup\] native:loadNative:done\r?$'))
    $paths = @([regex]::Matches($log,'(?m)^\[startup\] native:loadedPath:([^\r\n]+)\r?$'))
    $addons = @(Get-ChildItem -LiteralPath $env:PI_NATIVES_DIR -Recurse -Filter '*.node' -File)
    if ($log -notmatch 'native-ok' -or $markers.Count -ne 1 -or $done.Count -ne 1 -or $paths.Count -ne 1 -or $addons.Count -ne 1 -or $markers[0].Groups[1].Value -ne 'pi_natives.win32-x64-modern.node' -or $addons[0].Name -ne 'pi_natives.win32-x64-modern.node' -or $paths[0].Index -le $markers[0].Index -or $paths[0].Index -ge $done[0].Index) { throw 'Unexpected loaded addon evidence' }
    if (-not [string]::Equals((Resolve-Path -LiteralPath $paths[0].Groups[1].Value).Path,$addons[0].FullName,[StringComparison]::OrdinalIgnoreCase)) { throw 'Loaded path mismatch' }
    $hash = (Get-FileHash -LiteralPath $addons[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -cne $expected) { throw 'Loaded hash mismatch' }
    $status = 'windows-smoke-passed'
  } finally {
    Remove-Item -LiteralPath $root -Recurse -Force
  }
}
foreach ($n in $names) { if ((Get-FileHash -LiteralPath $n -Algorithm SHA256).Hash.ToLowerInvariant() -cne $before[$n]) { throw "File changed: $n" } }
[ordered]@{group=$g;runId=$env:GITHUB_RUN_ID;sourceSha=$env:GITHUB_SHA;artifactId=$env:PACKAGE_ID;artifactDigest=$env:PACKAGE_DIGEST;files=$before;status=$status;loadedAddonSha256=$hash} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath smoke-receipt.json -Encoding utf8NoBOM
Write-Host "$g : $status"
