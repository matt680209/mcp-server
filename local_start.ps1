param(
    [ValidateSet('start', 'stop', 'set-password')]
    [string]$Action = 'start'
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if ($Action -eq 'stop') {
    $scriptPath = Join-Path $PSScriptRoot 'local_start.ps1'
    $pattern = '(?i)(?:^|\s)-File\s+"?' + [regex]::Escape($scriptPath) + '"?(?:\s|$)'
    $processes = @(Get-CimInstance Win32_Process)
    $launchers = @($processes | Where-Object {
        $_.Name -ieq 'powershell.exe' -and $_.ProcessId -ne $PID -and
        $_.CommandLine -match $pattern -and $_.CommandLine -notmatch '(?i)\s-Action\s+(?:stop|set-password)(?:\s|$)'
    })
    if ($launchers.Count -eq 0) {
        Write-Host '[INFO] No MCP service started by this launcher is running.'
        exit 0
    }

    $pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    foreach ($launcher in $launchers) {
        $roots = @($processes | Where-Object {
            $_.ParentProcessId -eq $launcher.ProcessId -and $_.ExecutablePath -ieq $pythonPath
        })
        $targets = @($launcher)
        foreach ($root in $roots) {
            $targets += $root
            $targets += @($processes | Where-Object {
                $_.ParentProcessId -eq $root.ProcessId -and $_.Name -ieq 'python.exe'
            })
        }
        for ($index = $targets.Count - 1; $index -ge 0; $index--) {
            $processId = $targets[$index].ProcessId
            try {
                Stop-Process -Id $processId -ErrorAction Stop
            } catch {
                if (Get-Process -Id $processId -ErrorAction SilentlyContinue) { throw }
            }
        }
        Write-Host "[STOP] Stopped MCP launcher $($launcher.ProcessId) and its server processes."
    }
    Write-Host '[INFO] Running tasks were interrupted.'
    exit 0
}

. (Join-Path $PSScriptRoot 'admin_password.ps1')
$passwordPath = Join-Path $PSScriptRoot 'data\admin-password.dpapi'
if ($Action -eq 'set-password') {
    Request-AdminPassword -Path $passwordPath
    Write-Host '[INFO] Restart MCP to apply the new password.'
    exit 0
}
if (-not $env:MCP_ADMIN_PASSWORD) {
    if (-not (Test-Path -LiteralPath $passwordPath)) {
        Request-AdminPassword -Path $passwordPath
    }
    $env:MCP_ADMIN_PASSWORD = Read-AdminPassword -Path $passwordPath
}

$port = 8000
$hostName = '127.0.0.1'
$settingsPath = Join-Path $PSScriptRoot 'data\settings.json'
if (Test-Path -LiteralPath $settingsPath) {
    try {
        $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        if ($settings.network.port) { $port = [int]$settings.network.port }
        if ($settings.network.host -in @('::', '[::]')) {
            $hostName = '::1'
        } elseif ($settings.network.host -and $settings.network.host -notin @('0.0.0.0', 'localhost')) {
            $hostName = $settings.network.host
        }
    } catch {
        Write-Error "Could not read network settings: $_"
        exit 1
    }
}
if ($hostName.Contains(':') -and -not $hostName.StartsWith('[')) {
    $hostName = "[$hostName]"
}
$scheme = 'http'
if ($env:MCP_SSL_CERTFILE -and $env:MCP_SSL_KEYFILE) { $scheme = 'https' }
$url = "${scheme}://${hostName}:${port}/"

Write-Host "[START] Starting Avatar MCP. The service uses the configured network bind address."
Write-Host "[INFO] UI: $url"
Write-Host '[INFO] Press Ctrl+C to stop the service.'

$process = Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') `
    -ArgumentList 'main.py' -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
Write-Host '[INFO] Waiting for the web UI to become ready.'
$null = $process.Handle
try {
    $deadline = (Get-Date).AddSeconds(60)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) {
            $process.WaitForExit()
            Write-Host "[ERROR] MCP exited before it became ready (exit code $($process.ExitCode))."
            Write-Host '[INFO] Check the Python virtual environment and requirements in README.md.'
            exit 1
        }
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if ($ready) {
        Write-Host "[READY] Opening $url"
        try {
            Start-Process $url
        } catch {
            Write-Warning "Could not open the browser. Open $url manually: $_"
        }
    } else {
        Write-Warning "The UI did not respond within 60 seconds. Check the service output and open $url manually."
    }
    $process.WaitForExit()
    exit $process.ExitCode
} finally {
    $process.Refresh()
    if (-not $process.HasExited) { Stop-Process -Id $process.Id }
}
