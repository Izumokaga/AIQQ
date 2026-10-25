$ErrorActionPreference = "Continue"
$log = "D:\AIQQ\logs\napcat_clean_login.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
# Stop old NapCat tasks so only one launcher runs.
Get-ScheduledTask -TaskName "AIQQNapCat*" -ErrorAction SilentlyContinue | ForEach-Object {
  Stop-ScheduledTask -TaskName $_.TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -ErrorAction SilentlyContinue
  "REMOVED $($_.TaskName)" | Out-File $log -Append
}
& taskkill /F /IM QQ.exe *>> $log
Start-Sleep -Seconds 3
Remove-Item "D:\AIQQ\NapCat.Shell\cache\qrcode.png" -Force -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument '/c "cd /d D:\AIQQ\NapCat.Shell && launcher.bat > D:\AIQQ\logs\napcat_login_console.txt 2>&1"'
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "AIQQNapCatLogin" -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName "AIQQNapCatLogin"
"STARTED $(Get-Date -Format o)" | Out-File $log -Append
