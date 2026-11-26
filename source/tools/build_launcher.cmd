@echo off
setlocal
call "%~1\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
pushd "%~2" || exit /b 1
rc /nologo /fo portable.res portable.rc
if errorlevel 1 exit /b 1
cl /nologo /O2 /MT /EHsc /std:c++20 /utf-8 /W4 "%~3" portable.res /Fe:DSPStudio.exe /link /SUBSYSTEM:WINDOWS cabinet.lib user32.lib
exit /b %errorlevel%
