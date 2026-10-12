# 安装每日任务计划（普通用户权限即可，无需管理员）
# 用法：右键“使用 PowerShell 运行”，或在 PowerShell 中执行：
#   powershell -ExecutionPolicy Bypass -File install_task.ps1
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot

$runPs = Join-Path $dir "run_daily.ps1"
$evePs = Join-Path $dir "remind_evening.ps1"
$tr1 = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runPs`""
$tr2 = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$evePs`""

# 登录时生成（开机即推送，弥补电脑 8:30 没开导致的漏推）
schtasks /Create /TN "AIPlan_OnLogon" /TR $tr1 /SC ONLOGON /RL LIMITED /F
# 每天 08:30 生成（幂等：已生成则只覆盖桌面文件）
schtasks /Create /TN "AIPlan_Morning" /TR $tr1 /SC DAILY /ST 08:30 /RL LIMITED /F
# 每天 23:00 晚间提醒（今天完成了吗？→ 引导确认归档）
schtasks /Create /TN "AIPlan_Evening" /TR $tr2 /SC DAILY /ST 23:00 /RL LIMITED /F

Write-Host ""
Write-Host "==== 安装完成 ===="
Write-Host "已创建 3 个任务：AIPlan_OnLogon / AIPlan_Morning(08:30) / AIPlan_Evening(23:00)"
Write-Host "立即试跑一次： powershell -ExecutionPolicy Bypass -File `"$runPs`""
Write-Host "卸载命令： schtasks /Delete /TN AIPlan_OnLogon /F; schtasks /Delete /TN AIPlan_Morning /F; schtasks /Delete /TN AIPlan_Evening /F"
