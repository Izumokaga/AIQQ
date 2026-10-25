$ErrorActionPreference = "Continue"
$log = "D:\AIQQ\logs\edge_cdp_launch.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$runtime = "C:\Windows\Temp\AIQQEdge"
New-Item -ItemType Directory -Force $runtime | Out-Null
$env:TEMP = $runtime
$env:TMP = $runtime
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$ud = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"
Start-Process -FilePath $edge -ArgumentList "--remote-debugging-port=9222", "--user-data-dir=$ud", "--profile-directory=Default", "--no-first-run", "--no-default-browser-check", "https://github.com/Izumokaga/AIQQ"
Start-Sleep -Seconds 600
"END $(Get-Date -Format o)" | Out-File $log -Append
