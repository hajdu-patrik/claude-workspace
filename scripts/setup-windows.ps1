<#
  Jev-router - Windows local setup. PowerShell 5.1+ compatible, ASCII only.
  Run from the repo root:
    powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 [-KeepAwake] [-AutoStart]

  What it does (all idempotent):
    1. Checks: git, python (a real one, not the Store stub), claude, Git Bash.
    2. Warns about environment variables that block Remote Control.
    3. TYPESAFE_API_KEY -> user environment variable (if not already set).
    4. CLAUDE_CODE_SHELL -> Git Bash in %USERPROFILE%\.claude\settings.json's env block (backed up to .bak).
    5. -KeepAwake: on AC power, no sleep/hibernate, closing the lid does nothing.
    6. -AutoStart: phone access for all three tools - "ClaudeRemoteControl" scheduled task at
       logon (start-rc.cmd), plus the Antigravity and Codex remote-control daemons.
    7. Checks for the presence of the Codex / Antigravity CLI (informational only).
    8. Installs the router hook + MCP router GLOBALLY (user level) for Claude Code, Codex and
       Antigravity, via the space-free shim ~/.jev-router/bin (router/install_hooks.py --apply).
    9. Shared skill hub ~/.skills: migrate, link into all three tools, generate the worker agents,
       rebuild the skill catalog (router/skills_hub.py all --apply).
    10. Model-family policy check (scripts/check_models.py).
    11. Smoke test: the router hook with a Hungarian prompt, then the full health report
        (scripts/check_tools.py).
#>
param([switch]$KeepAwake, [switch]$AutoStart)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false  # no BOM (PS 5.1)

function Ok($m)   { Write-Host "[OK]   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!]    $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "[FAIL] $m" -ForegroundColor Red }

# 1. Tools
$fail = $false
foreach ($t in 'git', 'claude') {
  if (Get-Command $t -ErrorAction SilentlyContinue) { Ok "$t found" } else { Bad "$t not on PATH"; $fail = $true }
}
$py = $null
foreach ($c in 'python', 'python3', 'py') {
  try {
    $v = & $c -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
    if ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq 'True') { $py = $c; break }
  } catch { }
}
if ($py -eq 'py') { Warn "Only the 'py' launcher works; the hook calls 'python'/'python3' -> install Python with 'Add to PATH'" }
if ($py) { Ok "Python 3.10+: $py" } else { Bad "Python 3.10+ not found (the Microsoft Store stub doesn't count)"; $fail = $true }
$GitBash = 'C:\Program Files\Git\bin\bash.exe'
if (Test-Path $GitBash) { Ok "Git Bash: $GitBash" } else { Warn "Git Bash not in the usual location; set the hook shell manually" ; $GitBash = $null }
if ($fail) { Bad "Install the missing tools, then run this again."; exit 1 }

# 2. Variables that block Remote Control
$blockers = 'ANTHROPIC_API_KEY', 'DISABLE_TELEMETRY', 'DO_NOT_TRACK', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'
$found = @()
foreach ($n in $blockers) {
  foreach ($s in 'Process', 'User', 'Machine') {
    if ([Environment]::GetEnvironmentVariable($n, $s)) { $found += "$n ($s)" }
  }
}
if ($found) { Warn ("Blocks Remote Control: " + ($found -join ', ') + " -> remove them.") } else { Ok "No variable blocking Remote Control" }

# 3. TypeSafe key
if ([Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY', 'User')) {
  Ok "TYPESAFE_API_KEY is already set (User)"
} else {
  $sec = Read-Host "TypeSafe API key (Enter = skip, until Jev access is available)" -AsSecureString
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
  if ($plain) {
    [Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', $plain, 'User')
    $env:TYPESAFE_API_KEY = $plain
    Ok "TYPESAFE_API_KEY saved (User). A new terminal is needed for claude to see it too."
  } else { Ok "Without a key, the local (keyword-based) router runs; Jev takes over automatically once a key is provided." }
}

# 4. CLAUDE_CODE_SHELL into the user-level Claude settings
if ($GitBash) {
  $dir = Join-Path $env:USERPROFILE '.claude'
  $f = Join-Path $dir 'settings.json'
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $cfg = if (Test-Path $f) { Get-Content $f -Raw -Encoding UTF8 | ConvertFrom-Json } else { New-Object psobject }
  if (-not $cfg) { $cfg = New-Object psobject }
  if (-not ($cfg.PSObject.Properties.Name -contains 'env')) { $cfg | Add-Member -NotePropertyName env -NotePropertyValue (New-Object psobject) }
  if ($cfg.env.CLAUDE_CODE_SHELL -eq $GitBash) {
    Ok "CLAUDE_CODE_SHELL is already Git Bash"
  } else {
    if (Test-Path $f) { Copy-Item $f "$f.bak" -Force }
    Remove-TypeData System.Array -ErrorAction SilentlyContinue  # PS 5.1: don't write arrays as {"value":..,"Count":..}
    $cfg.env | Add-Member -NotePropertyName CLAUDE_CODE_SHELL -NotePropertyValue $GitBash -Force
    [IO.File]::WriteAllText($f, ($cfg | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding $false))
    Ok "CLAUDE_CODE_SHELL written: $f (backup: settings.json.bak)"
  }
}

# 5. Keep awake
if ($KeepAwake) {
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
  powercfg /setactive SCHEME_CURRENT
  Ok "On AC power: no sleep/hibernate, closing the lid does not put it to sleep"
}

# 6. Autostart
if ($AutoStart) {
  $cmd = Join-Path $Repo 'start-rc.cmd'
  $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$cmd`"" -WorkingDirectory $Repo
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
  try {
    Register-ScheduledTask -TaskName 'ClaudeRemoteControl' -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
    Ok "Scheduled task: ClaudeRemoteControl (at logon)"
  } catch { Warn "Scheduled task was not created ($($_.Exception.Message)). Run in an admin PowerShell." }
  $agy = Join-Path $env:LOCALAPPDATA 'agy\bin\agy.exe'
  if (Test-Path $agy) { & $agy remote-control start; Ok "Antigravity remote-control daemon started (check: agy remote-control status)" }
  if (Get-Command codex -ErrorAction SilentlyContinue) { codex remote-control start; Ok "Codex remote-control daemon started (pair a phone: codex remote-control pair)" }
}

# 7. Codex / Antigravity (phase 3)
foreach ($t in 'codex', 'agy') {
  if (Get-Command $t -ErrorAction SilentlyContinue) { Ok "$t CLI found" } else { Warn "$t CLI not installed (phase 3, optional) - if just installed, open a new terminal" }
}

# 8. Router hook + MCP router, globally (Claude Code + Codex + Antigravity, for every project)
& $py router/install_hooks.py --apply

# 9. Shared skill hub (~/.skills) + generated worker agents + skill catalog
& $py router/skills_hub.py all --apply

# 10. Model-family policy (router/models.json)
& $py scripts/check_models.py
if ($LASTEXITCODE -eq 0) { Ok "Model-family policy OK" } else { Bad "Model-family policy violation (see above)" }

# 11. Smoke test
$out = '{"prompt":"Refaktorald az auth modult"}' | & $py router/run_hook.py claude UserPromptSubmit
Write-Host "Hook output: $out"
$logf = Join-Path $env:USERPROFILE '.jev-router\logs\routing.jsonl'
if (Test-Path $logf) { Write-Host ("Last log line: " + (Get-Content $logf -Tail 1 -Encoding UTF8)) }
if ("$out" -match 'backend=jev') { Ok "Router works, with the Jev backend" }
elseif ("$out" -match 'backend=local') {
  if ($env:TYPESAFE_API_KEY) { Warn "A key is set, but the Jev call failed -> local backend. See the log line's 'error' field." }
  else { Ok "Router works, with the local backend (no Jev key)" }
}
else { Bad "Unexpected hook output" }

Write-Host ""
& $py scripts/check_tools.py
Write-Host ""
Write-Host "Next: run 'codex' once and trust the router hook in /hooks; pair your phone (README: Phone access)."
