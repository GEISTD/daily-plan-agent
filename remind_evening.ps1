# 23:00 晚间提醒：今天完成了吗？（由任务计划 AIPlan_Evening 调用）
$ErrorActionPreference = "Continue"
try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
    $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $t = $tpl.GetElementsByTagName("text")
    $t.Item(0).AppendChild($tpl.CreateTextNode("今日计划完成了吗？")) | Out-Null
    $t.Item(1).AppendChild($tpl.CreateTextNode("完成 → 双击桌面「✅完成今日计划」归档；未完成 → 文件保留，明天继续同一题")) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Daily Plan").Show($toast)
} catch { }
