function ConvertTo-PlainPassword([System.Security.SecureString]$Password) {
    $pointer = [IntPtr]::Zero
    try {
        $pointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
        return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}

function Save-AdminPassword(
    [string]$Path,
    [System.Security.SecureString]$Password,
    [System.Security.SecureString]$Confirmation
) {
    $plain = ConvertTo-PlainPassword $Password
    $confirmationPlain = ConvertTo-PlainPassword $Confirmation
    try {
        if (-not $plain -or $plain -cne $confirmationPlain) {
            throw 'The password is empty or the two entries do not match.'
        }
        if ($plain -cnotmatch '^[\x20-\x7E]+$') {
            throw 'The administrator password must contain only printable ASCII characters.'
        }
        $directory = Split-Path -Parent $Path
        [System.IO.Directory]::CreateDirectory($directory) | Out-Null
        $encrypted = ConvertFrom-SecureString -SecureString $Password
        $temporary = Join-Path $directory ([System.IO.Path]::GetRandomFileName())
        try {
            [System.IO.File]::WriteAllText($temporary, $encrypted)
            Move-Item -LiteralPath $temporary -Destination $Path -Force
        } finally {
            if (Test-Path -LiteralPath $temporary) {
                Remove-Item -LiteralPath $temporary -Force
            }
        }
    } finally {
        $plain = $null
        $confirmationPlain = $null
    }
}

function Read-AdminPassword([string]$Path) {
    try {
        $encrypted = [System.IO.File]::ReadAllText($Path)
        $password = ConvertTo-SecureString -String $encrypted -ErrorAction Stop
        try {
            $plain = ConvertTo-PlainPassword $password
            if (-not $plain -or $plain -cnotmatch '^[\x20-\x7E]+$') {
                throw 'The saved password is empty or invalid.'
            }
            return $plain
        } finally {
            $password.Dispose()
        }
    } catch {
        throw "Cannot decrypt the saved administrator password for this Windows user. Use local_start.bat set-password to replace it. $($_.Exception.Message)"
    }
}

function Request-AdminPassword([string]$Path) {
    Write-Host '[SETUP] Choose a long administrator password using printable ASCII characters.'
    $password = Read-Host 'Administrator password' -AsSecureString
    $confirmation = Read-Host 'Confirm administrator password' -AsSecureString
    try {
        Save-AdminPassword -Path $Path -Password $password -Confirmation $confirmation
        Write-Host '[SETUP] Administrator password saved for this Windows user.'
    } finally {
        $password.Dispose()
        $confirmation.Dispose()
    }
}
