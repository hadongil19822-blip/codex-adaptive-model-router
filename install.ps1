param(
    [string]$RuntimeRoot = (Join-Path $env:USERPROFILE ".codex\auto-router")
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$Python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $Python) {
    $Python = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $Python) {
    Write-Error "Python 3.9 or later is required. Install Python, then run this installer again."
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
Copy-Item (Join-Path $ProjectRoot "codex_router.py") (Join-Path $RuntimeRoot "codex_router.py") -Force
if (-not (Test-Path (Join-Path $RuntimeRoot "router_config.json"))) {
    Copy-Item (Join-Path $ProjectRoot "router_config.json") (Join-Path $RuntimeRoot "router_config.json")
} else {
    Write-Host "Keeping existing configuration: $RuntimeRoot\router_config.json"
}
Copy-Item (Join-Path $ProjectRoot "windows-widget\CodexAutoRouter.ps1") (Join-Path $RuntimeRoot "CodexAutoRouter.ps1") -Force
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $RuntimeRoot "python.path"), $Python.Source, $Utf8NoBom)

& $Python.Source (Join-Path $ProjectRoot "scripts\manage_hooks.py") install
& $Python.Source (Join-Path $RuntimeRoot "codex_router.py") stop | Out-Null
& $Python.Source (Join-Path $RuntimeRoot "codex_router.py") watch --all --daemon

$Shell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")
$Shortcut = $Shell.CreateShortcut((Join-Path $Desktop "Codex Auto Router.lnk"))
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $RuntimeRoot "CodexAutoRouter.ps1") + '"'
$Shortcut.WorkingDirectory = $RuntimeRoot
$Shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
$Shortcut.Description = "Codex Adaptive Model Router dashboard"
$Shortcut.Save()

Start-Process powershell.exe -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
    "-File", ('"' + (Join-Path $RuntimeRoot "CodexAutoRouter.ps1") + '"')
)

Write-Host ""
Write-Host "Installation complete."
Write-Host "Dashboard shortcut: $Desktop\Codex Auto Router.lnk"
Write-Host "One-time step: open Codex CLI, run /hooks, and trust the UserPromptSubmit hook."
