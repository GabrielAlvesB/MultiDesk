# Gera dist\installer\MultiDesk-Setup-<versão>.exe
# Requer: Python 3.10+ e Inno Setup 6 (winget install JRSoftware.InnoSetup)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
$py = ".venv\Scripts\python.exe"
& $py -m pip install -q -r requirements.txt pyinstaller
if ($LASTEXITCODE) { throw "pip falhou" }

& $py -m compileall -q multidesk
if ($LASTEXITCODE) { throw "Erro de sintaxe no código" }
$version = & $py -c "import multidesk; print(multidesk.__version__)"
& $py tools\make_icon.py

& $py -m PyInstaller --noconfirm --clean --windowed --name MultiDesk `
    --icon packaging\windows\multidesk.ico `
    --add-data "multidesk\assets;multidesk\assets" `
    --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 `
    --distpath dist --workpath build packaging\entry.py
if ($LASTEXITCODE) { throw "PyInstaller falhou" }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup não encontrado. Instale com: winget install JRSoftware.InnoSetup" }

& $iscc /Qp "/DAppVersion=$version" "/DSourceDir=$PSScriptRoot\dist\MultiDesk" packaging\windows\multidesk.iss
if ($LASTEXITCODE) { throw "Inno Setup falhou" }
Write-Host "`nPronto: dist\installer\MultiDesk-Setup-$version.exe"
