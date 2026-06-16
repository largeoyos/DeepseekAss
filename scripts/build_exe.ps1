param(
    [string]$AppName = "DeepseekAss",
    [switch]$Clean,
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BuildVenv = Join-Path $ProjectRoot ".venv-build"
$PythonExe = Join-Path $BuildVenv "Scripts\python.exe"
$PyInstallerExe = Join-Path $BuildVenv "Scripts\pyinstaller.exe"
$EntryPoint = Join-Path $ProjectRoot "gui_main.py"
$Requirements = Join-Path $ProjectRoot "requirements.txt"
$IconSvg = Join-Path $ProjectRoot "ui\icon.svg"
$DistDir = Join-Path $ProjectRoot "dist"
$BuildDir = Join-Path $ProjectRoot "build"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

if (-not (Test-Path $EntryPoint)) {
    throw "Entry point not found: $EntryPoint"
}

if (-not (Test-Path $Requirements)) {
    throw "Requirements file not found: $Requirements"
}

if ($Clean) {
    Write-Step "Cleaning previous build output"
    Remove-Item -LiteralPath $DistDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $BuildDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "$AppName.spec") -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $PythonExe)) {
    Write-Step "Creating isolated build virtual environment"
    py -3 -m venv $BuildVenv
}

Write-Step "Installing build dependencies"
& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r $Requirements pyinstaller

$Separator = [System.IO.Path]::PathSeparator
$AddData = "$IconSvg${Separator}ui"
$ModeArgs = @("--onedir")
if ($OneFile) {
    $ModeArgs = @("--onefile")
}

$PyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", $AppName,
    "--distpath", $DistDir,
    "--workpath", $BuildDir,
    "--specpath", $ProjectRoot,
    "--add-data", $AddData,
    "--collect-all", "PyQt6",
    "--hidden-import", "PyQt6.QtWebEngineWidgets"
) + $ModeArgs + @($EntryPoint)

Write-Step "Building $AppName.exe"
Push-Location $ProjectRoot
try {
    & $PyInstallerExe @PyInstallerArgs
}
finally {
    Pop-Location
}

if ($OneFile) {
    $ExePath = Join-Path $DistDir "$AppName.exe"
}
else {
    $ExePath = Join-Path $DistDir "$AppName\$AppName.exe"
}

if (-not (Test-Path $ExePath)) {
    throw "Build finished but exe was not found: $ExePath"
}

Write-Step "Build complete"
Write-Host "Executable: $ExePath" -ForegroundColor Green
Write-Host ""
Write-Host "Run it with:"
Write-Host "  & `"$ExePath`""
