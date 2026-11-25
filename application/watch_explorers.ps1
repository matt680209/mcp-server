function Get-ExplorerWindows {
    foreach ($window in @($shell.Windows())) {
        try {
            if ([IO.Path]::GetFileName($window.FullName) -ine 'explorer.exe') { continue }
            $location = $window.Document.Folder.Self.Path
            $path = ''
            if ($location -and [IO.Path]::IsPathRooted($location)) {
                $path = [IO.Path]::GetFullPath($location).TrimEnd('\')
            }
            [PSCustomObject]@{
                Handle = [string]$window.HWND
                Path = $path
                Window = $window
            }
        } catch [System.Runtime.InteropServices.COMException] {
            # Explorer can disappear while the shell collection is enumerated.
            continue
        }
    }
}

function Close-NewExplorerWindows($Entries, $Baseline, $Pending, $Roots, [DateTime]$Now, $ParserRoot) {
    $current = @{}
    foreach ($entry in $Entries) {
        if ($Baseline.ContainsKey($entry.Handle)) { continue }
        $matches = $false
        foreach ($root in $Roots) {
            if ($entry.Path.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or
                $entry.Path.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
                $matches = $true
                break
            }
        }
        if (-not $matches -and $ParserRoot -and
            $entry.Path.StartsWith($ParserRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
            $relative = $entry.Path.Substring($ParserRoot.Length + 1)
            $matches = $relative -match '^log_parser_\d{6}_\d{6}_\d{6}(\\|$)'
        }
        if (-not $matches) { continue }
        $key = $entry.Handle + '|' + $entry.Path
        $current[$key] = $true
        if (-not $Pending.ContainsKey($key)) { $Pending[$key] = $Now }
        if (($Now - $Pending[$key]).TotalSeconds -ge 2) {
            try {
                $entry.Window.Quit()
                $Pending.Remove($key)
            } catch [System.Runtime.InteropServices.COMException] {
                Write-Warning "Could not close an IntelAvatar-related Explorer window: $($_.Exception.Message)"
                $Baseline[$entry.Handle] = $true
            }
        }
    }
    foreach ($key in @($Pending.Keys)) {
        if (-not $current.ContainsKey($key)) { $Pending.Remove($key) }
    }
}

if ($MyInvocation.InvocationName -eq '.') { return }
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Threading.Tasks;
public static class AvatarExplorerStop {
    public static Task<string> Read() {
        return Task.Run(() => Console.In.ReadLine());
    }
}
'@
$shell = New-Object -ComObject Shell.Application
$sourceRoot = [IO.Path]::GetFullPath($env:MCP_AVATAR_INPUT_DIR).TrimEnd('\')
$downloads = $shell.NameSpace('shell:Downloads').Self.Path
$avatarRoot = [IO.Path]::Combine($downloads, 'IntelAvatar_files')
$parserRoot = [IO.Path]::GetFullPath($env:TMP).TrimEnd('\')
$baseline = @{}
$pending = @{}
foreach ($entry in @(Get-ExplorerWindows)) { $baseline[$entry.Handle] = $true }
Write-Output 'READY'
[Console]::Out.Flush()
# Windows PowerShell's Console.In can block even when ReadLineAsync is used.
$stop = [AvatarExplorerStop]::Read()
while (-not $stop.IsCompleted) {
    Close-NewExplorerWindows @(Get-ExplorerWindows) $baseline $pending @($sourceRoot, $avatarRoot) ([DateTime]::UtcNow) $parserRoot
    Start-Sleep -Milliseconds 250
}
