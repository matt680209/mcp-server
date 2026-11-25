$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\admin_password.ps1')

$directory = Join-Path ([System.IO.Path]::GetTempPath()) ("mcp-password-test-" + [guid]::NewGuid().ToString('N'))
[System.IO.Directory]::CreateDirectory($directory) | Out-Null
$password = ConvertTo-SecureString 'Test-only password !42' -AsPlainText -Force
$other = ConvertTo-SecureString 'Different password' -AsPlainText -Force
try {
    $path = Join-Path $directory 'password.dpapi'
    Save-AdminPassword -Path $path -Password $password -Confirmation $password
    if ([System.IO.File]::ReadAllText($path).Contains('Test-only password !42')) {
        throw 'Password was stored in plaintext.'
    }
    if ((Read-AdminPassword -Path $path) -cne 'Test-only password !42') {
        throw 'The password was not restored correctly.'
    }
    $rejected = $false
    try {
        Save-AdminPassword -Path $path -Password $password -Confirmation $other
    } catch {
        $rejected = $true
    }
    if (-not $rejected -or (Read-AdminPassword -Path $path) -cne 'Test-only password !42') {
        throw 'A mismatched confirmation did not preserve the saved password.'
    }
    [System.IO.File]::WriteAllText($path, 'invalid encrypted data')
    $rejected = $false
    try {
        Read-AdminPassword -Path $path | Out-Null
    } catch {
        $rejected = $true
    }
    if (-not $rejected) { throw 'Corrupted password data was accepted.' }
    Write-Host 'Administrator password tests passed.'
} finally {
    $password.Dispose()
    $other.Dispose()
    Remove-Item -LiteralPath $directory -Recurse -Force
}
