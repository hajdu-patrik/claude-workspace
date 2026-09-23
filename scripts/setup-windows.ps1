<#
  Jev-router - Windows helyi beallitas (1. fazis). PowerShell 5.1+ kompatibilis, csak ASCII.
  Futtatas a repo gyokerebol:
    powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 [-KeepAwake] [-AutoStart]

  Mit csinal (mind idempotens):
    1. Ellenorzi: git, python (valodi, nem Store-stub), claude, Git Bash.
    2. Figyelmeztet a Remote Controlt tilto kornyezeti valtozokra.
    3. TYPESAFE_API_KEY -> felhasznaloi kornyezeti valtozo (ha meg nincs).
    4. CLAUDE_CODE_SHELL -> Git Bash a %USERPROFILE%\.claude\settings.json env blokkjaba (mentes .bak-ba).
    5. -KeepAwake: halozati tapon alvas/hibernalas ki.
    6. -AutoStart: "ClaudeRemoteControl" utemezett feladat bejelentkezeskor (start-rc.cmd).
    7. Fustteszt: a router hook egy magyar prompttal.
#>
param([switch]$KeepAwake, [switch]$AutoStart)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8

function Ok($m)   { Write-Host "[OK]   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!]    $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "[HIBA] $m" -ForegroundColor Red }

# 1. Eszkozok
$fail = $false
foreach ($t in 'git', 'claude') {
  if (Get-Command $t -ErrorAction SilentlyContinue) { Ok "$t megvan" } else { Bad "$t nincs a PATH-on"; $fail = $true }
}
$py = $null
foreach ($c in 'python', 'python3', 'py') {
  try {
    $v = & $c -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
    if ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq 'True') { $py = $c; break }
  } catch { }
}
if ($py -eq 'py') { Warn "Csak a 'py' launcher mukodik; a hook 'python'/'python3'-at hiv -> telepitsd a Pythont 'Add to PATH'-szal" }
if ($py) { Ok "Python 3.10+: $py" } else { Bad "Python 3.10+ nem talalhato (a Microsoft Store stub nem szamit)"; $fail = $true }
$GitBash = 'C:\Program Files\Git\bin\bash.exe'
if (Test-Path $GitBash) { Ok "Git Bash: $GitBash" } else { Warn "Git Bash nincs a szokasos helyen; a hook shellt kezzel allitsd be" ; $GitBash = $null }
if ($fail) { Bad "Telepitsd a hianyzo eszkozoket, majd futtasd ujra."; exit 1 }

# 2. Remote Controlt tilto valtozok
$blockers = 'ANTHROPIC_API_KEY', 'DISABLE_TELEMETRY', 'DO_NOT_TRACK', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'
$found = @()
foreach ($n in $blockers) {
  foreach ($s in 'Process', 'User', 'Machine') {
    if ([Environment]::GetEnvironmentVariable($n, $s)) { $found += "$n ($s)" }
  }
}
if ($found) { Warn ("Remote Controlt tiltja: " + ($found -join ', ') + " -> torold oket.") } else { Ok "Nincs Remote Controlt tilto valtozo" }

# 3. TypeSafe kulcs
if ([Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY', 'User')) {
  Ok "TYPESAFE_API_KEY mar be van allitva (User)"
} else {
  $sec = Read-Host "TypeSafe API-kulcs (Enter = kihagyas)" -AsSecureString
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
  if ($plain) {
    [Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $plain, 'User')
    $env:TYPESAFE_API_KEY = $plain
    Ok "TYPESAFE_API_KEY elmentve (User). Uj terminal kell, hogy a claude is lassa."
  } else { Warn "Kulcs nelkul a router 'unavailable' fallbacken fut (nem blokkol)." }
}

# 4. CLAUDE_CODE_SHELL a felhasznaloi Claude-beallitasokba
if ($GitBash) {
  $dir = Join-Path $env:USERPROFILE '.claude'
  $f = Join-Path $dir 'settings.json'
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $cfg = if (Test-Path $f) { Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json } else { New-Object psobject }
  if (-not $cfg) { $cfg = New-Object psobject }
  if (-not ($cfg.PSObject.Properties.Name -contains 'env')) { $cfg | Add-Member -NotePropertyName env -NotePropertyValue (New-Object psobject) }
  if ($cfg.env.CLAUDE_CODE_SHELL -eq $GitBash) {
    Ok "CLAUDE_CODE_SHELL mar Git Bash"
  } else {
    if (Test-Path $f) { Copy-Item $f "$f.bak" -Force }
    Remove-TypeData System.Array -ErrorAction SilentlyContinue  # PS 5.1: tombok ne {"value":..,"Count":..} alakban irodjanak
    $cfg.env | Add-Member -NotePropertyName CLAUDE_CODE_SHELL -NotePropertyValue $GitBash -Force
    [IO.File]::WriteAllText($f, ($cfg | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding $false))
    Ok "CLAUDE_CODE_SHELL beirva: $f (mentes: settings.json.bak)"
  }
}

# 5. Ebren tartas
if ($KeepAwake) {
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  Ok "Alvas/hibernalas kikapcsolva halozati tapon"
}

# 6. Automatikus inditas
if ($AutoStart) {
  $cmd = Join-Path $Repo 'start-rc.cmd'
  $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$cmd`"" -WorkingDirectory $Repo
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
  try {
    Register-ScheduledTask -TaskName 'ClaudeRemoteControl' -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
    Ok "Utemezett feladat: ClaudeRemoteControl (bejelentkezeskor)"
  } catch { Warn "Utemezett feladat nem jott letre ($($_.Exception.Message)). Futtasd admin PowerShellben." }
}

# 7. Fustteszt
$out = '{"prompt":"Refaktorald az auth modult"}' | & $py .claude/hooks/router_hook.py
Write-Host "Hook kimenet: $out"
$logf = Join-Path $Repo 'logs\routing.jsonl'
if (Test-Path $logf) { Write-Host ("Utolso logsor: " + (Get-Content $logf -Tail 1 -Encoding UTF8)) }
if ("$out" -match 'task=code') { Ok "Jev-hivas sikeres" }
elseif ("$out" -match 'unavailable') { Warn "Router fallback: nezd a logsor 'error' mezojet (uj terminalban futtasd ujra, ha most allitottad a kulcsot)" }
else { Bad "Varatlan hook-kimenet" }

Write-Host ""
Write-Host "Kovetkezo: claude  (trust + /login), majd: claude remote-control --name `"Otthoni gep`""
