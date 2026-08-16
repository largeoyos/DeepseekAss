param(
    [string]$AppName = "DeepseekAss",
    [string]$ControlName = "DeepseekAssControl",
    [switch]$Clean,
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BuildVenv = Join-Path $ProjectRoot ".venv-build"
$PythonExe = Join-Path $BuildVenv "Scripts\python.exe"
$PyInstallerExe = Join-Path $BuildVenv "Scripts\pyinstaller.exe"
$EntryPoint = Join-Path $ProjectRoot "gui_main.py"
$ControlEntryPoint = Join-Path $ProjectRoot "control_main.py"
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

if (-not (Test-Path $ControlEntryPoint)) {
    throw "Control entry point not found: $ControlEntryPoint"
}

if ($Clean) {
    Write-Step "Cleaning previous build output"
    Remove-Item -LiteralPath $DistDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $BuildDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "$AppName.spec") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "$ControlName.spec") -Force -ErrorAction SilentlyContinue
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

$ControlPyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--console",
    "--name", $ControlName,
    "--distpath", $DistDir,
    "--workpath", $BuildDir,
    "--specpath", $ProjectRoot,
    "--hidden-import", "mcp.server",
    "--hidden-import", "mcp.server.mcpserver",
    "--hidden-import", "mcp.server.stdio",
    "--hidden-import", "mcp_types",
    "--exclude-module", "PyQt6",
    "--exclude-module", "langchain",
    "--exclude-module", "langchain_core",
    "--exclude-module", "langchain_openai",
    "--exclude-module", "langgraph",
    "--exclude-module", "llama_index",
    "--exclude-module", "numpy",
    "--exclude-module", "pandas",
    "--exclude-module", "scipy",
    "--exclude-module", "matplotlib",
    "--exclude-module", "sklearn",
    "--exclude-module", "transformers",
    "--exclude-module", "nltk",
    "--exclude-module", "tkinter",
    "--exclude-module", "PIL",
    "--exclude-module", "docx",
    "--exclude-module", "sqlalchemy",
    "--exclude-module", "playwright"
) + $ModeArgs + @($ControlEntryPoint)

Write-Step "Building $AppName.exe"
Push-Location $ProjectRoot
try {
    & $PyInstallerExe @PyInstallerArgs
    Write-Step "Building $ControlName.exe"
    & $PyInstallerExe @ControlPyInstallerArgs
}
finally {
    Pop-Location
}

if ($OneFile) {
    $ExePath = Join-Path $DistDir "$AppName.exe"
    $ControlExePath = Join-Path $DistDir "$ControlName.exe"
}
else {
    $ExePath = Join-Path $DistDir "$AppName\$AppName.exe"
    $ControlExePath = Join-Path $DistDir "$ControlName\$ControlName.exe"
}

if (-not (Test-Path $ExePath)) {
    throw "Build finished but exe was not found: $ExePath"
}

if (-not (Test-Path $ControlExePath)) {
    throw "Build finished but control executable was not found: $ControlExePath"
}

Write-Step "Build complete"
Write-Host "Executable: $ExePath" -ForegroundColor Green
Write-Host "Control executable: $ControlExePath" -ForegroundColor Green
Write-Host ""
Write-Host "Run it with:"
Write-Host "  & `"$ExePath`""
