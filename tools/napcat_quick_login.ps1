$ErrorActionPreference = "Continue"
$log = "D:\AIQQ\logs\napcat_quick_login.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
Get-ScheduledTask -TaskName "AIQQNapCat*" -ErrorAction SilentlyContinue | ForEach-Object {
  Stop-ScheduledTask -TaskName $_.TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -ErrorAction SilentlyContinue
}
& taskkill /F /IM QQ.exe *>> $log
Start-Sleep -Seconds 3
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c "cd /d D:\AIQQ\NapCat.Shell && launcher.bat -q 100000002 > D:\AIQQ\logs\napcat_quick_console.txt 2>&1"'
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "AIQQNapCatQuick2" -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName "AIQQNapCatQuick2"
"STARTED $(Get-Date -Format o)" | Out-File $log -Append
