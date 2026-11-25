# Install SimSight Companion (the SimSight mod, its speech pack and the phone
# companion) into The Sims 4 Mods folder: from a release folder (SimSight next
# to this script) or from the source tree (dist\SimSight-windows\SimSight).
# Reversible: backs up Options.ini and saves first; -Remove moves the mod to
# SimSight Backups. Your settings (SimSight.cfg) are kept.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 [-UserDir <folder>] [-Yes] [-Remove]
#     [-WaitForGame <seconds>]   (an update installed as the game quits waits for it to close)
#
# Windows PowerShell 5.1 or later. Keep this file ASCII: 5.1 reads scripts
# without a byte order mark as ANSI.
param([string]$UserDir = '', [switch]$Yes, [switch]$Remove, [int]$WaitForGame = 0)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

# One plain line per step: screen readers read new console lines by themselves.
function Tell([string]$Text) { Write-Host $Text }

function Stop-Here([string]$Text) {
  Tell $Text
  exit 1
}

# The Sims 4 user folder: Documents\Electronic Arts\<The Sims 4, in the game's
# language>, the one with Options.ini. Documents may be in OneDrive.
function Find-UserDirs {
  $docs = @([Environment]::GetFolderPath('MyDocuments'), (Join-Path $env:USERPROFILE 'Documents'))
  if ($env:OneDrive) { $docs += (Join-Path $env:OneDrive 'Documents') }
  $found = @()
  foreach ($d in $docs) {
    if (-not $d) { continue }
    $ea = Join-Path $d 'Electronic Arts'
    if (-not (Test-Path -LiteralPath $ea)) { continue }
    foreach ($sub in Get-ChildItem -LiteralPath $ea -Directory) {
      if ((Test-Path -LiteralPath (Join-Path $sub.FullName 'Options.ini')) -and ($found -notcontains $sub.FullName)) {
        $found += $sub.FullName
      }
    }
  }
  return ,$found
}

function Choose-UserDir {
  $dirs = Find-UserDirs
  if ($dirs.Count -eq 0) {
    Stop-Here 'The Sims 4 settings were not found. Start the game once, quit it, then try again. Nothing was changed.'
  }
  if ($dirs.Count -eq 1) { return $dirs[0] }
  Tell 'Several Sims 4 folders were found:'
  for ($i = 0; $i -lt $dirs.Count; $i++) { Tell ("{0}: {1}" -f ($i + 1), $dirs[$i]) }
  $n = Read-Host 'Type the number of the folder your game uses, then press Enter'
  $k = 0
  if (-not [int]::TryParse($n, [ref]$k) -or $k -lt 1 -or $k -gt $dirs.Count) {
    Stop-Here 'No folder chosen. Nothing was changed.'
  }
  return $dirs[$k - 1]
}

function Test-GameRunning {
  return [bool](Get-Process -Name 'TS4_x64', 'TS4_DX9_x64', 'TS4' -ErrorAction SilentlyContinue)
}

# Turn on "Enable Custom Content and Mods" and "Script Mods Allowed".
function Enable-Mods([string]$Ini) {
  $text = [IO.File]::ReadAllText($Ini)
  $new = $text
  foreach ($pair in @(@('scriptmodsenabled', '1'), @('modsdisabled', '0'))) {
    $key = $pair[0]; $value = $pair[1]
    $pattern = '(?im)^(\s*' + $key + '\s*=\s*)\d+'
    if ([regex]::IsMatch($new, $pattern)) {
      $new = [regex]::Replace($new, $pattern, '${1}' + $value)
    } elseif ([regex]::IsMatch($new, '(?im)^\[options\]\s*$')) {
      $new = [regex]::Replace($new, '(?im)^(\[options\]\s*?)(\r?\n)', '${1}${2}' + $key + ' = ' + $value + '${2}', 1)
    }
  }
  if ($new -ne $text) { [IO.File]::WriteAllText($Ini, $new) }
}

$here = $PSScriptRoot
$src = Join-Path $here 'SimSight'
if (-not (Test-Path -LiteralPath (Join-Path $src 'SimSight.ts4script'))) {
  $src = Join-Path (Split-Path $here -Parent) 'dist\SimSight-windows\SimSight'
}

for ($i = 0; $i -lt $WaitForGame -and (Test-GameRunning); $i++) { Start-Sleep -Seconds 1 }
if (Test-GameRunning) {
  Stop-Here 'The Sims 4 is running. Save, quit the game, then try again. Nothing was changed.'
}
if (-not $UserDir) { $UserDir = Choose-UserDir }
$mods = Join-Path $UserDir 'Mods'
$dest = Join-Path $mods 'SimSight'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backups = Join-Path $UserDir 'SimSight Backups'

if (-not $Yes -and -not $Remove) {
  if (Test-Path -LiteralPath $dest) {
    $answer = Read-Host 'SimSight is installed. Press Enter to update it, type r then Enter to remove it, or type n then Enter to stop'
    if ($answer -match '^[nN]') { Tell 'Stopped. Nothing was changed.'; exit 0 }
    if ($answer -match '^[rR]') { $Remove = $true }
  } else {
    $answer = Read-Host 'Press Enter to install SimSight, or type n then Enter to stop'
    if ($answer -match '^[nN]') { Tell 'Stopped. Nothing was changed.'; exit 0 }
  }
}

if ($Remove) {
  if (-not (Test-Path -LiteralPath $dest)) { Tell 'SimSight is not installed. Nothing was changed.'; exit 0 }
  $park = Join-Path $backups "removed-$stamp"
  New-Item -ItemType Directory -Force -Path $park | Out-Null
  Move-Item -LiteralPath $dest -Destination $park
  Tell "SimSight is removed. Your saves were not touched; the mod folder was moved to $park"
  exit 0
}

if (-not (Test-Path -LiteralPath (Join-Path $src 'SimSight.ts4script'))) {
  Stop-Here 'The SimSight files are missing next to this installer. Nothing was changed.'
}

# The mod is made for one game version; a newer game may break parts of it.
$versionFile = Join-Path $here 'VERSION'
$gameVersionFile = Join-Path $UserDir 'GameVersion.txt'
if ((Test-Path -LiteralPath $versionFile) -and (Test-Path -LiteralPath $gameVersionFile)) {
  $want = ''
  foreach ($line in Get-Content -LiteralPath $versionFile) { if ($line -like 'game=*') { $want = $line.Substring(5) } }
  $raw = [IO.File]::ReadAllText($gameVersionFile, [Text.Encoding]::GetEncoding(28591))
  $m = [regex]::Match($raw, '\d+\.\d+\.\d+')
  if ($want -and $m.Success -and $m.Value -ne $want) {
    Tell "Note: this SimSight Companion is made for game version $want, your game is $($m.Value). It usually works; if something breaks, look for a newer SimSight Companion."
  }
}

Tell 'Step 1 of 3: backing up your game settings and saves.'
$backup = Join-Path $backups $stamp
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -LiteralPath (Join-Path $UserDir 'Options.ini') -Destination $backup
$saves = Join-Path $UserDir 'saves'
if (Test-Path -LiteralPath $saves) { Copy-Item -LiteralPath $saves -Destination $backup -Recurse }  # copy only; saves are never modified
if (Test-Path -LiteralPath $mods) {
  Get-ChildItem -LiteralPath $mods -Force | Select-Object Name, Length, LastWriteTime |
    Out-File -FilePath (Join-Path $backup 'Mods-listing.txt') -Encoding utf8
}
if (Test-Path -LiteralPath $dest) {
  New-Item -ItemType Directory -Force -Path (Join-Path $backup 'Mods') | Out-Null
  Copy-Item -LiteralPath $dest -Destination (Join-Path $backup 'Mods') -Recurse
}
Tell "Backup: $backup"

Tell 'Step 2 of 3: copying the mod.'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$files = @('SimSight.ts4script', 'SimSight_UI.package', 'simsight-bridge.exe', 'cloudflared.exe',
  'cloudflared-LICENSE.txt', 'LICENSE.txt', 'README.txt', 'CONTROLS.txt', 'CHEATS.txt', 'SimSight Setup.cmd')
foreach ($f in $files) {
  Copy-Item -LiteralPath (Join-Path $src $f) -Destination (Join-Path $dest $f) -Force
}
$pack = Join-Path $dest 'SimSight_SpeechPack'
if (Test-Path -LiteralPath $pack) { Remove-Item -LiteralPath $pack -Recurse -Force }
Copy-Item -LiteralPath (Join-Path $src 'SimSight_SpeechPack') -Destination $pack -Recurse
# Downloaded with a browser, Windows would warn before starting the helpers.
Get-ChildItem -LiteralPath $dest -Recurse -File | Unblock-File

Tell 'Step 3 of 3: allowing mods in the game settings.'
Enable-Mods (Join-Path $UserDir 'Options.ini')
Tell "Installed to: $dest"
Tell 'SimSight Companion is installed. Start your screen reader, then The Sims 4, and load a household. Control Alt H opens the accessibility menu.'
