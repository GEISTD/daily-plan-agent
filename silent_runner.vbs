' 静默运行一个 PowerShell 脚本，全程不出现任何控制台窗口
' 用法: wscript.exe silent_runner.vbs "C:\path\to\script.ps1"
'
' 为什么要多这一层：计划任务直接调用 powershell.exe 时，Windows 会先创建控制台窗口、
' 再由 -WindowStyle Hidden 隐藏，中间那一下闪窗就是"cmd 弹窗"。
' 由 wscript.exe（GUI 程序，本身没有控制台）以 SW_HIDE 启动 powershell，就没有任何窗口了。
Option Explicit

Dim sh, ps1, cmd
If WScript.Arguments.Count < 1 Then WScript.Quit 2
ps1 = WScript.Arguments(0)

Set sh = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """"

' 0 = 隐藏窗口, False = 不等待
sh.Run cmd, 0, False
