$ErrorActionPreference = "Continue"
$log = "D:\AIQQ\logs\edge_task_launch.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$runtime = "C:\Windows\Temp\AIQQEdge"
New-Item -ItemType Directory -Force $runtime | Out-Null
$env:TEMP = $runtime
$env:TMP = $runtime
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$ud = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File D:\AIQQ\tools\edge_task_runner.ps1"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "AIQQEdgeCDP" -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName "AIQQEdgeCDP"
"STARTED $(Get-Date -Format o)" | Out-File $log -Append
