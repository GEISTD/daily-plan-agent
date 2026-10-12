# 每日计划生成（登录时 / 08:30 由任务计划调用）
# 生成 → Toast → 推送新计划到 GitHub dailyPlan 仓库
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

# 1) 生成计划与练习文件（幂等：同一天重复运行不重复出题）
& $py -X utf8 (Join-Path $dir "gen_daily.py") *>> $log

# 2) Toast 通知（原生 WinRT，无需安装模块；失败则静默跳过）
try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
    $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $sumFile = Join-Path $dir "last_summary.txt"
    if (Test-Path $sumFile) {
        $lines = Get-Content $sumFile -Encoding UTF8
        $t = $tpl.GetElementsByTagName("text")
        $t.Item(0).AppendChild($tpl.CreateTextNode($lines[0])) | Out-Null
        if ($lines.Count -gt 1) { $t.Item(1).AppendChild($tpl.CreateTextNode($lines[1])) | Out-Null }
    }
    $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Daily Plan").Show($toast)
} catch {
    Add-Content $log "toast failed: $_"
}

# 3) 推送新计划到 GitHub dailyPlan 仓库（失败只记日志，下次自动补推）
try {
    if (Test-Path (Join-Path $repo ".git")) {
        Push-Location $repo
        $dirty = git status --porcelain
        if ($dirty) {
            git add -A
            git commit -m "plan: $stamp" *>> $log
            git push origin main *>> $log
        }
        Pop-Location
    }
} catch {
    Add-Content $log "git failed: $_"
    try { Pop-Location } catch {}
}
