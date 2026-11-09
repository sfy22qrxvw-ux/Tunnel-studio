Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"
!include "WinVer.nsh"
!define VERSION "0.23.5"
!define PAYLOAD "payload"
!define BOOTSTRAP "bootstrap"
!define SETUP_ART "${PAYLOAD}/build_support/installer-art"
!ifndef SETUP_OUTFILE
  !define SETUP_OUTFILE "TunnelStudio_Setup_0.23.5_x64.exe"
!endif
Name "Tunnel Studio ${VERSION}"
Caption "Tunnel Studio Setup"
OutFile "${SETUP_OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\TunnelStudio\releases\${VERSION}"
RequestExecutionLevel user
ManifestDPIAware true
SetCompressor /SOLID zlib
CRCCheck on
ShowInstDetails show
ShowUninstDetails show
!define SETUP_BRANDING "Tunnel Studio · Damian Gulczynski"
BrandingText "${SETUP_BRANDING}"
VIProductVersion "0.23.5.0"
VIAddVersionKey /LANG=1033 "ProductName" "Tunnel Studio"
VIAddVersionKey /LANG=1033 "ProductVersion" "0.23.5"
VIAddVersionKey /LANG=1033 "FileVersion" "0.23.5.0"
VIAddVersionKey /LANG=1033 "FileDescription" "Tunnel Studio Windows x64 Installer"
VIAddVersionKey /LANG=1033 "CompanyName" "Damian Gulczynski"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright (c) 2026 Damian Gulczynski"
!define MUI_ICON "payload/app/web/tunnel.ico"
!define MUI_UNICON "payload/app/web/tunnel.ico"
!define MUI_ABORTWARNING
!define MUI_BGCOLOR "0D1017"
!define MUI_TEXTCOLOR "F5F5F7"
!define SETUP_MUTED "BCC8D9"
!define SETUP_CYAN "64D2FF"
!define SETUP_FRAME_COLORREF 0x4C3627
!define SETUP_CAPTION_COLORREF 0x17100D
!define SETUP_TITLE_COLORREF 0xF7F5F5
!define MUI_WELCOMEFINISHPAGE_BITMAP "${SETUP_ART}/background.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "${SETUP_ART}/header.bmp"
!define MUI_CUSTOMFUNCTION_GUIINIT ModernInstallerInit
!define MUI_PAGE_CUSTOMFUNCTION_SHOW ModernWelcomeShow
!define WELCOME_EYEBROW "SURVEY WORKSPACE"
!define WELCOME_TITLE "Tunnel Studio"
!define WELCOME_LEAD "Point clouds and survey analysis"
!define WELCOME_BODY "Register and clean scans. Build 3D models, compare scans with design, and monitor movement across survey epochs."
!define WELCOME_ACTION "Close Tunnel Studio before installing."
!define WELCOME_NOTICE "Preview build. Verify survey results independently."
!define MUI_WELCOMEPAGE_TITLE "${WELCOME_TITLE} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "${WELCOME_LEAD}$\r$\n$\r$\n${WELCOME_BODY}$\r$\n$\r$\n${WELCOME_ACTION}$\r$\n$\r$\n${WELCOME_NOTICE}"
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_HEADER_TEXT "Installing Tunnel Studio"
!define MUI_PAGE_HEADER_SUBTEXT "Preparing the application and its processing engines."
!define MUI_INSTFILESPAGE_COLORS "F5F5F7 090C12"
!define MUI_PAGE_CUSTOMFUNCTION_SHOW ModernInstallShow
!insertmacro MUI_PAGE_INSTFILES
!define MUI_PAGE_CUSTOMFUNCTION_SHOW ModernFinishShow
!define MUI_FINISHPAGE_TITLE_3LINES
!define MUI_FINISHPAGE_TEXT_LARGE
!define MUI_FINISHPAGE_TITLE "Tunnel Studio is ready"
!define MUI_FINISHPAGE_TEXT "Open Tunnel Studio now or use its shortcut in the Start menu."
!define MUI_FINISHPAGE_RUN "$INSTDIR\TunnelStudio.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Open Tunnel Studio"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
Var Stage
Var Base
Var ModernBodyFont
Var ModernHeadingFont
Var ModernHeaderFont
Var ModernNoteFont

!macro WelcomeLabel TOP HEIGHT FONT COLOR TEXT
  ${NSD_CreateLabel} 122u ${TOP}u 192u ${HEIGHT}u "${TEXT}"
  Pop $0
  SetCtlColors $0 "${COLOR}" transparent
  SendMessage $0 ${WM_SETFONT} ${FONT} 0
!macroend

!macro FillSetupPage PAGE IMAGE BITMAP ARTWORK
  ; The wizard creates a sidebar control. Expand it behind the native labels.
  System::Call '* (i 0, i 0, i 0, i 0) p .r0'
  System::Call 'user32::GetClientRect(p ${PAGE}, p r0)'
  System::Call '*$0(i, i, i .r1, i .r2)'
  System::Free $0
  ${NSD_ClearImage} ${IMAGE}
  ${NSD_FreeImage} ${BITMAP}
  ${NSD_RemoveStyle} ${IMAGE} ${WS_CLIPSIBLINGS}
  System::Call 'user32::SetWindowPos(p ${IMAGE}, p 1, i 0, i 0, i r1, i r2, i 0x0010)'
  ${NSD_SetStretchedImage} ${IMAGE} "$PLUGINSDIR\${ARTWORK}" ${BITMAP}
!macroend

Function ModernInstallerInit
  CreateFont $ModernBodyFont "Segoe UI" 9 400
  CreateFont $ModernHeadingFont "Segoe UI" 19 650
  CreateFont $ModernHeaderFont "Segoe UI" 9 600
  CreateFont $ModernNoteFont "Segoe UI" 8 500
  SetCtlColors $HWNDPARENT "${MUI_TEXTCOLOR}" "${MUI_BGCOLOR}"
  Call ModernInstallerFooter
  ; Use the dark Windows caption where DWM supports it.
  System::Call 'dwmapi::DwmSetWindowAttribute(p $HWNDPARENT, i 20, *i 1, i 4) i .r0'
  ${If} $0 != 0
    System::Call 'dwmapi::DwmSetWindowAttribute(p $HWNDPARENT, i 19, *i 1, i 4)'
  ${EndIf}
  ; Windows 11 frame, caption and caption text colours, in COLORREF byte order.
  System::Call 'dwmapi::DwmSetWindowAttribute(p $HWNDPARENT, i 34, *i ${SETUP_FRAME_COLORREF}, i 4)'
  System::Call 'dwmapi::DwmSetWindowAttribute(p $HWNDPARENT, i 35, *i ${SETUP_CAPTION_COLORREF}, i 4)'
  System::Call 'dwmapi::DwmSetWindowAttribute(p $HWNDPARENT, i 36, *i ${SETUP_TITLE_COLORREF}, i 4)'
  SendMessage $mui.Button.Next ${WM_SETFONT} $ModernBodyFont 1
  SendMessage $mui.Button.Cancel ${WM_SETFONT} $ModernBodyFont 1
  SendMessage $mui.Button.Back ${WM_SETFONT} $ModernBodyFont 1
  SendMessage $mui.Header.Text ${WM_SETFONT} $ModernHeaderFont 1
  SendMessage $mui.Header.SubText ${WM_SETFONT} $ModernBodyFont 1
  InitPluginsDir
  File "/oname=$PLUGINSDIR\setup-welcome.bmp" "${SETUP_ART}/welcome.bmp"
  !pragma verifyloadimage "${SETUP_ART}/welcome.bmp"
FunctionEnd

Function ModernInstallerFooter
  ; Preserve the native layout at the current DPI and paint one opaque label.
  System::Call '* (i 0, i 0, i 0, i 0) p .r0'
  System::Call 'user32::GetWindowRect(p $mui.Branding.Text, p r0)'
  System::Call 'user32::MapWindowPoints(p 0, p $HWNDPARENT, p r0, i 2)'
  System::Call '*$0(i .r1, i .r2, i .r3, i .r4)'
  System::Free $0
  IntOp $3 $3 - $1
  IntOp $4 $4 - $2
  ; WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS | SS_CENTERIMAGE | SS_NOPREFIX
  System::Call 'user32::CreateWindowExW(i 0, w "STATIC", w "${SETUP_BRANDING}", i 0x54000280, i r1, i r2, i r3, i r4, p $HWNDPARENT, p 0, p 0, p 0) p .r0'
  ${If} $0 P<> 0
    ShowWindow $mui.Branding.Background ${SW_HIDE}
    ShowWindow $mui.Branding.Text ${SW_HIDE}
    ; Keep the page show/hide handling pointed at the new label.
    StrCpy $mui.Branding.Background 0
    StrCpy $mui.Branding.Text $0
  ${Else}
    SetCtlColors $mui.Branding.Background "" "${MUI_BGCOLOR}"
  ${EndIf}
  SetCtlColors $mui.Branding.Text "${SETUP_MUTED}" "${MUI_BGCOLOR}"
  SendMessage $mui.Branding.Text ${WM_SETFONT} $ModernBodyFont 1
FunctionEnd

Function ModernWelcomeShow
  ShowWindow $mui.WelcomePage.Title ${SW_HIDE}
  ShowWindow $mui.WelcomePage.Text ${SW_HIDE}
  !insertmacro WelcomeLabel 15 13 $ModernHeaderFont ${SETUP_CYAN} "${WELCOME_EYEBROW}"
  !insertmacro WelcomeLabel 30 24 $ModernHeadingFont ${MUI_TEXTCOLOR} "${WELCOME_TITLE}"
  !insertmacro WelcomeLabel 87 17 $ModernHeaderFont ${MUI_TEXTCOLOR} "${WELCOME_LEAD}"
  !insertmacro WelcomeLabel 110 41 $ModernBodyFont ${SETUP_MUTED} "${WELCOME_BODY}"
  !insertmacro WelcomeLabel 157 16 $ModernBodyFont ${MUI_TEXTCOLOR} "${WELCOME_ACTION}"
  !insertmacro WelcomeLabel 177 13 $ModernNoteFont ${SETUP_MUTED} "${WELCOME_NOTICE}"
  !insertmacro FillSetupPage $mui.WelcomePage $mui.WelcomePage.Image $mui.WelcomePage.Image.Bitmap setup-welcome.bmp
FunctionEnd

Function ModernFinishShow
  SetCtlColors $mui.FinishPage.Title "${MUI_TEXTCOLOR}" transparent
  SetCtlColors $mui.FinishPage.Text "${MUI_TEXTCOLOR}" transparent
  SendMessage $mui.FinishPage.Title ${WM_SETFONT} $ModernHeadingFont 1
  SendMessage $mui.FinishPage.Text ${WM_SETFONT} $ModernBodyFont 1
  ${If} $mui.FinishPage.Run P<> 0
    ${NSD_AddExStyle} $mui.FinishPage.Run ${WS_EX_TRANSPARENT}
    System::Call 'uxtheme::SetWindowTheme(p $mui.FinishPage.Run, w "", w "")'
    SetCtlColors $mui.FinishPage.Run "${MUI_TEXTCOLOR}" transparent
  ${EndIf}
  !insertmacro FillSetupPage $mui.FinishPage $mui.FinishPage.Image $mui.FinishPage.Image.Bitmap modern-wizard.bmp
FunctionEnd

Function ModernInstallShow
  SetCtlColors $mui.InstFilesPage "" "${MUI_BGCOLOR}"
  SetCtlColors $mui.InstFilesPage.Text "${MUI_TEXTCOLOR}" "${MUI_BGCOLOR}"
  System::Call 'uxtheme::SetWindowTheme(p $mui.InstFilesPage.ProgressBar, w "", w "")'
  SendMessage $mui.InstFilesPage.ProgressBar 0x0409 0 0xFF840A ; PBM_SETBARCOLOR
  SendMessage $mui.InstFilesPage.ProgressBar 0x2001 0 0x392920 ; PBM_SETBKCOLOR
  SendMessage $mui.InstFilesPage.Text ${WM_SETFONT} $ModernBodyFont 1
  SendMessage $mui.InstFilesPage.Log ${WM_SETFONT} $ModernBodyFont 1
  SendMessage $mui.InstFilesPage.ShowLogButton ${WM_SETFONT} $ModernBodyFont 1
FunctionEnd

Function .onInit
  SetShellVarContext current
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "Tunnel Studio requires 64-bit Windows 10 or Windows 11." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "Tunnel Studio requires Windows 10 or Windows 11." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  StrCpy $Base "$LOCALAPPDATA\Programs\TunnelStudio\releases"
  StrCpy $INSTDIR "$Base\${VERSION}"
FunctionEnd

!macro Bootstrap
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\bootstrap"
  !include "bootstrap_files.nsh"
  SetOutPath "$PLUGINSDIR"
  File "windows_installer.py"
!macroend

!macro CheckClosed
  Delete "$PLUGINSDIR\install_error.txt"
  ExecWait '"$PLUGINSDIR\bootstrap\pythonw.exe" "$PLUGINSDIR\windows_installer.py" --check-closed --error-file "$PLUGINSDIR\install_error.txt"' $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Close Tunnel Studio and its browser recovery window, then try again. If it is already closed, check %LOCALAPPDATA%\TunnelStudio\installation_${VERSION}_error.txt." /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
!macroend

Section "Tunnel Studio" SEC_MAIN
  !insertmacro Bootstrap
  !insertmacro CheckClosed
  System::Call 'kernel32::GetCurrentProcessId() i .r0'
  System::Call 'kernel32::GetTickCount() i .r1'
  StrCpy $Stage "$Base\.installing-${VERSION}-$0-$1"
  CreateDirectory "$Stage"
  SetOutPath "$Stage"
  DetailPrint "Unpacking Tunnel Studio ${VERSION} and the bundled Windows runtime..."
  !include "payload_files.nsh"
  ; The stable Windows program entry is published only after this file exists.
  ClearErrors
  WriteUninstaller "$Stage\UninstallTunnelStudio.exe"
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "The installer could not create the staged uninstaller. Your previous installation and survey data have been kept." /SD IDOK
    SetErrorLevel 3
    Abort
  ${EndIf}
  ; Release the staging working directory before Windows renames it atomically.
  SetOutPath "$PLUGINSDIR"
  DetailPrint "Checking installation. Verification progress is shown below."
  Delete "$PLUGINSDIR\install_error.txt"
  nsExec::ExecToLog '"$PLUGINSDIR\bootstrap\python.exe" -u "$PLUGINSDIR\windows_installer.py" --stage "$Stage" --base "$Base" --error-file "$PLUGINSDIR\install_error.txt"'
  Pop $0
  ${If} $0 S!= "0"
  ${AndIf} $0 S!= "20"
    StrCpy $2 "The installer could not activate this update."
    ClearErrors
    FileOpen $1 "$PLUGINSDIR\install_error.txt" r
    ${IfNot} ${Errors}
      FileRead $1 $2
      FileClose $1
    ${EndIf}
    MessageBox MB_OK|MB_ICONSTOP "$2$\r$\n$\r$\nYour previous installation and survey data have been kept. Details: %LOCALAPPDATA%\TunnelStudio\installation_${VERSION}_error.txt" /SD IDOK
    SetErrorLevel 3
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR"
  CreateShortCut "$DESKTOP\Tunnel Studio.lnk" "$INSTDIR\TunnelStudio.exe" "" "$INSTDIR\app\web\tunnel.ico" 0
  CreateShortCut "$SMPROGRAMS\Tunnel Studio.lnk" "$INSTDIR\TunnelStudio.exe" "" "$INSTDIR\app\web\tunnel.ico" 0
  ${If} $0 == 20
    MessageBox MB_OK|MB_ICONINFORMATION "Tunnel Studio ${VERSION} is installed. Some older installation entries or files were kept because they could not be safely removed. Your survey data is unchanged. Details: %LOCALAPPDATA%\TunnelStudio\installation_${VERSION}.json" /SD IDOK
  ${EndIf}
  DetailPrint "Tunnel Studio ${VERSION} installed. Desktop and Start menu shortcuts updated."
  SetErrorLevel 0
SectionEnd

Function .onInstFailed
  ${If} $Stage != ""
    SetOutPath "$TEMP"
    RMDir /r "$Stage"
  ${EndIf}
FunctionEnd

Function un.onInit
  SetShellVarContext current
  !insertmacro Bootstrap
  !insertmacro CheckClosed
FunctionEnd

Section "Uninstall"
  SetOutPath "$PLUGINSDIR"
  StrCpy $Base "$INSTDIR\.."
  Delete "$PLUGINSDIR\uninstall_error.txt"
  ExecWait '"$PLUGINSDIR\bootstrap\pythonw.exe" "$PLUGINSDIR\windows_installer.py" --uninstall --target "$INSTDIR" --base "$Base" --error-file "$PLUGINSDIR\uninstall_error.txt"' $0
  ${If} $0 != 0
  ${AndIf} $0 != 10
    StrCpy $2 "Tunnel Studio could not finish removing its program files."
    ClearErrors
    FileOpen $1 "$PLUGINSDIR\uninstall_error.txt" r
    ${IfNot} ${Errors}
      FileRead $1 $2
      FileClose $1
    ${EndIf}
    MessageBox MB_OK|MB_ICONSTOP "$2$\r$\n$\r$\nYour survey data has been kept. Details: %LOCALAPPDATA%\TunnelStudio\uninstall_${VERSION}_error.txt" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${If} $0 == 0
    Delete "$DESKTOP\Tunnel Studio.lnk"
    Delete "$SMPROGRAMS\Tunnel Studio.lnk"
  ${EndIf}
  RMDir "$INSTDIR"
  DetailPrint "Confirmed program files removed. Survey projects and protected or modified files have been kept."
  SetErrorLevel 0
SectionEnd
