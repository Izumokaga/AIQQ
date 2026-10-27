$ErrorActionPreference = "Continue"
$log = "D:\AIQQ\logs\napcat_restart_admin.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
& taskkill /F /IM QQ.exe *>> $log
Start-Sleep -Seconds 4
Set-Location "D:\AIQQ\NapCat.Shell"
$proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "launcher.bat > D:\AIQQ\logs\napcat_launcher_out.txt 2>&1" -WorkingDirectory "D:\AIQQ\NapCat.Shell" -WindowStyle Hidden -PassThru
"LAUNCH_PID=$($proc.Id)" | Out-File $log -Append
$proc.WaitForExit()
"LAUNCH_RC=$($proc.ExitCode)" | Out-File $log -Append
"END $(Get-Date -Format o)" | Out-File $log -Append
