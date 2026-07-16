# Build do Copiloto Dota 2: versao (git) -> exe (PyInstaller) -> Setup (Inno).
#
#   .\scripts\build_installer.ps1              # build completo
#   .\scripts\build_installer.ps1 -SkipInstaller  # so o exe (dist\CopilotoDota2\)
#
# Saida: dist\CopilotoDota2-Setup-<versao>.exe
param([switch]$SkipInstaller)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---- 1) Versao automatica: 1.0.<numero de commits> --------------------------
$count = (& git rev-list --count HEAD).Trim()
if (-not $count) { throw "nao consegui contar os commits (git)" }
$version = "1.0.$count"
Write-Host "==> Versao: $version"

# copiloto/_version.py (fora do git; em dev o app mostra 'dev')
"__version__ = `"$version`"" | Out-File -Encoding utf8 (Join-Path $root "copiloto\_version.py")

# Propriedades do exe (botao direito -> Detalhes)
New-Item -ItemType Directory -Force (Join-Path $root "build") | Out-Null
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=(1, 0, $count, 0), prodvers=(1, 0, $count, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'Copiloto Dota 2'),
      StringStruct('FileDescription', 'Copiloto Dota 2'),
      StringStruct('FileVersion', '$version'),
      StringStruct('ProductVersion', '$version'),
      StringStruct('CompanyName', 'TeteuPower'),
      StringStruct('LegalCopyright', 'TeteuPower')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Out-File -Encoding utf8 (Join-Path $root "build\version_info.txt")

# ---- 2) Exe (PyInstaller) ----------------------------------------------------
python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "==> Instalando PyInstaller..."; python -m pip install pyinstaller }

Write-Host "==> PyInstaller (dist\CopilotoDota2\)..."
python -m PyInstaller --noconfirm --clean CopilotoDota2.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou" }

if ($SkipInstaller) { Write-Host "==> OK (so exe)."; exit 0 }

# ---- 3) Instalador (Inno Setup) ----------------------------------------------
$iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup nao encontrado (winget install JRSoftware.InnoSetup)" }

Write-Host "==> Inno Setup ($iscc)..."
& $iscc "/DAppVersion=$version" (Join-Path $root "installer\installer.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC falhou" }

Write-Host "==> PRONTO: dist\CopilotoDota2-Setup-$version.exe"
