# 确认完成今日计划：归档 → 推送 GitHub dailyPlan → 删除桌面文件 → 推进题号
# 由桌面「✅完成今日计划.vbs」静默调用，结果通过 Toast 反馈
$ErrorActionPreference = "Continue"
$dir = $PSScriptRoot
# ↓ 按需修改：你的 python.exe 路径（找不到时自动回退到 py 启动器）
$py = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
if (-not (Test-Path $py)) { $py = "py" }
# dailyPlan 归档仓库：默认取与本目录平级的 dailyPlan/
$repo = Join-Path (Split-Path $PSScriptRoot -Parent) "dailyPlan"
$stamp = Get-Date -Format "yyyy-MM-dd"
if (-not (Test-Path (Join-Path $dir "logs"))) { New-Item -ItemType Directory -Path (Join-Path $dir "logs") | Out-Null }
$log = Join-Path $dir "logs\$stamp.log"

# 1) Python 结算：pending → done、移动 plans/ → done/、删桌面文件、推进题号与连续打卡
$out = & $py -X utf8 (Join-Path $dir "gen_daily.py") --confirm 2>> $log
$outText = ($out | Out-String).Trim()

$l1 = ""; $l2 = ""
if ($outText -match "NOTHING_PENDING") {
    $l1 = "没有待确认的计划"
    $l2 = "完成今天的计划后再点确认哦"
} elseif ($outText -match "CONFIRMED:(?<dates>[\d,\-]+)") {
    $dates = $Matches["dates"] -split ","
    $n = $dates.Count
    $streak = if ($outText -match "STREAK:(\d+)") { $Matches[1] } else { "?" }
    $l1 = "已确认完成，$n 份计划已归档"
    # 2) 提交并推送归档（失败只记日志，下次自动补推）
    $pushed = $false
    try {
        if (Test-Path (Join-Path $repo ".git")) {
            Push-Location $repo
            git add -A
            git commit -m "done: $($dates -join ' ')" *>> $log
            git push origin main *>> $log
            $pushed = ($LASTEXITCODE -eq 0)
            Pop-Location
        }
    } catch {
        Add-Content $log "git failed: $_"
        try { Pop-Location } catch {}
    }
    if ($pushed) { $l2 = "已推送 GitHub dailyPlan ｜ 连续打卡 $streak 天" }
    else { $l2 = "本地已归档（推送失败，下次自动补推）｜ 连续打卡 $streak 天" }
} else {
    $l1 = "确认失败，详见日志"
    $l2 = "logs\$stamp.log"
}

# 3) Toast 反馈
try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
    $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $t = $tpl.GetElementsByTagName("text")
    $t.Item(0).AppendChild($tpl.CreateTextNode($l1)) | Out-Null
    $t.Item(1).AppendChild($tpl.CreateTextNode($l2)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Daily Plan").Show($toast)
} catch {
    Add-Content $log "toast failed: $_"
}
