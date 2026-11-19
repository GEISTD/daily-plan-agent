# 每日计划生成（登录时 / 08:30 由任务计划调用）
# 补确认弹窗（有遗留时） → 生成 → Toast → 推送新计划到 GitHub dailyPlan 仓库
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

$mtx = New-Object System.Threading.Mutex($false, "Local\AIPlanDailyRun")
if (-not $mtx.WaitOne(5000)) { exit }

$stamp = Get-Date -Format "yyyy-MM-dd"
if (-not (Test-Path (Join-Path $dir "logs"))) { New-Item -ItemType Directory -Path (Join-Path $dir "logs") | Out-Null }
$log = Join-Path $dir "logs\$stamp.log"

function Write-Log([string]$Text) {
    if ($Text) {
        Add-Content -LiteralPath $log -Value $Text -Encoding UTF8
    }
}

try {
    # 0) 有未确认的遗留计划时：不再弹模态对话框（会抢焦点、打断工作），
    #    改为静默沿用同一题 + Toast 提醒。想归档旧计划就双击桌面「✅完成今日计划」。
    $statusOut = & $py -X utf8 (Join-Path $dir "gen_daily.py") --status 2>&1
    $carry = ($statusOut | Select-String "^CARRY:(.+)").Matches.Groups[1].Value
    $todayEntry = ($statusOut | Select-String "^TODAY_ENTRY:(\w+)").Matches.Groups[1].Value
    $hasCarry = $carry -and $carry -ne "none" -and $todayEntry -eq "no"
    if ($hasCarry) {
        Write-Log "carry over pending: $carry (silent, no popup)"
    }

    # 1) 生成计划与练习文件（幂等：同一天已生成返回 already_exists，已确认返回 today_done）
    $genOut = & $py -X utf8 (Join-Path $dir "gen_daily.py") 2>&1 | Out-String
    Write-Log $genOut.Trim()
    $runResult = ""
    try {
        $rr = [IO.File]::ReadAllText((Join-Path $dir "last_run.json"), [Text.Encoding]::UTF8) | ConvertFrom-Json
        $runResult = [string]$rr.result
    } catch { Write-Log "last_run read failed: $_" }

    # 2) Toast 通知（仅在今日首次新生成 "ok" 时弹窗，避免重复触发多次打扰）
    if ($runResult -eq "ok") {
        try {
            [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
            $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $sumFile = Join-Path $dir "last_summary.txt"
            $t = $tpl.GetElementsByTagName("text")
            if (Test-Path $sumFile) {
                $lines = Get-Content $sumFile -Encoding UTF8
                $t.Item(0).AppendChild($tpl.CreateTextNode($lines[0])) | Out-Null
                if ($lines.Count -gt 1) { $t.Item(1).AppendChild($tpl.CreateTextNode($lines[1])) | Out-Null }
            }
            if ($hasCarry) {
                # 有遗留未确认：在摘要里补一句，引导用户去点确认，而不是弹窗打断
                $t.Item(1).AppendChild($tpl.CreateTextNode("（有未确认的旧计划：$carry，完成后双击「✅完成今日计划」归档）")) | Out-Null
            }
            $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Daily Plan").Show($toast)
        } catch {
            Write-Log "toast failed: $_"
        }
    }

    # 3) 推送新计划到 GitHub dailyPlan 仓库（含未推送提交自动补推）
    try {
        if (Test-Path (Join-Path $repo ".git")) {
            Push-Location $repo
            $dirty = git status --porcelain
            if ($dirty) {
                git add -A
                $cMsg = git commit -m "plan: $stamp" 2>&1 | Out-String
                Write-Log $cMsg.Trim()
            }
            $unpushed = 0
            try { $unpushed = [int](git rev-list --count origin/main..HEAD 2>$null) } catch { $unpushed = 0 }
            if ($dirty -or $unpushed -gt 0) {
                $pMsg = git push origin main 2>&1 | Out-String
                Write-Log $pMsg.Trim()
            }
            Pop-Location
        }
    } catch {
        Write-Log "git failed: $_"
        try { Pop-Location } catch {}
    }

    # 4) 同步推送 Obsidian 库（计划/练习/笔记镜像都在里面）
    if ($vault) {
        try {
            if (Test-Path (Join-Path $vault ".git")) {
                Push-Location $vault
                $vDirty = git status --porcelain
                if ($vDirty) {
                    git add -A
                    $vMsg = git commit -m "daily: sync plan $stamp" 2>&1 | Out-String
                    Write-Log $vMsg.Trim()
                }
                $vUnpushed = 0
                try { $vUnpushed = [int](git rev-list --count origin/main..HEAD 2>$null) } catch { $vUnpushed = 0 }
                if ($vDirty -or $vUnpushed -gt 0) {
                    $vPush = git push origin main 2>&1 | Out-String
                    Write-Log $vPush.Trim()
                }
                Pop-Location
            }
        } catch {
           Write-Log "obsidian git failed: $_"
            try { Pop-Location } catch {}
        }
    }
}
finally {
    $mtx.ReleaseMutex() | Out-Null
    $mtx.Dispose()
}
