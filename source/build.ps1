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
$dspOriginalPath = $env:PATH
try {
    # Qt uses the Windows system ICU ABI. Poppler's versioned ICU has the same
    # filename but incompatible exports; prevent dependency scanning from taking it.
    $env:PATH = (($env:PATH -split ';') | Where-Object { $_ -notmatch '(?i)poppler' }) -join ';'
    & $dspPython -m PyInstaller --noconfirm --clean --onedir --windowed --name DSPStudio --exclude-module matplotlib --exclude-module pandas --exclude-module torch --exclude-module sklearn --exclude-module pytest --exclude-module IPython --exclude-module tkinter --exclude-module numba --exclude-module cv2 --exclude-module sphinx --exclude-module docutils --exclude-module PIL --exclude-module pygments --exclude-module babel --exclude-module jinja2 main.py
    if ($LASTEXITCODE -ne 0) { throw '打包失败' }
} finally {
    $env:PATH = $dspOriginalPath
}
Write-Host '构建完成：dist\DSPStudio\DSPStudio.exe。移动整个 DSPStudio 文件夹，不能只复制 exe。'
