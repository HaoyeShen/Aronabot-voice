$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "什亭之匣.lnk"
$Target = Join-Path $Root "启动 什亭之匣.bat"
$Arguments = ""
$IconExe = Join-Path $Root ".venv\Scripts\什亭之匣.exe"
if (Test-Path -LiteralPath $IconExe) {
    $Target = $IconExe
    $Arguments = "`"$(Join-Path $Root 'run.py')`""
}
$Icon = if (Test-Path -LiteralPath $IconExe) { $IconExe } else { Join-Path $Root "assets\arona_icon.ico" }
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Target
$Shortcut.Arguments = $Arguments
$Shortcut.WorkingDirectory = $Root
$Shortcut.IconLocation = "$Icon,0"
$Shortcut.Description = "什亭之匣"
$Shortcut.Save()
Write-Host "已创建桌面快捷方式：$ShortcutPath" -ForegroundColor Green
