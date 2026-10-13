# Costruisce CueStage per Windows 10/11 (64 bit): cartella portable (.zip) e installatore (setup.exe).
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1      (dalla cartella del progetto)
#
# Serve: Python 3.11-3.13 da python.org (con «py» launcher) e la connessione a Internet.
# Per l'installatore serve anche Inno Setup 6 (https://jrsoftware.org/isdl.php oppure «choco install innosetup»);
# senza, si ottiene solo lo zip portable.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$Build = Join-Path $Root "build\windows"
$Cache = Join-Path $Root "build\cache"
New-Item -ItemType Directory -Force -Path $Build, $Cache, (Join-Path $Root "dist") | Out-Null
$Version = (Select-String -Path "cuestage\__init__.py" -Pattern '__version__\s*=\s*"(.*)"').Matches[0].Groups[1].Value
function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }

$Py = if ($env:PYTHON) { $env:PYTHON } else { "py" }
$PyArgs = if ($Py -eq "py") { @("-3") } else { @() }
Say "ambiente Python"
& $Py @PyArgs -m venv "$Build\venv"
& "$Build\venv\Scripts\python.exe" -m pip install -q --upgrade pip
& "$Build\venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller
if ($LASTEXITCODE) { throw "pip non riuscito" }

Say "ffmpeg (BtbN, LGPL)"
$FfUrl = if ($env:FFMPEG_URL) { $env:FFMPEG_URL } else { "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip" }
$FfZip = Join-Path $Cache "ffmpeg-win64.zip"
if (-not (Test-Path $FfZip)) { Invoke-WebRequest -Uri $FfUrl -OutFile $FfZip -UseBasicParsing }
$FfTmp = Join-Path $Build "fftmp"; $FfDir = Join-Path $Build "ffmpeg"
Remove-Item -Recurse -Force $FfTmp, $FfDir -ErrorAction SilentlyContinue
Expand-Archive -Path $FfZip -DestinationPath $FfTmp
New-Item -ItemType Directory -Force -Path $FfDir | Out-Null
Get-ChildItem -Path $FfTmp -Recurse -Include ffmpeg.exe, ffprobe.exe, *.dll | Copy-Item -Destination $FfDir
& "$FfDir\ffprobe.exe" -version | Select-Object -First 1

Say "PyInstaller"
Remove-Item -Recurse -Force "dist\CueStage" -ErrorAction SilentlyContinue
$env:CUESTAGE_FFMPEG_DIR = $FfDir
& "$Build\venv\Scripts\pyinstaller.exe" packaging\cuestage.spec --noconfirm --distpath dist --workpath "$Build\pyi"
if ($LASTEXITCODE) { throw "PyInstaller non riuscito" }

Say "prova di avvio"
$Report = Join-Path $Build "self-test.txt"
$env:QT_QPA_PLATFORM = "offscreen"
$p = Start-Process -FilePath "dist\CueStage\CueStage.exe" -ArgumentList "--self-test", "`"$Report`"" -Wait -PassThru -NoNewWindow
Remove-Item Env:\QT_QPA_PLATFORM
Get-Content $Report -Encoding UTF8
if ($p.ExitCode -ne 0) { throw "la prova di avvio ha trovato problemi" }

Say "zip portable"
$Zip = "dist\CueStage-$Version-windows-portable.zip"
Remove-Item $Zip -ErrorAction SilentlyContinue
Compress-Archive -Path "dist\CueStage" -DestinationPath $Zip -CompressionLevel Optimal

$Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if ($Iscc) {
    Say "installatore"
    & $Iscc /Q "/DAppVersion=$Version" packaging\windows\cuestage.iss
    if ($LASTEXITCODE) { throw "Inno Setup non riuscito" }
} else {
    Write-Warning "Inno Setup non trovato: creato solo lo zip portable"
}
Say "fatto:"
Get-ChildItem dist\CueStage-$Version-windows-* | ForEach-Object { "  $($_.Name)  ($([math]::Round($_.Length / 1MB)) MB)" }
