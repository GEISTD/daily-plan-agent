# 安装每日任务计划（使用 Register-ScheduledTask，普通用户权限即可，无需管理员）
# 解决 schtasks /SC ONLOGON 权限拒绝、电池模式不启动、睡眠唤醒错过 08:30 不补跑的问题
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot

$psExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
$runPs = Join-Path $dir "run_daily.ps1"
$evePs = Join-Path $dir "commit_evening.ps1"

$actRun = New-ScheduledTaskAction -Execute $psExe -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runPs`""
$actEve = New-ScheduledTaskAction -Execute $psExe -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$evePs`""

$userId = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew

# 1) 登录时生成（开机登录即推送）
$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$taskLogon = New-ScheduledTask -Action $actRun -Trigger $trigLogon -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName "AIPlan_OnLogon" -InputObject $taskLogon -Force | Out-Null

# 2) 每天 08:30 生成 + 白天每 2 小时轻量巡检兜底（睡眠唤醒后自动补生成，已生成则 0.05s 静默退出）
$trigMorning = New-ScheduledTaskTrigger -Daily -At "08:30"
$trigRepeat = New-ScheduledTaskTrigger -Once -At "08:30" `
    -RepetitionInterval (New-TimeSpan -Hours 2) `
    -RepetitionDuration (New-TimeSpan -Hours 14)
$trigMorning.Repetition = $trigRepeat.Repetition
$taskMorning = New-ScheduledTask -Action $actRun -Trigger $trigMorning -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName "AIPlan_Morning" -InputObject $taskMorning -Force | Out-Null

# 3) 每天 23:00 晚间提醒（未确认则弹窗提醒 + 自动备份手写练习进度）
$trigEvening = New-ScheduledTaskTrigger -Daily -At "23:00"
$taskEvening = New-ScheduledTask -Action $actEve -Trigger $trigEvening -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName "AIPlan_Evening" -InputObject $taskEvening -Force | Out-Null

Write-Host ""
Write-Host "==== 安装完成 ===="
Write-Host "已注册 3 个任务：AIPlan_OnLogon / AIPlan_Morning(08:30 + 每2h巡检兜底) / AIPlan_Evening(23:00)"
Write-Host "支持：电池模式启动 + 错过时间唤醒自动补跑（StartWhenAvailable）"
