!include "LogicLib.nsh"

!define GW_DATA_ROOT "$LOCALAPPDATA\GodsWorkbench"
!define GW_UPGRADE_ROOT "$LOCALAPPDATA\ai.godsworkbench.desktop\upgrade"
!define GW_BACKUP_PROGRAM "${GW_UPGRADE_ROOT}\backup-last\program"
!define GW_BACKUP_CLIENT "${GW_UPGRADE_ROOT}\backup-last\client.json"
!define GW_JOURNAL "${GW_UPGRADE_ROOT}\journal.txt"
!define GW_CLIENT_CONFIG "$APPDATA\ai.godsworkbench.desktop\client.json"
!define GW_ROBOCOPY "$SYSDIR\robocopy.exe"

!macro GW_RESOLVE_PATHS
  ReadEnvStr $R6 "LOCALAPPDATA"
  ${If} $R6 == ""
    StrCpy $R6 "$LOCALAPPDATA"
  ${EndIf}
  StrCpy $R7 "$R6\GodsWorkbench"
  StrCpy $R8 "$R6\ai.godsworkbench.desktop\upgrade"
  ReadEnvStr $R9 "APPDATA"
  ${If} $R9 == ""
    StrCpy $R9 "$APPDATA"
  ${EndIf}
  StrCpy $R9 "$R9\ai.godsworkbench.desktop\client.json"
!macroend

!macro GW_WRITE_STATE state
  !insertmacro GW_RESOLVE_PATHS
  CreateDirectory "$R8"
  FileOpen $9 "$R8\journal.txt" w
  FileWrite $9 "${state}"
  FileClose $9
!macroend

!macro GW_PREFIX_MATCH out prefix text
  StrLen $R0 "${prefix}"
  StrCpy $R1 "${text}" $R0
  StrCpy ${out} "0"
  ${If} $R1 == "${prefix}"
    StrCpy ${out} "1"
  ${EndIf}
!macroend

!macro GW_ASSERT_NON_CONTAINMENT
  !insertmacro GW_RESOLVE_PATHS
  ${If} $INSTDIR == $R7
    MessageBox MB_ICONSTOP "安装目录不能与神工坊数据根相同。请选择独立目录后重试。" /SD IDOK
    Abort
  ${EndIf}
  !insertmacro GW_PREFIX_MATCH $R2 "$R7\" "$INSTDIR"
  ${If} $R2 == "1"
    MessageBox MB_ICONSTOP "安装目录不能位于神工坊数据根内。请选择独立目录后重试。" /SD IDOK
    Abort
  ${EndIf}
  !insertmacro GW_PREFIX_MATCH $R2 "$INSTDIR\" "$R7"
  ${If} $R2 == "1"
    MessageBox MB_ICONSTOP "数据根不能位于安装目录内。请选择独立安装目录后重试。" /SD IDOK
    Abort
  ${EndIf}
!macroend

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
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\packaging\upgrade.ps1" -Mode PostInstall'
  Pop $0
  ${If} $0 != 0
    !insertmacro GW_RESTORE_BACKUP "安装后自检失败"
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; 只删除程序与快捷方式；数据根、设备密钥与升级备份必须保留。
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; 不触碰 ${GW_DATA_ROOT} 与 ${GW_UPGRADE_ROOT}。
!macroend
