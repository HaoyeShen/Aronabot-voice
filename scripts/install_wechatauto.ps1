$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$LocalTemp = Join-Path $Root "data\temp"
New-Item -ItemType Directory -Force -Path $LocalTemp | Out-Null
$env:TEMP = $LocalTemp
$env:TMP = $LocalTemp
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$PythonW = Join-Path $Root ".venv\Scripts\pythonw.exe"

Write-Host ""
Write-Host "  ARONA // WeChat 4.1.15 compatibility backend" -ForegroundColor Cyan
Write-Host "  ------------------------------------------------" -ForegroundColor DarkCyan
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "  未找到 .venv。请先双击「启动 ARONA.bat」完成基础环境安装。" -ForegroundColor Yellow
    exit 1
}

# A sandbox-created venv can carry a Low mandatory label; Low-integrity Python
# cannot read the Weixin process.  Restore Medium before installing/running.
foreach ($exe in @($Python, $PythonW)) {
    if (Test-Path -LiteralPath $exe) {
        & icacls.exe $exe /setintegritylevel Medium 1>$null 2>$null
    }
}

$ErrorActionPreference = "Continue"
Write-Host "  安装 wechatauto-replica（不安装 OCR/截图依赖）…" -ForegroundColor Gray
& $Python -m pip install --disable-pip-version-check --no-input --no-deps `
    -i https://pypi.tuna.tsinghua.edu.cn/simple wechatauto-replica==1.2.8.0
if ($LASTEXITCODE -ne 0) {
    Write-Host "  wechatauto-replica 下载失败，请检查网络后重试。" -ForegroundColor Red
    exit 1
}

Write-Host "  安装 UIA / 数据库读取依赖…" -ForegroundColor Gray
& $Python -m pip install --disable-pip-version-check --no-input --timeout 60 --retries 2 `
    -i https://pypi.tuna.tsinghua.edu.cn/simple -r (Join-Path $Root "requirements-wechatauto.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "  依赖安装失败。" -ForegroundColor Red
    exit 1
}

& $Python -c "import wechatauto, uiautomation, cryptography; print('wechatauto', wechatauto.__version__)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  安装校验失败，请查看上面的错误。" -ForegroundColor Red
    exit 1
}
$ErrorActionPreference = "Stop"
Write-Host ""
Write-Host "  安装完成。" -ForegroundColor Green
Write-Host "  打开「设置 → 微信连接」，适配器选择：" -ForegroundColor White
Write-Host "  wechatauto（4.1.15 实验）" -ForegroundColor Cyan
Write-Host ""
Write-Host "  注意：此实验后端会读取本机微信数据库，并在微信进程中临时开启 Qt 无障碍位；" -ForegroundColor Yellow
Write-Host "  只在本人设备、本人账号上使用，并确保 Python 与微信权限级别一致（不要以管理员运行）。" -ForegroundColor Yellow
