$ErrorActionPreference = "Continue"
$log = "D:\AIQQ\logs\napcat_task_restart.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
& taskkill /F /IM QQ.exe *>> $log
Start-Sleep -Seconds 3
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c "cd /d D:\AIQQ\NapCat.Shell && launcher.bat"'
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "AIQQNapCatBoot" -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName "AIQQNapCatBoot"
"STARTED $(Get-Date -Format o)" | Out-File $log -Append
