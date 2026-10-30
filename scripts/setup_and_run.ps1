param(
    [switch]$Console,
    [switch]$InstallOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$LocalTemp = Join-Path $Root "data\temp"
New-Item -ItemType Directory -Force -Path $LocalTemp | Out-Null
$env:TEMP = $LocalTemp
$env:TMP = $LocalTemp
$Venv = Join-Path $Root ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$VenvPythonW = Join-Path $Venv "Scripts\pythonw.exe"
$IconExe = Join-Path $Venv "Scripts\ARONA.exe"

function Show-Message([string]$Text, [string]$Title = "ARONA") {
    Write-Host ""
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host "  $Text" -ForegroundColor Yellow
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        [System.Windows.Forms.MessageBox]::Show($Text, $Title, "OK", "Warning") | Out-Null
    } catch {}
}

function Test-Python([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $out = & $Path -c "import sys, struct; print(sys.version_info[0], sys.version_info[1], struct.calcsize('P')*8)" 2>$null
        if (-not $out) { return $false }
        $parts = ($out -join " ").Trim().Split(" ")
        if ($parts.Count -lt 3) { return $false }
        $major = [int]$parts[0]; $minor = [int]$parts[1]; $bits = [int]$parts[2]
        return ($major -eq 3 -and $minor -ge 9 -and $minor -le 12 -and $bits -eq 64)
    } catch { return $false }
}

function Find-Python {
    if ($env:ARONA_PYTHON -and (Test-Python $env:ARONA_PYTHON)) { return $env:ARONA_PYTHON }
    $candidates = @()
    if (Test-Path -LiteralPath $VenvPython) { $candidates += $VenvPython }
    $candidates += @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python39\python.exe"),
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
    try {
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($py) {
            foreach ($ver in @("-3.12", "-3.11", "-3.10", "-3.9")) {
                try {
                    $resolved = & $py.Source $ver -c "import sys; print(sys.executable)" 2>$null
                    if ($resolved) { $candidates += $resolved.Trim() }
                } catch {}
            }
        }
    } catch {}
    try {
        $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike "*WindowsApps*") { $candidates += $cmd.Source }
    } catch {}
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-Python $candidate) { return $candidate }
    }
    return $null
}

Write-Host ""
Write-Host "  ARONA // SCHALE AUTO ASSISTANT" -ForegroundColor Cyan
Write-Host "  ------------------------------------------------" -ForegroundColor DarkCyan
Write-Host "  正在准备本地运行环境…" -ForegroundColor Gray

$basePython = Find-Python
if (-not $basePython) {
    Show-Message "没有找到可用的 64 位 Python 3.9–3.12。`n`n请安装 Python 3.12（安装时勾选 Add python.exe to PATH）：`nhttps://www.python.org/downloads/`n`n安装完成后重新双击「启动 ARONA.bat」。"
    exit 1
}
Write-Host "  使用 Python: $basePython" -ForegroundColor DarkGray

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "  正在创建独立虚拟环境 .venv …" -ForegroundColor Gray
    & $basePython -m venv $Venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
        Show-Message "创建虚拟环境失败。`n请确认 Python 安装完整，并重新运行。"
        exit 1
    }
}

# DSH / sandbox-created venvs can inherit a Low integrity label.  A Low-integrity
# Python cannot enumerate or read the Weixin process, which makes wechatauto
# report "未检测到 Weixin.exe" even while WeChat is running.  Python itself is
# Medium by default, so restore the two venv launchers to Medium on every boot.
foreach ($exe in @($VenvPython, $VenvPythonW)) {
    if (Test-Path -LiteralPath $exe) {
        & icacls.exe $exe /setintegritylevel Medium 1>$null 2>$null
    }
}

$needInstall = $false
$oldErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $VenvPython -c "import tkinter, PIL, pypdf" 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { $needInstall = $true }

if ($needInstall) {
    Write-Host "  首次运行，正在安装 Pillow 与 PDF 解析组件（可能需要几分钟）…" -ForegroundColor Cyan
    $requirements = Join-Path $Root "requirements.txt"
    & $VenvPython -m pip install --disable-pip-version-check --no-input --timeout 60 --retries 2 `
        -i https://pypi.tuna.tsinghua.edu.cn/simple -r $requirements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  镜像安装失败，尝试官方 PyPI…" -ForegroundColor Yellow
        & $VenvPython -m pip install --disable-pip-version-check --no-input --timeout 60 --retries 1 `
            -i https://pypi.org/simple -r $requirements
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  基础依赖安装未完全成功。" -ForegroundColor Yellow
        Start-Sleep -Seconds 2
    }
}

# Non-wxauto 4.1.15 backend: install once, without OCR/screenshot extras.
& $VenvPython -c "import wechatauto" 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  安装微信 4.1.15 兼容后端（数据库 + UIA，不使用 wxauto）…" -ForegroundColor Cyan
    & $VenvPython -m pip install --disable-pip-version-check --no-input --no-deps `
        -i https://pypi.tuna.tsinghua.edu.cn/simple wechatauto-replica==1.2.8.0
    & $VenvPython -m pip install --disable-pip-version-check --no-input --timeout 60 --retries 2 `
        -i https://pypi.tuna.tsinghua.edu.cn/simple -r (Join-Path $Root "requirements-wechatauto.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  4.1.15 后端安装未完成；界面仍可打开，可稍后运行「安装 4.1.15 后端.bat」。" -ForegroundColor Yellow
        Start-Sleep -Seconds 2
    }
}
$ErrorActionPreference = $oldErrorAction

# Core dependency check
$ErrorActionPreference = "Continue"
& $VenvPython -c "import tkinter" 1>$null 2>$null
$tkMissing = ($LASTEXITCODE -ne 0)
$ErrorActionPreference = $oldErrorAction
if ($tkMissing) {
    Show-Message "当前的 Python 不包含 tkinter，无法显示图形界面。`n请安装官方 python.org 的完整 Python 3.12。"
    exit 1
}

if ($InstallOnly) {
    Write-Host "  依赖安装完成。现在可以双击「启动 ARONA.bat」。" -ForegroundColor Green
    exit 0
}

# Give the taskbar and title bar the ARONA icon.  Windows uses the process
# executable icon for the taskbar, so a plain pythonw.exe launch still shows
# the Python icon even when Tk's iconphoto is set.
if (-not (Test-Path -LiteralPath $IconExe) -or
    -not (Test-Path -LiteralPath (Join-Path $Venv "Scripts\ARONA.iconv2"))) {
    try {
        & $VenvPython (Join-Path $Root "scripts\make_icon_exe.py") 1>$null 2>$null
    } catch {}
}

Write-Host "  启动 ARONA…" -ForegroundColor Green
if ($Console) {
    & $VenvPython (Join-Path $Root "run.py")
    exit $LASTEXITCODE
}

if (Test-Path -LiteralPath $IconExe) {
    Start-Process -FilePath $IconExe -ArgumentList @("run.py") -WorkingDirectory $Root
} elseif (Test-Path -LiteralPath $VenvPythonW) {
    Start-Process -FilePath $VenvPythonW -ArgumentList @("run.py") -WorkingDirectory $Root
} else {
    Start-Process -FilePath $VenvPython -ArgumentList @("run.py") -WorkingDirectory $Root
}
exit 0
