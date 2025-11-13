<#
Create a .lnk shortcut in the current user's Startup folder that points to the wrapper CMD.
This does not require admin rights and will auto-start the watchdog at user logon.
#>
param(
    [string]$WrapperRelative = '.\tools\run_watchdog_wrapper.cmd',
    [string]$ShortcutName = 'Watchdog_SF_IA7.lnk'
)

$wrapper = (Resolve-Path $WrapperRelative).Path
$startup = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup $ShortcutName

Write-Output "Creating shortcut: $linkPath -> $wrapper"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $wrapper
$shortcut.WorkingDirectory = Split-Path $wrapper
$shortcut.WindowStyle = 7
$shortcut.Save()

Write-Output "SHORTCUT_CREATED: $linkPath"
