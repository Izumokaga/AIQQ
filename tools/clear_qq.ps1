$ErrorActionPreference = "Continue"
$log = "D:\AIQQ\logs\clear_qq.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
Get-ScheduledTask -TaskName "AIQQNapCat*" -ErrorAction SilentlyContinue | ForEach-Object {
  Stop-ScheduledTask -TaskName $_.TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -ErrorAction SilentlyContinue
  "REMOVED $($_.TaskName)" | Out-File $log -Append
}
& taskkill /F /IM QQ.exe *>> $log
Start-Sleep -Seconds 3
$left = @(Get-Process QQ -ErrorAction SilentlyContinue)
"LEFT_QQ=$($left.Count)" | Out-File $log -Append
"END $(Get-Date -Format o)" | Out-File $log -Append
