!include "LogicLib.nsh"
!include "nsDialogs.nsh"

!define GW_DATA_DIR_DEFAULT_NAME "Gods Workbench data"
!define GW_BOOTSTRAP_DIR_NAME "GodsWorkbench"
!define GW_UPGRADE_ROOT "$LOCALAPPDATA\ai.godsworkbench.desktop\upgrade"
!define GW_BACKUP_PROGRAM "${GW_UPGRADE_ROOT}\backup-last\program"
!define GW_UPGRADE_REL_DIR "ai.godsworkbench.desktop\upgrade"
!define GW_ROBOCOPY "$SYSDIR\robocopy.exe"

Var GwUserLocalAppData
Var GwUserAppData
Var GwDataDir
Var GwInstallAction
Var GwHasExistingData
Var GwResolvedReady
Var GwDataDialog
Var GwDataInput
Var GwDataBrowseBtn
Var GwDataHintLabel

!macro GW_RESOLVE_PATHS
  ${If} $GwResolvedReady != "1"
    ; 解析原本登录的普通用户 LOCALAPPDATA 与 APPDATA，避免异用户 UAC 提权写入管理员 profile。
    nsExec::ExecToStack `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$$ErrorActionPreference='Stop'; $$sid=$null; $$exp=Get-Process -Name explorer -ErrorAction SilentlyContinue | Select-Object -First 1; if($$exp){ try{ $$owner=(Get-CimInstance Win32_Process -Filter ('ProcessId='+$$exp.Id) | Invoke-CimMethod -MethodName GetOwnerSid); if($$owner.ReturnValue -eq 0){ $$sid=$$owner.Sid } }catch{} }; if(-not $$sid){ $$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value }; if($$sid -match '^S-1-5-(18|19|20)$$'){ exit 2 }; $$sf='Registry::HKEY_USERS\'+$$sid+'\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'; $$la=$null; $$ra=$null; if(Test-Path $$sf){ $$p=Get-ItemProperty $$sf; $$la=$$p.'Local AppData'; $$ra=$$p.AppData }; if(-not $$la){ $$la=$env:LOCALAPPDATA }; if(-not $$ra){ $$ra=$env:APPDATA }; if(-not $$la -or -not $$ra){ exit 3 }; Write-Output ('OK|'+$$la+'|'+$$ra)"`
    Pop $0
    Pop $1
    ${If} $0 != 0
      MessageBox MB_ICONSTOP "无法安全解析原本登录用户的本地配置目录（错误码 $0），安装已中止以避免写入错误账户。" /SD IDOK
      Abort
    ${EndIf}
    ; 剥离末尾换行并拆分 OK|<LocalAppData>|<AppData>
    nsExec::ExecToStack `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "$$s='$1'.Trim(); $$p=$$s.Split('|'); if($$p.Length -lt 3 -or $$p[0] -ne 'OK'){ exit 4 }; Write-Output $$p[1]"`
    Pop $0
    Pop $GwUserLocalAppData
    ${If} $0 != 0
      MessageBox MB_ICONSTOP "无法读取原本登录用户的 LOCALAPPDATA，安装已中止。" /SD IDOK
      Abort
    ${EndIf}
    nsExec::ExecToStack `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "$$s='$1'.Trim(); $$p=$$s.Split('|'); Write-Output $$p[2]"`
    Pop $0
    Pop $GwUserAppData

    ; 去掉可能残留的 CR/LF
    StrCpy $GwUserLocalAppData $GwUserLocalAppData "" 0
    StrCpy $GwUserAppData $GwUserAppData "" 0
    StrCpy $GwResolvedReady "1"
  ${EndIf}

  StrCpy $R6 "$GwUserLocalAppData"
  StrCpy $R7 "$R6\${GW_BOOTSTRAP_DIR_NAME}"
  StrCpy $R8 "$R6\${GW_UPGRADE_REL_DIR}"
  StrCpy $R9 "$GwUserAppData\ai.godsworkbench.desktop\client.json"

  ${If} $GwDataDir == ""
    ; 探测是否已存在 627 引导指针或旧默认 GodsWorkbench 生产数据
    nsExec::ExecToStack `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "$$b=Join-Path '$R6' '${GW_BOOTSTRAP_DIR_NAME}'; $$ptr=Join-Path $$b 'runtime-root.json'; if(Test-Path -LiteralPath $$ptr){ try{ $$j=Get-Content -LiteralPath $$ptr -Raw -Encoding UTF8 | ConvertFrom-Json; if($$j.data_location){ Write-Output ('EXISTING|'+$$j.data_location); exit 0 } }catch{} }; $$has=$false; if(Test-Path -LiteralPath (Join-Path $$b 'auth\auth.sqlite3')){ $$has=$true }; foreach($$sub in 'data','video'){ $$p=Join-Path $$b $$sub; if((Test-Path -LiteralPath $$p) -and @(Get-ChildItem -LiteralPath $$p -Force -ErrorAction SilentlyContinue).Count -gt 0){ $$has=$true } }; if($$has){ Write-Output ('EXISTING|'+$$b) }else{ Write-Output ('FRESH|'+(Join-Path '$R6' '${GW_DATA_DIR_DEFAULT_NAME}')) }"`
    Pop $0
    Pop $1
    ${If} $0 == 0
      StrCpy $R0 "$1" 8
      ${If} $R0 == "EXISTING"
        StrCpy $GwHasExistingData "1"
        StrCpy $GwInstallAction "UpgradeKeep"
        StrCpy $GwDataDir "$1" "" 9
      ${Else}
        StrCpy $GwHasExistingData "0"
        StrCpy $GwInstallAction "FreshInstall"
        StrCpy $GwDataDir "$R6\${GW_DATA_DIR_DEFAULT_NAME}"
      ${EndIf}
    ${Else}
      StrCpy $GwHasExistingData "0"
      StrCpy $GwInstallAction "FreshInstall"
      StrCpy $GwDataDir "$R6\${GW_DATA_DIR_DEFAULT_NAME}"
    ${EndIf}
  ${EndIf}
!macroend

!macro GW_WRITE_STATE state
  !insertmacro GW_RESOLVE_PATHS
  CreateDirectory "$R8"
  FileOpen $9 "$R8\journal.txt" w
  FileWrite $9 "${state}"
  FileClose $9
!macroend

!macro GW_ASSERT_NON_CONTAINMENT
  !insertmacro GW_RESOLVE_PATHS
  nsExec::ExecToStack `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "$$ErrorActionPreference='Stop'; function Norm([string]$$p){ return [IO.Path]::GetFullPath($$p).TrimEnd('\','/').ToLowerInvariant() }; $$a=Norm '$INSTDIR'; $$b=Norm '$GwDataDir'; $$c=Norm '$R7'; if($$a -eq $$b -or $$a.StartsWith($$b+'\') -or $$b.StartsWith($$a+'\') -or $$a -eq $$c -or $$a.StartsWith($$c+'\') -or $$c.StartsWith($$a+'\')){ exit 1 }"`
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "软件安装目录与用户数据目录不能相同或互相包含。请选择独立目录后重试。" /SD IDOK
    Abort
  ${EndIf}
!macroend

Function GwDataDirBrowse
  ${If} $GwHasExistingData == "1"
    Return
  ${EndIf}
  ${NSD_GetText} $GwDataInput $0
  nsDialogs::SelectFolderDialog "选择神工坊用户数据目录" "$0"
  Pop $0
  ${If} $0 != "error"
    ${NSD_SetText} $GwDataInput "$0"
  ${EndIf}
FunctionEnd

Function GwDataDirPageCreate
  !insertmacro GW_RESOLVE_PATHS
  nsDialogs::Create 1018
  Pop $GwDataDialog
  ${If} $GwDataDialog == error
    Abort
  ${EndIf}

  ${If} $GwHasExistingData == "1"
    ${NSD_CreateLabel} 0 0 100% 28u "已检测到既有用户数据或数据位置指针。升级/重装将继续使用以下数据目录（如需迁移请在安装后于设置中操作）："
    Pop $GwDataHintLabel
    ${NSD_CreateDirRequest} 0 34u 80% 14u "$GwDataDir"
    Pop $GwDataInput
    EnableWindow $GwDataInput 0
    ${NSD_CreateBrowseButton} 82% 33u 18% 16u "浏览..."
    Pop $GwDataBrowseBtn
    EnableWindow $GwDataBrowseBtn 0
  ${Else}
    ${NSD_CreateLabel} 0 0 100% 28u "请选择神工坊用户数据目录（工作区、索引与缓存将保存在此处，必须与软件安装目录互不包含）："
    Pop $GwDataHintLabel
    ${NSD_CreateDirRequest} 0 34u 80% 14u "$GwDataDir"
    Pop $GwDataInput
    ${NSD_CreateBrowseButton} 82% 33u 18% 16u "浏览..."
    Pop $GwDataBrowseBtn
    ${NSD_OnClick} $GwDataBrowseBtn GwDataDirBrowse
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function GwDataDirPageLeave
  ${If} $GwHasExistingData != "1"
    ${NSD_GetText} $GwDataInput $GwDataDir
  ${EndIf}
  ${If} $GwDataDir == ""
    MessageBox MB_ICONSTOP "用户数据目录不能为空。" /SD IDOK
    Abort
  ${EndIf}
  !insertmacro GW_ASSERT_NON_CONTAINMENT
FunctionEnd

Page custom GwDataDirPageCreate GwDataDirPageLeave

!macro GW_RESTORE_BACKUP reason
  !insertmacro GW_RESOLVE_PATHS
  ${IfNot} ${FileExists} "$R8\backup-last\program\${MAINBINARYNAME}.exe"
    !insertmacro GW_WRITE_STATE "rolled_back"
    MessageBox MB_ICONSTOP "${reason}；未找到可还原的上一版本，请重新安装或联系支持。" /SD IDOK
    Abort
  ${EndIf}
  nsExec::ExecToLog '"${GW_ROBOCOPY}" "$R8\backup-last\program" "$INSTDIR" /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP'
  Pop $0
  ${If} $0 > 7
    MessageBox MB_ICONSTOP "${reason}；自动还原失败，请使用上一版本安装包重新安装。" /SD IDOK
    Abort
  ${EndIf}
  ${If} ${FileExists} "$R8\backup-last\client.json"
    CopyFiles /SILENT "$R8\backup-last\client.json" "$R9"
  ${EndIf}
  !insertmacro GW_WRITE_STATE "rolled_back"
  MessageBox MB_ICONSTOP "${reason}；已还原上一版本程序。用户数据未被修改。" /SD IDOK
  Abort
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro GW_RESOLVE_PATHS
  CreateDirectory "$R8"
  !insertmacro GW_ASSERT_NON_CONTAINMENT

  StrCpy $R3 "clean"
  ${If} ${FileExists} "$R8\journal.txt"
    FileOpen $0 "$R8\journal.txt" r
    FileRead $0 $R3
    FileClose $0
  ${EndIf}
  ; verified / committed 表示上一代安装已通过自检，不得还原更早的 backup-last。
  ${If} $R3 == "installing"
  ${OrIf} $R3 == "installed"
    ${IfNot} ${FileExists} "$R8\backup-last\program\${MAINBINARYNAME}.exe"
      !insertmacro GW_WRITE_STATE "rolled_back"
      MessageBox MB_ICONSTOP "上次升级未完成，且未找到可还原的上一版本。请重新安装或联系支持。" /SD IDOK
      Abort
    ${EndIf}
    nsExec::ExecToLog '"${GW_ROBOCOPY}" "$R8\backup-last\program" "$INSTDIR" /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP'
    Pop $0
    ${If} $0 > 7
      MessageBox MB_ICONSTOP "上次升级未完成，自动还原上一版本失败。升级备份已保留，请检查磁盘空间与文件权限后重试。" /SD IDOK
      Abort
    ${EndIf}
    ${If} ${FileExists} "$R8\backup-last\client.json"
      CopyFiles /SILENT "$R8\backup-last\client.json" "$R9"
    ${EndIf}
    !insertmacro GW_WRITE_STATE "rolled_back"
  ${EndIf}

  !insertmacro GW_WRITE_STATE "prepare"
  ${If} ${FileExists} "$INSTDIR\${MAINBINARYNAME}.exe"
    RMDir /r "$R8\backup-last"
    CreateDirectory "$R8\backup-last\program"
    nsExec::ExecToLog '"${GW_ROBOCOPY}" "$INSTDIR" "$R8\backup-last\program" /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP'
    Pop $0
    ${If} $0 > 7
      !insertmacro GW_WRITE_STATE "aborted"
      MessageBox MB_ICONSTOP "无法备份现有安装，升级已中止。请确认磁盘空间与文件权限后重试。" /SD IDOK
      Abort
    ${EndIf}
    ${If} ${FileExists} "$R9"
      CopyFiles /SILENT "$R9" "$R8\backup-last\client.json"
    ${EndIf}
  ${EndIf}
  !insertmacro GW_WRITE_STATE "backed_up"
  !insertmacro GW_WRITE_STATE "installing"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  !insertmacro GW_WRITE_STATE "installed"
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\packaging\upgrade.ps1" -Mode PostInstall -UserLocalAppData "$GwUserLocalAppData" -UserAppData "$GwUserAppData" -InstallDir "$INSTDIR" -SelectedDataDir "$GwDataDir" -InstallAction "$GwInstallAction"'
  Pop $0
  ${If} $0 != 0
    !insertmacro GW_RESTORE_BACKUP "安装后自检失败"
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; 只删除程序与快捷方式；数据根、设备密钥与升级备份必须保留。
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; 不触碰用户数据目录、引导指针 runtime-root.json 与升级备份。
!macroend
