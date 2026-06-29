param(
    [switch]$Enable,
    [switch]$Disable,
    [string]$Server = "127.0.0.1:17890"
)

$ErrorActionPreference = "Stop"
$regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
$envRegPath = "HKCU:\Environment"
$stateFile = Join-Path $env:TEMP "us_socks_proxy_backup.json"
$proxyUrl = "http://$Server"

function Save-ProxyState {
    $state = @{
        ProxyEnable = (Get-ItemProperty -Path $regPath -Name ProxyEnable -ErrorAction SilentlyContinue).ProxyEnable
        ProxyServer = (Get-ItemProperty -Path $regPath -Name ProxyServer -ErrorAction SilentlyContinue).ProxyServer
        ProxyOverride = (Get-ItemProperty -Path $regPath -Name ProxyOverride -ErrorAction SilentlyContinue).ProxyOverride
        AutoDetect = (Get-ItemProperty -Path $regPath -Name AutoDetect -ErrorAction SilentlyContinue).AutoDetect
        HTTP_PROXY = (Get-ItemProperty -Path $envRegPath -Name HTTP_PROXY -ErrorAction SilentlyContinue).HTTP_PROXY
        HTTPS_PROXY = (Get-ItemProperty -Path $envRegPath -Name HTTPS_PROXY -ErrorAction SilentlyContinue).HTTPS_PROXY
        ALL_PROXY = (Get-ItemProperty -Path $envRegPath -Name ALL_PROXY -ErrorAction SilentlyContinue).ALL_PROXY
        NO_PROXY = (Get-ItemProperty -Path $envRegPath -Name NO_PROXY -ErrorAction SilentlyContinue).NO_PROXY
    }
    $state | ConvertTo-Json | Set-Content -Path $stateFile -Encoding UTF8
}

function Stop-ConflictingVpn {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.ExecutablePath -like "*GoGoJumpVPN*") -or
            ($_.CommandLine -like "*GoGoJumpVPN*")
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

function Set-UserEnvProxy {
    param([string]$Url)
    Set-ItemProperty -Path $envRegPath -Name HTTP_PROXY -Value $Url
    Set-ItemProperty -Path $envRegPath -Name HTTPS_PROXY -Value $Url
    Set-ItemProperty -Path $envRegPath -Name ALL_PROXY -Value $Url
    Set-ItemProperty -Path $envRegPath -Name NO_PROXY -Value "localhost,127.0.0.1"
    $env:HTTP_PROXY = $Url
    $env:HTTPS_PROXY = $Url
    $env:ALL_PROXY = $Url
    $env:NO_PROXY = "localhost,127.0.0.1"
}

function Clear-UserEnvProxy {
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")) {
        if (Get-ItemProperty -Path $envRegPath -Name $name -ErrorAction SilentlyContinue) {
            Remove-ItemProperty -Path $envRegPath -Name $name -ErrorAction SilentlyContinue
        }
        Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
    }
}

function Restore-UserEnvFromState {
    if (-not (Test-Path $stateFile)) {
        Clear-UserEnvProxy
        return
    }
    $state = Get-Content -Path $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")) {
        $value = $state.$name
        if ($null -ne $value -and "$value".Length -gt 0) {
            Set-ItemProperty -Path $envRegPath -Name $name -Value $value
        } elseif (Get-ItemProperty -Path $envRegPath -Name $name -ErrorAction SilentlyContinue) {
            Remove-ItemProperty -Path $envRegPath -Name $name -ErrorAction SilentlyContinue
        }
    }
}

function Notify-ProxyChange {
    if (-not ("WinInetProxy" -as [type])) {
        Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinInetProxy {
    [DllImport("wininet.dll", SetLastError = true)]
    public static extern bool InternetSetOption(IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
}
public class Win32Env {
    [DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Auto)]
    public static extern IntPtr SendMessageTimeout(IntPtr hWnd, int Msg, IntPtr wParam, string lParam, int fuFlags, int uTimeout, out IntPtr lpdwResult);
}
"@
    }
    [WinInetProxy]::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0) | Out-Null
    [WinInetProxy]::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0) | Out-Null
    $result = [IntPtr]::Zero
    [Win32Env]::SendMessageTimeout([IntPtr]0xffff, 0x1a, [IntPtr]::Zero, "Environment", 2, 5000, [ref]$result) | Out-Null
}

if ($Enable) {
    Save-ProxyState
    Stop-ConflictingVpn

    Set-ItemProperty -Path $regPath -Name ProxyEnable -Value 1
    Set-ItemProperty -Path $regPath -Name ProxyServer -Value $Server
    Set-ItemProperty -Path $regPath -Name ProxyOverride -Value "<local>"
    Set-ItemProperty -Path $regPath -Name AutoDetect -Value 0

    Set-UserEnvProxy -Url $proxyUrl

    & netsh winhttp import proxy source=ie | Out-Null

    Notify-ProxyChange
    Write-Host "global proxy enabled: $Server"
}

if ($Disable) {
    if (Test-Path $stateFile) {
        $state = Get-Content -Path $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -ne $state.ProxyEnable) {
            Set-ItemProperty -Path $regPath -Name ProxyEnable -Value ([int]$state.ProxyEnable)
        } else {
            Set-ItemProperty -Path $regPath -Name ProxyEnable -Value 0
        }
        if ($null -ne $state.ProxyServer) {
            Set-ItemProperty -Path $regPath -Name ProxyServer -Value $state.ProxyServer
        }
        if ($null -ne $state.ProxyOverride) {
            Set-ItemProperty -Path $regPath -Name ProxyOverride -Value $state.ProxyOverride
        }
        if ($null -ne $state.AutoDetect) {
            Set-ItemProperty -Path $regPath -Name AutoDetect -Value ([int]$state.AutoDetect)
        }
        Restore-UserEnvFromState
        Remove-Item -Path $stateFile -Force -ErrorAction SilentlyContinue
    } else {
        Set-ItemProperty -Path $regPath -Name ProxyEnable -Value 0
        Clear-UserEnvProxy
    }

    # 清除 bat 脚本写入的 17890 代理环境变量，避免与狗急跳墙等 VPN 冲突
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")) {
        $value = (Get-ItemProperty -Path $envRegPath -Name $name -ErrorAction SilentlyContinue).$name
        if ($value -like "*17890*") {
            Remove-ItemProperty -Path $envRegPath -Name $name -ErrorAction SilentlyContinue
        }
    }

    & netsh winhttp import proxy source=ie | Out-Null

    Notify-ProxyChange
    Write-Host "global proxy disabled"
}
