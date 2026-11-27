param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$release = [System.IO.Path]::GetFullPath($Destination)
$executable = Join-Path $project 'dist\DSPStudio.exe'
if (!(Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw '请先运行 build.ps1 生成 dist\DSPStudio.exe。'
}
if ((Test-Path -LiteralPath $release) -and @(Get-ChildItem -LiteralPath $release -Force).Count -gt 0) {
    throw "交付目录非空，为保护已有数据不覆盖：$release"
}
New-Item -ItemType Directory -Path $release -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $release 'source') | Out-Null
New-Item -ItemType Directory -Path (Join-Path $release 'app') | Out-Null
Copy-Item -LiteralPath $executable -Destination (Join-Path $release 'app')
foreach ($folder in @('dspstudio','tests','tools')) {
    $target = Join-Path $release "source\$folder"
    New-Item -ItemType Directory -Path $target | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $project $folder) -File | Copy-Item -Destination $target
}
foreach ($filename in @('main.py','requirements.txt','requirements-lock.txt','build.ps1','使用说明.md')) {
    Copy-Item -LiteralPath (Join-Path $project $filename) -Destination (Join-Path $release 'source')
}
foreach ($folder in @('samples','verification')) {
    $optional = Join-Path $project $folder
    if (Test-Path -LiteralPath $optional -PathType Container) {
        Copy-Item -LiteralPath $optional -Destination (Join-Path $release $folder) -Recurse
    }
}
Copy-Item -LiteralPath (Join-Path $project '使用说明.md') -Destination $release
$launcher = @'
@echo off
cd /d "%~dp0"
start "" "%~dp0app\DSPStudio.exe"
'@
[System.IO.File]::WriteAllText((Join-Path $release '启动软件.cmd'),$launcher,[System.Text.Encoding]::ASCII)
$readme = @'
数字音效实验室 DSP Studio

双击“启动软件.cmd”开始使用，也可运行 app\DSPStudio.exe。
DSPStudio.exe 可独立运行，无需安装 Python。

必做功能：麦克风实录、语音增强、回声产生与消除、项目组成员说话人识别。
附加功能：波形/频谱、变速变调、机器人音、电话音、音量调整、WAV 导入导出。

先录制成员语音并注册，再用新的录音识别。本软件没有预置真实成员声纹。
本版为录音后处理；真实声学回声消除需要远端参考及安静校准时段。

使用说明.md：操作、算法、实验方法、常见问题。
source：源码与测试。若随包提供，samples 存放合成实验音频，verification 存放测试证据。
data：启动后自动生成的本地声纹、日志和默认导出目录。
'@
[System.IO.File]::WriteAllText((Join-Path $release '先读我.txt'),$readme,[System.Text.UTF8Encoding]::new($true))
Get-ChildItem -LiteralPath $release -Recurse -File | ForEach-Object {
    [pscustomobject]@{ path=$_.FullName.Substring($release.Length+1); bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $release 'release_manifest.json') -Encoding utf8
Write-Host "交付目录已生成：$release"
