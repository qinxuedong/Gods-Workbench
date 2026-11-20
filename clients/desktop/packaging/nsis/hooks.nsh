!include "LogicLib.nsh"

!define GW_DATA_ROOT "$LOCALAPPDATA\GodsWorkbench"
!define GW_UPGRADE_ROOT "$LOCALAPPDATA\ai.godsworkbench.desktop\upgrade"
!define GW_BACKUP_PROGRAM "${GW_UPGRADE_ROOT}\backup-last\program"
!define GW_BACKUP_CLIENT "${GW_UPGRADE_ROOT}\backup-last\client.json"
!define GW_JOURNAL "${GW_UPGRADE_ROOT}\journal.txt"
!define GW_CLIENT_CONFIG "$APPDATA\ai.godsworkbench.desktop\client.json"
!define GW_ROBOCOPY "$SYSDIR\robocopy.exe"

!macro GW_WRITE_STATE state
  CreateDirectory "${GW_UPGRADE_ROOT}"
  FileOpen $9 "${GW_JOURNAL}" w
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
  ${If} $INSTDIR == "${GW_DATA_ROOT}"
    MessageBox MB_ICONSTOP "安装目录不能与神工坊数据根相同。请选择独立目录后重试。" /SD IDOK
    Abort
  ${EndIf}
  !insertmacro GW_PREFIX_MATCH $R2 "${GW_DATA_ROOT}\" "$INSTDIR"
  ${If} $R2 == "1"
    MessageBox MB_ICONSTOP "安装目录不能位于神工坊数据根内。请选择独立目录后重试。" /SD IDOK
    Abort
  ${EndIf}
  !insertmacro GW_PREFIX_MATCH $R2 "$INSTDIR\" "${GW_DATA_ROOT}"
  ${If} $R2 == "1"
    MessageBox MB_ICONSTOP "数据根不能位于安装目录内。请选择独立安装目录后重试。" /SD IDOK
    Abort
  ${EndIf}
!macroend

!macro GW_RESTORE_BACKUP reason
  ${IfNot} ${FileExists} "${GW_BACKUP_PROGRAM}\${MAINBINARYNAME}.exe"
    !insertmacro GW_WRITE_STATE "rolled_back"
    MessageBox MB_ICONSTOP "${reason}；未找到可还原的上一版本，请重新安装或联系支持。" /SD IDOK
    Abort
  ${EndIf}
  nsExec::ExecToLog '"${GW_ROBOCOPY}" "${GW_BACKUP_PROGRAM}" "$INSTDIR" /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP'
  Pop $0
  ${If} $0 > 7
    MessageBox MB_ICONSTOP "${reason}；自动还原失败，请使用上一版本安装包重新安装。" /SD IDOK
    Abort
  ${EndIf}
  ${If} ${FileExists} "${GW_BACKUP_CLIENT}"
    CopyFiles /SILENT "${GW_BACKUP_CLIENT}" "${GW_CLIENT_CONFIG}"
  ${EndIf}
  !insertmacro GW_WRITE_STATE "rolled_back"
  MessageBox MB_ICONSTOP "${reason}；已还原上一版本程序。用户数据未被修改。" /SD IDOK
  Abort
!macroend

!macro NSIS_HOOK_PREINSTALL
  CreateDirectory "${GW_UPGRADE_ROOT}"
  !insertmacro GW_ASSERT_NON_CONTAINMENT

  StrCpy $R3 "clean"
  ${If} ${FileExists} "${GW_JOURNAL}"
    FileOpen $0 "${GW_JOURNAL}" r
    FileRead $0 $R3
    FileClose $0
  ${EndIf}
  ; verified / committed 表示上一代安装已通过自检，不得还原更早的 backup-last。
  ${If} $R3 == "installing"
  ${OrIf} $R3 == "installed"
    ${IfNot} ${FileExists} "${GW_BACKUP_PROGRAM}\${MAINBINARYNAME}.exe"
      !insertmacro GW_WRITE_STATE "rolled_back"
      MessageBox MB_ICONSTOP "上次升级未完成，且未找到可还原的上一版本。请重新安装或联系支持。" /SD IDOK
      Abort
    ${EndIf}
    nsExec::ExecToLog '"${GW_ROBOCOPY}" "${GW_BACKUP_PROGRAM}" "$INSTDIR" /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP'
    Pop $0
    ${If} $0 > 7
      MessageBox MB_ICONSTOP "上次升级未完成，自动还原上一版本失败。升级备份已保留，请检查磁盘空间与文件权限后重试。" /SD IDOK
      Abort
    ${EndIf}
    ${If} ${FileExists} "${GW_BACKUP_CLIENT}"
      CopyFiles /SILENT "${GW_BACKUP_CLIENT}" "${GW_CLIENT_CONFIG}"
    ${EndIf}
    !insertmacro GW_WRITE_STATE "rolled_back"
  ${EndIf}

  !insertmacro GW_WRITE_STATE "prepare"
  ${If} ${FileExists} "$INSTDIR\${MAINBINARYNAME}.exe"
    RMDir /r "${GW_UPGRADE_ROOT}\backup-last"
    CreateDirectory "${GW_BACKUP_PROGRAM}"
    nsExec::ExecToLog '"${GW_ROBOCOPY}" "$INSTDIR" "${GW_BACKUP_PROGRAM}" /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP'
    Pop $0
    ${If} $0 > 7
      !insertmacro GW_WRITE_STATE "aborted"
      MessageBox MB_ICONSTOP "无法备份现有安装，升级已中止。请确认磁盘空间与文件权限后重试。" /SD IDOK
      Abort
    ${EndIf}
    ${If} ${FileExists} "${GW_CLIENT_CONFIG}"
      CopyFiles /SILENT "${GW_CLIENT_CONFIG}" "${GW_BACKUP_CLIENT}"
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
