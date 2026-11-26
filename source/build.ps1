$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败' }
}
$dspPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $dspPython -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }
& $dspPython -m pip install pyinstaller==6.22.3 pytest
if ($LASTEXITCODE -ne 0) { throw '安装构建工具失败' }
& $dspPython -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw '测试未通过' }

$dspBuild = Join-Path $PSScriptRoot 'build'
$dspBundle = Join-Path $dspBuild 'portable'
$dspOriginalPath = $env:PATH
try {
    # Qt uses the Windows system ICU ABI. Poppler's versioned ICU has the same
    # filename but incompatible exports; prevent dependency scanning from taking it.
    $env:PATH = (($env:PATH -split ';') | Where-Object { $_ -notmatch '(?i)poppler' }) -join ';'
    & $dspPython -m PyInstaller --noconfirm --clean --distpath $dspBundle `
        --workpath (Join-Path $dspBuild 'pyinstaller') 'tools/portable.spec'
    if ($LASTEXITCODE -ne 0) { throw '打包失败' }
} finally {
    $env:PATH = $dspOriginalPath
}

$dspToolRoot = Join-Path $dspBuild 'tools'
$dspNsis = Join-Path $dspToolRoot 'nsis-3.13/makensis.exe'
if (!(Test-Path -LiteralPath $dspNsis -PathType Leaf)) {
    New-Item -ItemType Directory -Path $dspToolRoot -Force | Out-Null
    $dspNsisZip = Join-Path $dspToolRoot 'nsis-3.13.zip'
    $dspNsisUrl = 'https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.13/nsis-3.13.zip'
    $dspNsisHash = 'ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8'
    Invoke-WebRequest -UseBasicParsing -Uri $dspNsisUrl -OutFile $dspNsisZip -TimeoutSec 90
    if ((Get-FileHash -LiteralPath $dspNsisZip -Algorithm SHA256).Hash -ne $dspNsisHash) {
        throw 'NSIS 构建工具校验失败'
    }
    Expand-Archive -LiteralPath $dspNsisZip -DestinationPath $dspToolRoot -Force
}
$dspPayload = Join-Path $dspBundle 'DSPStudio'
$dspOutput = Join-Path $dspBuild 'DSPStudio.exe'
& $dspNsis '/V2' "/DPAYLOAD=$dspPayload" "/DOUTPUT=$dspOutput" 'tools/portable.nsi'
if ($LASTEXITCODE -ne 0) { throw '单文件压缩失败' }
New-Item -ItemType Directory -Path 'dist' -Force | Out-Null
Move-Item -LiteralPath $dspOutput -Destination 'dist/DSPStudio.exe' -Force
Write-Host ('Windows EXE {0:N1} MB' -f ((Get-Item -LiteralPath 'dist/DSPStudio.exe').Length / 1000000))
Write-Host '构建完成。可直接运行或复制 dist\DSPStudio.exe。'
