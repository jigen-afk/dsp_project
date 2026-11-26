Unicode true
Name "DSP Studio"
OutFile "${OUTPUT}"
RequestExecutionLevel user
SilentInstall silent
SetCompressor /SOLID lzma
SetCompressorDictSize 64

!include "FileFunc.nsh"

Section
    System::Call 'Kernel32::GetCurrentDirectoryW(i ${NSIS_MAX_STRLEN}, w .r2)'
    InitPluginsDir
    SetOutPath "$PLUGINSDIR\DSPStudio"
    File /r "${PAYLOAD}\*"

    System::Call 'Kernel32::SetEnvironmentVariableW(w "DSPSTUDIO_PORTABLE_DIR", w "$EXEDIR")'
    ${GetParameters} $1
    StrCmp $2 "" 0 +2
        StrCpy $2 "$EXEDIR"
    SetOutPath "$2"
    ClearErrors
    ExecWait '"$PLUGINSDIR\DSPStudio\DSPStudio.exe" $1' $0
    IfErrors 0 +2
        StrCpy $0 1
    SetErrorLevel $0
SectionEnd
