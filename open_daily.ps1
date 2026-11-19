# 一键开启今日学习工作台（由桌面「🚀开始今日学习.vbs」调用）
# 若今日未生成先生成 → VS Code 同时打开计划/练习/刷题笔记 → 浏览器打开题目页
# 本身通过 silent_runner.vbs 以 SW_HIDE 启动，全程不出现控制台窗口
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$dir = $PSScriptRoot
$py = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
if (-not (Test-Path $py)) { $py = "py" }

$stamp = Get-Date -Format "yyyy-MM-dd"
if (-not (Test-Path (Join-Path $dir "logs"))) { New-Item -ItemType Directory -Path (Join-Path $dir "logs") | Out-Null }
$log = Join-Path $dir "logs\$stamp.log"

$out = & $py -X utf8 (Join-Path $dir "gen_daily.py") --open 2>&1 | Out-String
if ($out.Trim()) {
    Add-Content -LiteralPath $log -Value $out.Trim() -Encoding UTF8
}

$qTitle = "今日计划与练习"
try {
    $rr = [IO.File]::ReadAllText((Join-Path $dir "last_run.json"), [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($rr.q) { $qTitle = $rr.q }
} catch {}

try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
    $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $t = $tpl.GetElementsByTagName("text")
    $t.Item(0).AppendChild($tpl.CreateTextNode("🚀 今日学习工作台已开启")) | Out-Null
    $t.Item(1).AppendChild($tpl.CreateTextNode("$qTitle ｜ VS Code（计划+练习+笔记）与网页已就绪")) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Daily Plan").Show($toast)
} catch {}
