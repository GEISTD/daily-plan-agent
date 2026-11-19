# 一键收尾：同步 Obsidian 镜像 → 提交并推送所有相关仓库
# 用法：powershell -ExecutionPolicy Bypass -File sync_all.ps1 [-Message "备注"]
# 适用场景：手工改过计划/笔记，或新会话说"重新生成今天的计划"之后的收尾
param(
    [string]$Message = ""
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$dir = $PSScriptRoot
$py = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
if (-not (Test-Path $py)) { $py = "py" }

$stamp = Get-Date -Format "yyyy-MM-dd"
if (-not (Test-Path (Join-Path $dir "logs"))) { New-Item -ItemType Directory -Path (Join-Path $dir "logs") | Out-Null }
$log = Join-Path $dir "logs\$stamp.log"

function Write-Log([string]$Text) {
    if ($Text) { Add-Content -LiteralPath $log -Value $Text -Encoding UTF8 }
}

# 0) 定位工作区（认准含 .git 的 dailyPlan）
$root = $null
foreach ($p in @((Split-Path $dir -Parent), (Split-Path (Split-Path $dir -Parent) -Parent))) {
    if ($p -and (Test-Path (Join-Path $p "dailyPlan\.git"))) { $root = $p; break }
}
if (-not $root) { $root = Split-Path (Split-Path $dir -Parent) -Parent }

$repos = @(
    @{ Path = (Join-Path $root "dailyPlan");         Msg = "plan: $stamp" },
    @{ Path = (Join-Path $root "learning-notes");    Msg = "feat(daily): sync $stamp" },
    @{ Path = (Join-Path $root "obsidian-notes");    Msg = "daily: sync plan $stamp" },
    @{ Path = (Join-Path $root "daily-plan-agent");  Msg = "sync: $stamp" }
)

# 1) 先同步 Obsidian 镜像（计划 / 练习 / 刷题笔记 / IELTS）
$syncOut = & $py -X utf8 (Join-Path $dir "gen_daily.py") --sync-obsidian 2>&1 | Out-String
Write-Log $syncOut.Trim()
Write-Output $syncOut.Trim()

# 2) 逐个仓库提交并推送（只处理有改动 / 有未推送提交的）
$result = @()
foreach ($r in $repos) {
    $path = $r.Path
    if (-not (Test-Path (Join-Path $path ".git"))) {
        $result += "跳过（无 .git）: $path"
        continue
    }
    $msg = if ($Message) { $Message } else { $r.Msg }
    Push-Location $path
    try {
        $dirty = git status --porcelain
        if ($dirty) {
            git add -A
            git commit -m $msg 2>&1 | ForEach-Object { Write-Log $_ }
        }
        # 以「未推送提交数」为准判断是否真的产生了新提交（换行符规范化时 status 会假 dirty）
        $unpushed = 0
        try { $unpushed = [int](git rev-list --count origin/main..HEAD 2>$null) } catch { $unpushed = 0 }
        if ($unpushed -gt 0) {
            git push origin main 2>&1 | ForEach-Object { Write-Log $_ }
            $result += "已推送 $(Split-Path $path -Leaf)（$unpushed 个提交）"
        } elseif ($dirty) {
            $result += "无实质改动 $(Split-Path $path -Leaf)（仅换行符差异）"
        } else {
            $result += "无改动 $(Split-Path $path -Leaf)"
        }
    } catch {
        $result += "失败 $(Split-Path $path -Leaf): $_"
        Write-Log "sync failed ($path): $_"
    } finally {
        Pop-Location
    }
}

# 3) 汇总结果 + Toast
$summary = $result -join "`n"
Write-Output $summary
Write-Log $summary
try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
    $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $t = $tpl.GetElementsByTagName("text")
    $t.Item(0).AppendChild($tpl.CreateTextNode("每日计划已同步并推送")) | Out-Null
    $t.Item(1).AppendChild($tpl.CreateTextNode("dailyPlan · learning-notes · obsidian-notes · daily-plan-agent")) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Daily Plan").Show($toast)
} catch {}
