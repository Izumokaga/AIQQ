$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $Root "logs\napcat_clean_qq.txt"
New-Item -ItemType Directory -Force (Split-Path -Parent $log) | Out-Null
"START $(Get-Date -Format o)" | Out-File $log -Encoding utf8

# 启动 NapCat 前清除所有已有 QQ 进程，避免 QQ 多实例/旧登录占用导致
# “已有QQ登录”或“登录成功正在启动 NapCat”卡住。
# 该脚本由 launcher.bat 在管理员权限下调用。
$before = @(Get-Process QQ -ErrorAction SilentlyContinue)
"CLEAR_ALL_QQ count=$($before.Count)" | Out-File $log -Append -Encoding utf8
taskkill /F /T /IM QQ.exe 2>&1 | Out-File $log -Append -Encoding utf8
Start-Sleep -Seconds 2
$left = @(Get-Process QQ -ErrorAction SilentlyContinue)
"LEFT_QQ=$($left.Count)" | Out-File $log -Append -Encoding utf8
"END $(Get-Date -Format o)" | Out-File $log -Append -Encoding utf8
