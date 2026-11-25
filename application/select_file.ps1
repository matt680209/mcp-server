$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class AvatarFileDialog {
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr SendMessage(IntPtr hwnd, uint msg, IntPtr wparam, string lparam);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr SendMessage(IntPtr hwnd, uint msg, IntPtr wparam, StringBuilder lparam);
    [DllImport("user32.dll", SetLastError=true)]
    public static extern bool PostMessage(IntPtr hwnd, uint msg, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
}
'@
$path = $env:MCP_AVATAR_INPUT_FILE
$avatarPid = [int]$env:MCP_AVATAR_PID
if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Input file does not exist.' }
function Test-AvatarDescendant([int]$processId) {
    while ($processId -gt 0) {
        if ($processId -eq $avatarPid) { return $true }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
        if (-not $process -or $process.ParentProcessId -eq $processId) { return $false }
        $processId = [int]$process.ParentProcessId
    }
    return $false
}
$root = [System.Windows.Automation.AutomationElement]::RootElement
$condition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, 'Select local analysis file')
$dialog = $null
for ($attempt = 0; $attempt -lt 120; $attempt++) {
    $dialogs = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $condition)
    foreach ($candidate in $dialogs) {
        [uint32]$ownerPid = 0
        [void][AvatarFileDialog]::GetWindowThreadProcessId([IntPtr]$candidate.Current.NativeWindowHandle, [ref]$ownerPid)
        if (Test-AvatarDescendant ([int]$ownerPid)) { $dialog = $candidate; break }
    }
    if ($dialog) { break }
    Start-Sleep -Milliseconds 250
}
if (-not $dialog) { throw 'IntelAvatar file picker did not open.' }
$dialogHandle = $dialog.Current.NativeWindowHandle
Start-Sleep -Seconds 3
$inputCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::AutomationIdProperty, '1148')
$input = $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants, $inputCondition) |
    Where-Object { $_.Current.ClassName -eq 'Edit' -and $_.Current.NativeWindowHandle -ne 0 } |
    Select-Object -First 1
if (-not $input) { throw 'File picker filename input was not found.' }
$handle = [IntPtr]$input.Current.NativeWindowHandle
if ([AvatarFileDialog]::SendMessage($handle, 0x000C, [IntPtr]::Zero, $path) -eq [IntPtr]::Zero) {
    throw 'Could not set file picker path.'
}
$text = New-Object System.Text.StringBuilder ($path.Length + 1)
[void][AvatarFileDialog]::SendMessage($handle, 0x000D, [IntPtr]$text.Capacity, $text)
if ($text.ToString() -cne $path) { throw 'File picker did not retain the requested path.' }
$buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::AutomationIdProperty, '1')
$button = $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition) |
    Where-Object { $_.Current.ClassName -eq 'Button' -and $_.Current.NativeWindowHandle -ne 0 } |
    Select-Object -First 1
if (-not $button) { throw 'File picker Open button was not found.' }
if (-not [AvatarFileDialog]::PostMessage([IntPtr]$button.Current.NativeWindowHandle, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)) {
    throw 'Could not activate the file picker Open button.'
}
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 250
    $matching = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $condition) |
        Where-Object { $_.Current.NativeWindowHandle -eq $dialogHandle }
    if (-not $matching) { exit 0 }
}
throw 'File picker did not close.'
