# Run this once to create a Desktop shortcut for techno_scan
# Right-click the file -> "Run with PowerShell"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$batPath     = Join-Path $projectRoot "launch_gui.bat"
$iconPath    = Join-Path $projectRoot "techno_scan.ico"
$shortcutPath = "$env:USERPROFILE\Desktop\techno_scan.lnk"

$shell    = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)

$shortcut.TargetPath       = Join-Path $projectRoot "launch_gui.bat"
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description      = "techno_scan - Electronic music event scanner"

if (Test-Path $iconPath) {
    $shortcut.IconLocation = "$iconPath,0"
}

$shortcut.Save()

Write-Host "Shortcut created on Desktop: $shortcutPath" -ForegroundColor Green
Start-Sleep -Seconds 2
