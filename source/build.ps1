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

$dspVsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
if (!(Test-Path -LiteralPath $dspVsWhere -PathType Leaf)) {
    throw '构建便携 EXE 需要安装 Visual Studio C++ 生成工具和 Windows SDK'
}
$dspVs = & $dspVsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or !$dspVs) {
    throw '未找到用于构建便携 EXE 的 MSVC x64 工具链'
}
$dspLauncher = Join-Path $dspBuild 'launcher'
New-Item -ItemType Directory -Path $dspLauncher -Force | Out-Null
$dspPayload = Join-Path $dspBundle 'DSPStudio'
& $dspPython 'tools/pack_portable.py' $dspPayload (Join-Path $dspLauncher 'payload.bin')
if ($LASTEXITCODE -ne 0) { throw '单文件资源压缩失败' }
Set-Content -LiteralPath (Join-Path $dspLauncher 'portable.rc') -Encoding ascii -Value '1 RCDATA "payload.bin"'
& 'tools/build_launcher.cmd' $dspVs $dspLauncher (Join-Path $PSScriptRoot 'tools/portable.cpp')
if ($LASTEXITCODE -ne 0) { throw '单文件启动程序构建失败' }
New-Item -ItemType Directory -Path 'dist' -Force | Out-Null
Move-Item -LiteralPath (Join-Path $dspLauncher 'DSPStudio.exe') -Destination 'dist/DSPStudio.exe' -Force
Write-Host ('Windows EXE {0:N1} MB' -f ((Get-Item -LiteralPath 'dist/DSPStudio.exe').Length / 1000000))
Write-Host '构建完成。可直接运行或复制 dist\DSPStudio.exe。'
