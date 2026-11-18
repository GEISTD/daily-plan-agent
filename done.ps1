# 确认完成今日计划：检测本地练习代码 AC → 归档 → 推送 GitHub dailyPlan → 删除桌面文件 → 推进题号
# 若今天计划尚未生成（如睡眠唤醒漏跑任务），双击自动补生成今日计划
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$dir = $PSScriptRoot
$py = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
if (-not (Test-Path $py)) { $py = "py" }
$p1 = Split-Path $dir -Parent
$p2 = Split-Path $p1 -Parent
$repo = if (Test-Path (Join-Path $p1 "dailyPlan\.git")) { Join-Path $p1 "dailyPlan" } else { Join-Path $p2 "dailyPlan" }
# Obsidian 库：在 dailyPlan 同级找 obsidian-notes/.obsidian
$vault = Join-Path (Split-Path $repo -Parent) "obsidian-notes"
if (-not (Test-Path (Join-Path $vault ".obsidian"))) { $vault = $null }
$stamp = Get-Date -Format "yyyy-MM-dd"
if (-not (Test-Path (Join-Path $dir "logs"))) { New-Item -ItemType Directory -Path (Join-Path $dir "logs") | Out-Null }
$log = Join-Path $dir "logs\$stamp.log"

function Write-Log([string]$Text) {
    if ($Text) {
        Add-Content -LiteralPath $log -Value $Text -Encoding UTF8
    }
}

$rr = $null
$acVerified = $false
$aborted = $false
$mtx = New-Object System.Threading.Mutex($false, "Local\AIPlanDailyRun")
$locked = $false
try {
    $locked = $mtx.WaitOne(15000)
    if (-not $locked) {
        Write-Log "mutex busy"
        $l1 = "生成任务正在运行，请 1 分钟后再点确认"
        $l2 = ""
    } else {
        # 0) 确认前自检当日主练 .py 是否已跑通 AC!
        $chkOut = & $py -X utf8 (Join-Path $dir "gen_daily.py") --check-practice 2>&1 | Out-String
        Write-Log $chkOut.Trim()
        $chk = $null
        try {
            $chk = [IO.File]::ReadAllText((Join-Path $dir "last_run.json"), [Text.Encoding]::UTF8) | ConvertFrom-Json
        } catch {}

        if ($chk -and ($chk.check -eq "ac")) {
            $acVerified = $true
        } elseif ($chk -and ($chk.check -in @("todo", "fail"))) {
            try {
                $ws = New-Object -ComObject WScript.Shell
                $msg = "检测到今日练习代码尚未本地跑通 AC!`n`n题目：$($chk.q)`n文件：$($chk.file)`n状态：$($chk.reason)`n`n[是] 我已在网页端完成 / 仍要强制确认归档`n[否] 先不归档，回去把本地代码跑通 AC!"
                $ans = $ws.Popup($msg, 120, "每日计划 · 练习代码自检", 4 + 48)
                if ($ans -ne 6) {
                    $aborted = $true
                    Write-Log "confirm aborted by user after AC check (ans=$ans)"
                } else {
                    Write-Log "user forced confirm despite check=$($chk.check)"
                }
            } catch {
                Write-Log "check popup failed: $_"
            }
        }

        if (-not $aborted) {
            $cOut = & $py -X utf8 (Join-Path $dir "gen_daily.py") --confirm 2>&1 | Out-String
            Write-Log $cOut.Trim()
        }
    }
}
catch {
    Write-Log "confirm failed: $_"
}
finally {
    if ($locked) { $mtx.ReleaseMutex() | Out-Null }
    $mtx.Dispose()
}

if ($aborted) {
    exit 0
}

if (-not $l1) {
    try {
        $rr = [IO.File]::ReadAllText((Join-Path $dir "last_run.json"), [Text.Encoding]::UTF8) | ConvertFrom-Json
    } catch { Write-Log "last_run read failed: $_" }
}

if ($l1) {
    # 互斥锁忙，直接弹 Toast
} elseif ($null -eq $rr) {
    $l1 = "确认失败，详见日志"
    $l2 = "logs\$stamp.log"
} elseif ($rr.result -eq "generated_today") {
    $l1 = "今日计划原本未生成，已为你自动补生成！"
    try {
        if (Test-Path (Join-Path $repo ".git")) {
            Push-Location $repo
            $dirty = git status --porcelain
            if ($dirty) {
                git add -A
                $cMsg = git commit -m "plan: $stamp" 2>&1 | Out-String
                Write-Log $cMsg.Trim()
                $pMsg = git push origin main 2>&1 | Out-String
                Write-Log $pMsg.Trim()
            }
            Pop-Location
        }
    } catch {
        Write-Log "git failed: $_"
        try { Pop-Location } catch {}
    }
    $l2 = "桌面《学习计划_$stamp.md》已就绪，完成后再双击归档"
} elseif ($rr.result -eq "nothing_pending") {
    $l1 = "今日计划已确认归档"
    $l2 = "今天没有待确认的计划，明天继续加油！"
} elseif ($rr.result -eq "confirmed") {
    $dates = @($rr.dates)
    $n = $dates.Count
    $streak = $rr.streak
    $acTag = if ($acVerified) { "✅ 本地 AC 已验证 ｜ " } else { "" }
    $l1 = "已确认完成，$n 份计划已归档（刷题笔记已标 AC）"
    $pushed = $false
    try {
        if (Test-Path (Join-Path $repo ".git")) {
            Push-Location $repo
            git add -A
            $cMsg = git commit -m "done: $($dates -join ' ')" 2>&1 | Out-String
            Write-Log $cMsg.Trim()
            $pMsg = git push origin main 2>&1 | Out-String
            Write-Log $pMsg.Trim()
            $pushed = ($LASTEXITCODE -eq 0)
            Pop-Location
        }
    } catch {
        Write-Log "git failed: $_"
        try { Pop-Location } catch {}
    }
    if ($pushed) { $l2 = "${acTag}已推送 GitHub dailyPlan ｜ 连续打卡 $streak 天" }
    else { $l2 = "${acTag}本地已归档（推送失败，下次自动补推）｜ 连续打卡 $streak 天" }

    # 同步推送 Obsidian 库（归档计划、AC 笔记与代码回写都在里面）
    if ($vault) {
        try {
            if (Test-Path (Join-Path $vault ".git")) {
                Push-Location $vault
                git add -A
                $vMsg = git commit -m "daily: archive $($dates -join ' ')" 2>&1 | Out-String
                Write-Log $vMsg.Trim()
                $vPush = git push origin main 2>&1 | Out-String
                Write-Log $vPush.Trim()
                Pop-Location
            }
        } catch {
            Write-Log "obsidian git failed: $_"
            try { Pop-Location } catch {}
        }
    }
} else {
    $l1 = "确认失败，详见日志"
    $l2 = "logs\$stamp.log"
}

try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
    $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $t = $tpl.GetElementsByTagName("text")
    $t.Item(0).AppendChild($tpl.CreateTextNode($l1)) | Out-Null
    if ($l2) { $t.Item(1).AppendChild($tpl.CreateTextNode($l2)) | Out-Null }
    $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Daily Plan").Show($toast)
} catch {
    Write-Log "toast failed: $_"
}
