# 23:00 晚间提醒与练习自动备份（由任务计划 AIPlan_Evening 调用）
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$dir = $PSScriptRoot
$stamp = Get-Date -Format "yyyy-MM-dd"
if (-not (Test-Path (Join-Path $dir "logs"))) { New-Item -ItemType Directory -Path (Join-Path $dir "logs") | Out-Null }
$log = Join-Path $dir "logs\$stamp.log"

function Write-Log([string]$Text) {
    if ($Text) {
        Add-Content -LiteralPath $log -Value $Text -Encoding UTF8
    }
}

# 1) 检查今日计划是否仍未确认；未确认则弹晚间提醒 Toast
$stillPending = $true
try {
    $stateFile = Join-Path $dir "state.json"
    if (Test-Path $stateFile) {
        $st = [IO.File]::ReadAllText($stateFile, [Text.Encoding]::UTF8) | ConvertFrom-Json
        $todayEntry = $st.history.$stamp
        if ($todayEntry -and $todayEntry.status -eq "done") {
            $stillPending = $false
        }
    }
} catch {}

if ($stillPending) {
    try {
        [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
        $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $t = $tpl.GetElementsByTagName("text")
        $t.Item(0).AppendChild($tpl.CreateTextNode("今日计划完成了吗？")) | Out-Null
        $t.Item(1).AppendChild($tpl.CreateTextNode("完成 → 双击桌面「✅完成今日计划」归档；未完成 → 文件保留，明天继续同一题")) | Out-Null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Daily Plan").Show($toast)
    } catch {
        Write-Log "evening toast failed: $_"
    }
}

# 2) 自动备份 dailyPlan 仓库中当天手写的 practice/ 进度
try {
    $p1 = Split-Path $dir -Parent
    $p2 = Split-Path $p1 -Parent
    $dailyRepo = if (Test-Path (Join-Path $p1 "dailyPlan\.git")) { Join-Path $p1 "dailyPlan" } else { Join-Path $p2 "dailyPlan" }
    if (Test-Path (Join-Path $dailyRepo ".git")) {
        Push-Location $dailyRepo
        $dirty = git status --porcelain -- practice
        if ($dirty) {
            git add practice/
            $cMsg = git commit -m "practice: handwritten progress $stamp" 2>&1 | Out-String
            Write-Log $cMsg.Trim()
            $pMsg = git push origin main 2>&1 | Out-String
            Write-Log $pMsg.Trim()
        }
        Pop-Location
    }
} catch {
    Write-Log "evening git failed: $_"
    try { Pop-Location } catch {}
}
