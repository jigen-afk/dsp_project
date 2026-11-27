$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败' }
}
$dspPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $dspPython -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }
& $dspPython -m pip install pyinstaller==6.22.3 pytest zstandard==0.25.0
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
$dspZstd = Join-Path $dspBuild 'zstd-1.6.0'
$dspZstdLib = Join-Path $dspZstd 'lib'
$dspDecoderSource = Join-Path $dspZstd 'build/single_file_libs'
if (!(Test-Path -LiteralPath (Join-Path $dspDecoderSource 'combine.py') -PathType Leaf)) {
    $dspZstdArchive = Join-Path $dspBuild 'zstd-1.6.0.tar.gz'
    $dspZstdUrl = 'https://github.com/meta/zstd/releases/download/v1.6.0/zstd-1.6.0.tar.gz'
    $dspZstdHash = '2ba599ae2a257976278e7e71e998433a387bfd18aa7b78678229fd5d84f5e9b8'
    Invoke-WebRequest -UseBasicParsing -Uri $dspZstdUrl -OutFile $dspZstdArchive -TimeoutSec 90
    if ((Get-FileHash -LiteralPath $dspZstdArchive -Algorithm SHA256).Hash -ne $dspZstdHash) {
        throw 'Zstandard 源码校验失败'
    }
    tar.exe -xf $dspZstdArchive -C $dspBuild 'zstd-1.6.0/lib' 'zstd-1.6.0/build/single_file_libs' 'zstd-1.6.0/LICENSE'
    if ($LASTEXITCODE -ne 0) { throw 'Zstandard 源码解包失败' }
}
Push-Location -LiteralPath $dspDecoderSource
try {
    & $dspPython combine.py -r $dspZstdLib -x legacy/zstd_legacy.h -o (Join-Path $dspLauncher 'zstddeclib.c') zstddeclib-in.c
    if ($LASTEXITCODE -ne 0) { throw '生成 Zstandard 解压源码失败' }
} finally {
    Pop-Location
}
$dspPayload = Join-Path $dspBundle 'DSPStudio'
Copy-Item -LiteralPath (Join-Path $dspZstd 'LICENSE') -Destination (Join-Path $dspPayload 'Zstandard-LICENSE.txt')
& $dspPython 'tools/pack_portable.py' $dspPayload (Join-Path $dspLauncher 'payload.bin')
if ($LASTEXITCODE -ne 0) { throw '单文件资源压缩失败' }
Set-Content -LiteralPath (Join-Path $dspLauncher 'portable.rc') -Encoding ascii -Value '1 RCDATA "payload.bin"'
& 'tools/build_launcher.cmd' $dspVs $dspLauncher (Join-Path $PSScriptRoot 'tools/portable.cpp') $dspZstdLib
if ($LASTEXITCODE -ne 0) { throw '单文件启动程序构建失败' }
New-Item -ItemType Directory -Path 'dist' -Force | Out-Null
Move-Item -LiteralPath (Join-Path $dspLauncher 'DSPStudio.exe') -Destination 'dist/DSPStudio.exe' -Force
Write-Host ('Windows EXE {0:N1} MB' -f ((Get-Item -LiteralPath 'dist/DSPStudio.exe').Length / 1000000))
Write-Host '构建完成。可直接运行或复制 dist\DSPStudio.exe。'
