# FreeCAD x Claude: one-step installer for Windows (PowerShell).
#
#   irm https://raw.githubusercontent.com/Scharmen/FreeCadClaudeBridge/main/install.ps1 | iex
#
# Needs FreeCAD and Claude Desktop installed and signed in; the wizard checks both and shows
# where to get what is missing. It installs uv (if missing, after asking), the Claude plugin
# and the Claude panel for FreeCAD.
# FREECAD_BRIDGE_SRC overrides where the installer itself comes from (CI uses the checkout);
# FREECAD_BRIDGE_WIZARD_ARGS passes options to the wizard (e.g. "--yes").
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$src = $env:FREECAD_BRIDGE_SRC
if (-not $src) {
    $src = "freecad-bridge @ https://github.com/Scharmen/FreeCadClaudeBridge/archive/refs/heads/main.zip#subdirectory=plugin/server"
}

Write-Host "FreeCAD x Claude installer"
Write-Host ""

function Find-Uvx {
    $cmd = Get-Command uvx -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($c in @("$env:USERPROFILE\.local\bin\uvx.exe", "$env:USERPROFILE\.cargo\bin\uvx.exe")) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

$uvx = Find-Uvx
if (-not $uvx) {
    Write-Host "The installer needs uv, a small tool from Astral that runs the FreeCAD bridge"
    Write-Host "(https://docs.astral.sh/uv/)."
    $answer = "y"
    if ([Environment]::UserInteractive -and -not $env:CI) {
        $answer = Read-Host "  Install uv now? [Y/n]"
    }
    if ($answer -match '^[nN]') {
        Write-Host "Nothing was changed. Install uv and run this installer again."
        return
    }
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    $uvx = Find-Uvx
    if (-not $uvx) {
        Write-Host "uv was installed but not found; open a new PowerShell window and run this installer again."
        return
    }
}

$wizardArgs = @()
if ($env:FREECAD_BRIDGE_WIZARD_ARGS) { $wizardArgs = $env:FREECAD_BRIDGE_WIZARD_ARGS -split " " }
# A window for people who started it themselves; the console for CI, --yes and --no-gui.
if ([Environment]::UserInteractive -and -not $env:CI -and -not ($wizardArgs -contains "--yes") -and
    -not ($wizardArgs -contains "--no-gui")) {
    Write-Host "Opening the setup window ..."
    $wizardArgs = @("--gui") + $wizardArgs
}
# --refresh: always the current installer, not a cached copy from an earlier run.
# Managed Python first: it brings Tk for the window.
& $uvx --refresh --python-preference managed --from $src freecad-bridge wizard @wizardArgs
$global:LASTEXITCODE = $LASTEXITCODE
