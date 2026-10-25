$log = "D:\AIQQ\logs\qq_cmdlines.txt"
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8
Get-CimInstance Win32_Process -Filter "Name='QQ.exe'" -ErrorAction SilentlyContinue |
  ForEach-Object {
    "PID=$($_.ProcessId) PPID=$($_.ParentProcessId) CMD=$($_.CommandLine)" | Out-File $log -Append -Encoding utf8
  }
"END $(Get-Date -Format o)" | Out-File $log -Append -Encoding utf8
