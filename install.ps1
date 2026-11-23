#Requires -Version 5.1
<#
.SYNOPSIS
    One-click installer for everything-mcp on Windows.

.DESCRIPTION
    Installs everything-mcp into a private virtual environment, verifies the
    installation with a self-test, and optionally registers it with Claude
    Desktop and/or Claude Code.

    Local usage (from a cloned/extracted repo):
        powershell -ExecutionPolicy Bypass -File .\install.ps1
        .\install.bat

    Remote one-liner:
        irm https://raw.githubusercontent.com/Yuiii-owo/everything-mcp/main/install.ps1 | iex

    Remote with options:
        & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Yuiii-owo/everything-mcp/main/install.ps1))) -Configure both

.PARAMETER InstallDir
    Target directory. Default: %LOCALAPPDATA%\everything-mcp

.PARAMETER Python
    Path to a Python 3.10+ executable. Auto-detected when omitted.

.PARAMETER Version
    Release tag to install (e.g. v0.2.0) or "latest". Only used for remote installs.

.PARAMETER Configure
    Client to register the server with: none, claude-desktop, claude-code, both.

.PARAMETER Repo
    GitHub repository in owner/name form.

.PARAMETER FromLocal
    Install from the current repository instead of a GitHub release.

.PARAMETER SkipSelftest
    Do not run the post-install self-test.

.PARAMETER InstallEverything
    Install the Everything desktop client via winget if it is not running.

.PARAMETER Yes
    Assume yes for prompts (e.g. installing Python when missing).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "everything-mcp"),
    [string]$Python = "",
    [string]$Version = "latest",
    [ValidateSet("none", "claude-desktop", "claude-code", "both")]
    [string]$Configure = "none",
    [string]$Repo = "Yuiii-owo/everything-mcp",
    [switch]$FromLocal,
    [switch]$SkipSelftest,
    [switch]$InstallEverything,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    # Older .NET runtimes may not expose Tls12; the default protocol is usually fine on Windows 11.
}

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn([string]$Message) {
    Write-Host "!!  $Message" -ForegroundColor Yellow
}

function Invoke-Checked {
    param([string]$FilePath, [string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

function Get-PythonInfo {
    param([string]$Exe, [string[]]$PrefixArgs = @())
    try {
        $probe = & $Exe @PrefixArgs -c "import sys, sysconfig; print('%d.%d.%d' % sys.version_info[:3]); print(sys.executable); print(sys.platform); print(sysconfig.get_platform())" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $probe) { return $null }
        $lines = @($probe)
        if ($lines.Count -lt 4) { return $null }

        $parsedVersion = $null
        if (-not [version]::TryParse([string]$lines[0], [ref]$parsedVersion)) { return $null }
        if ($parsedVersion -lt [version]"3.10") { return $null }

        $exePath = ([string]$lines[1]).Trim()
        $sysPlatform = ([string]$lines[2]).Trim()
        $platformTag = ([string]$lines[3]).Trim()

        # Reject MSYS2/MinGW/Cygwin interpreters: uv and Windows venvs cannot use them reliably.
        if ($sysPlatform -ne "win32" -or $platformTag -like "mingw*") { return $null }
        if (-not (Test-Path -LiteralPath $exePath)) { return $null }

        return [pscustomobject]@{ Path = $exePath; Version = $parsedVersion }
    } catch {
        return $null
    }
}

function Find-Python {
    param([string]$Explicit)

    if ($Explicit) {
        $info = Get-PythonInfo -Exe $Explicit
        if ($info) { return $info }
        throw "The supplied Python is unavailable or older than 3.10: $Explicit"
    }

    $candidates = New-Object System.Collections.ArrayList

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($tag in @("3.13", "3.12", "3.11", "3.10", "3")) {
            [void]$candidates.Add([pscustomobject]@{ Exe = $pyLauncher.Source; Args = @("-$tag") })
        }
    }

    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        $env:ProgramFiles
    )
    foreach ($root in $roots) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
        Get-ChildItem -LiteralPath $root -Filter "Python3*" -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $exe = Join-Path $_.FullName "python.exe"
                if (Test-Path -LiteralPath $exe) {
                    [void]$candidates.Add([pscustomobject]@{ Exe = $exe; Args = @() })
                }
            }
    }

    # PATH is checked last: it often contains MSYS2/MinGW builds that get rejected above.
    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            [void]$candidates.Add([pscustomobject]@{ Exe = $cmd.Source; Args = @() })
        }
    }

    foreach ($candidate in $candidates) {
        $info = Get-PythonInfo -Exe $candidate.Exe -PrefixArgs $candidate.Args
        if ($info) { return $info }
    }
    return $null
}

function Install-PythonViaWinget {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.10+ was not found and winget is unavailable. Install Python from https://www.python.org/downloads/ and rerun this script."
    }

    Write-Step "Python 3.10+ not found; installing Python 3.12 via winget"
    & $winget.Source install --id Python.Python.3.12 -e --scope user --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install Python (exit $LASTEXITCODE)."
    }

    $info = Find-Python
    if (-not $info) {
        throw "Python was installed but python.exe could not be located. Open a new terminal and rerun this script."
    }
    return $info
}

function Resolve-InstallTarget {
    param([string]$Repo, [string]$Version, [string]$ScriptRoot, [switch]$ForceLocal)

    $looksLocal = $ScriptRoot -and (Test-Path -LiteralPath (Join-Path $ScriptRoot "pyproject.toml"))
    if ($ForceLocal -or $looksLocal) {
        if (-not $looksLocal) {
            throw "-FromLocal requires running install.ps1 from the repository root."
        }

        $distDir = Join-Path $ScriptRoot "dist"
        if (Test-Path -LiteralPath $distDir) {
            $wheel = Get-ChildItem -LiteralPath $distDir -Filter "everything_mcp-*.whl" -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
            if ($wheel) {
                return [pscustomobject]@{ Kind = "wheel"; Path = $wheel.FullName; Cleanup = $null }
            }
        }

        return [pscustomobject]@{ Kind = "source"; Path = $ScriptRoot; Cleanup = $null }
    }

    if ($Version -eq "latest") {
        $uri = "https://api.github.com/repos/$Repo/releases/latest"
    } else {
        $uri = "https://api.github.com/repos/$Repo/releases/tags/$Version"
    }

    $headers = @{ "User-Agent" = "everything-mcp-installer" }
    if ($env:GITHUB_TOKEN) {
        $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN"
    }

    try {
        $release = Invoke-RestMethod -Uri $uri -Headers $headers -UseBasicParsing
    } catch {
        throw "Could not fetch release metadata from GitHub ($uri). Check your network, or use -FromLocal. Original error: $($_.Exception.Message)"
    }

    $asset = @($release.assets) | Where-Object { $_.name -like "*.whl" } | Select-Object -First 1
    if (-not $asset) {
        throw "Release $($release.tag_name) has no .whl asset."
    }

    $tempDir = Join-Path ([IO.Path]::GetTempPath()) ("everything-mcp-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    $wheelPath = Join-Path $tempDir $asset.name

    Write-Step "Downloading $($asset.name)"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $wheelPath -UseBasicParsing

    $sumsAsset = @($release.assets) | Where-Object { $_.name -eq "SHA256SUMS.txt" } | Select-Object -First 1
    if ($sumsAsset) {
        $sumsPath = Join-Path $tempDir "SHA256SUMS.txt"
        Invoke-WebRequest -Uri $sumsAsset.browser_download_url -OutFile $sumsPath -UseBasicParsing
        $line = Get-Content -LiteralPath $sumsPath |
            Where-Object { $_ -match [regex]::Escape($asset.name) } |
            Select-Object -First 1
        if ($line) {
            $expected = (($line -split "\s+") | Where-Object { $_ })[0].ToLowerInvariant()
            $actual = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($expected -ne $actual) {
                throw "SHA256 mismatch for $($asset.name)."
            }
            Write-Step "SHA256 verified"
        }
    }

    return [pscustomobject]@{ Kind = "wheel"; Path = $wheelPath; Cleanup = $tempDir }
}

function Install-PackageVenv {
    param(
        [string]$PythonPath,
        [string]$InstallDir,
        [string]$PackageSpec
    )

    $venvDir = Join-Path $InstallDir "venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    $uv = Get-Command uv -ErrorAction SilentlyContinue

    if (-not (Test-Path -LiteralPath $venvPython)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
        if ($uv) {
            Write-Step "Creating virtual environment (uv)"
            Invoke-Checked -FilePath $uv.Source -Arguments @("venv", "--python", $PythonPath, $venvDir)
        } else {
            Write-Step "Creating virtual environment (python -m venv)"
            Invoke-Checked -FilePath $PythonPath -Arguments @("-m", "venv", $venvDir)
        }
    }

    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Virtual environment creation failed: $venvDir"
    }

    Write-Step "Installing everything-mcp"
    if ($uv) {
        Invoke-Checked -FilePath $uv.Source -Arguments @("pip", "install", "--python", $venvPython, "--upgrade", $PackageSpec)
    } else {
        Invoke-Checked -FilePath $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip", "--disable-pip-version-check")
        Invoke-Checked -FilePath $venvPython -Arguments @("-m", "pip", "install", "--upgrade", $PackageSpec, "--disable-pip-version-check")
    }

    return $venvPython
}

function Test-EverythingRunning {
    return [bool](Get-Process -Name "Everything" -ErrorAction SilentlyContinue)
}

function Install-EverythingClient {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Warn "winget is unavailable; install Everything manually from https://www.voidtools.com/downloads/"
        return
    }

    Write-Step "Installing the Everything desktop client via winget"
    & $winget.Source install --id voidtools.Everything -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "winget failed to install Everything (exit $LASTEXITCODE). Install it manually from https://www.voidtools.com/downloads/"
    }
}

function Set-ClaudeDesktopConfig {
    param([string]$PythonPath)

    if (-not $env:APPDATA) {
        Write-Warn "APPDATA is not set; skipping Claude Desktop configuration."
        return
    }

    $configPath = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
    $parent = Split-Path -Parent $configPath
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $config = $null
    if (Test-Path -LiteralPath $configPath) {
        $raw = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
        if ($raw -and $raw.Trim()) {
            try {
                $config = $raw | ConvertFrom-Json
            } catch {
                throw "Existing Claude Desktop config is not valid JSON; left untouched: $configPath"
            }
        }
        Copy-Item -LiteralPath $configPath -Destination "$configPath.bak" -Force
        Write-Step "Backed up existing Claude Desktop config to $configPath.bak"
    }

    if ($null -eq $config) {
        $config = [pscustomobject]@{}
    }

    if (-not ($config.PSObject.Properties.Name -contains "mcpServers") -or $null -eq $config.mcpServers) {
        $config | Add-Member -NotePropertyName "mcpServers" -NotePropertyValue ([pscustomobject]@{}) -Force
    }

    $entry = [pscustomobject]@{
        command = $PythonPath
        args    = @("-m", "everything_mcp")
    }
    $config.mcpServers | Add-Member -NotePropertyName "everything" -NotePropertyValue $entry -Force

    $json = $config | ConvertTo-Json -Depth 20
    [IO.File]::WriteAllText($configPath, $json + "`r`n", [System.Text.UTF8Encoding]::new($false))
    Write-Step "Claude Desktop config updated: $configPath"
}

function Set-ClaudeCodeConfig {
    param([string]$PythonPath)

    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) {
        Write-Warn "claude CLI not found; skipping Claude Code configuration."
        return
    }

    & $claude.Source mcp remove everything -s user 2>$null | Out-Null
    & $claude.Source mcp add everything -s user -- $PythonPath -m everything_mcp
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Could not update Claude Code automatically. Add it manually:"
        Write-Host "  claude mcp add everything -s user -- `"$PythonPath`" -m everything_mcp"
    } else {
        Write-Step "Claude Code user config updated"
    }
}

Write-Host ""
Write-Host "everything-mcp installer" -ForegroundColor White
Write-Host "Repository: https://github.com/$Repo" -ForegroundColor DarkGray
Write-Host ""

if ($env:OS -ne "Windows_NT") {
    throw "everything-mcp requires Windows (the Everything SDK is Windows-only)."
}

$pythonInfo = Find-Python -Explicit $Python

if (-not $pythonInfo) {
    $installPython = $Yes
    if (-not $Yes) {
        $answer = Read-Host "Python 3.10+ was not found. Install Python 3.12 via winget? [Y/n]"
        $installPython = ($answer -eq "" -or $answer -match "^(y|yes)$")
    }
    if (-not $installPython) {
        throw "Install Python 3.10+ from https://www.python.org/downloads/ and rerun this script."
    }
    $pythonInfo = Install-PythonViaWinget
}

Write-Step "Python $($pythonInfo.Version) at $($pythonInfo.Path)"

$target = Resolve-InstallTarget -Repo $Repo -Version $Version -ScriptRoot $PSScriptRoot -ForceLocal:$FromLocal

try {
    $venvPython = Install-PackageVenv -PythonPath $pythonInfo.Path -InstallDir $InstallDir -PackageSpec $target.Path

    $launcherPath = Join-Path $InstallDir "everything-mcp.cmd"
    $launcherBody = "@echo off`r`n`"%~dp0venv\Scripts\python.exe`" -m everything_mcp %*`r`n"
    [IO.File]::WriteAllText($launcherPath, $launcherBody, [System.Text.Encoding]::ASCII)
    Write-Step "Launcher written: $launcherPath"

    $selftestOk = $null
    if (-not $SkipSelftest) {
        Write-Step "Running self-test"
        $selftestOutput = & $venvPython -m everything_mcp --selftest 2>&1
        $selftestExit = $LASTEXITCODE
        $selftestOutput | ForEach-Object { Write-Host $_ }
        if ($selftestExit -eq 0) {
            $selftestOk = $true
        } elseif ($selftestExit -eq 2) {
            $selftestOk = $false
        } else {
            throw "Self-test failed (exit $selftestExit)."
        }

        if ($null -eq $selftestOk -or -not $selftestOk) {
            Write-Warn "Everything.exe is not running. The SDK needs the desktop client to be running."
        }
    }

    $everythingRunning = Test-EverythingRunning
    if (-not $everythingRunning) {
        if ($InstallEverything) {
            Install-EverythingClient
        } else {
            Write-Warn "Everything.exe was not detected."
            Write-Host "    Start the client (tray icon must be present), or install it with:" -ForegroundColor DarkGray
            Write-Host "      winget install --id voidtools.Everything -e" -ForegroundColor DarkGray
            Write-Host "    Download: https://www.voidtools.com/downloads/" -ForegroundColor DarkGray
        }
    }

    if ($Configure -eq "claude-desktop" -or $Configure -eq "both") {
        Set-ClaudeDesktopConfig -PythonPath $venvPython
    }
    if ($Configure -eq "claude-code" -or $Configure -eq "both") {
        Set-ClaudeCodeConfig -PythonPath $venvPython
    }

    $configSnippet = [pscustomobject]@{
        mcpServers = [pscustomobject]@{
            everything = [pscustomobject]@{
                command = $venvPython
                args    = @("-m", "everything_mcp")
            }
        }
    } | ConvertTo-Json -Depth 10

    Write-Host ""
    Write-Host "Installation complete." -ForegroundColor Green
    Write-Host "  Python    : $($pythonInfo.Path) ($($pythonInfo.Version))"
    Write-Host "  Venv      : $(Join-Path $InstallDir 'venv')"
    Write-Host "  Launcher  : $launcherPath"
    if ($null -ne $selftestOk) {
        Write-Host "  Self-test : $(if ($selftestOk) { 'passed' } else { 'Everything client not running' })"
    }
    Write-Host ""
    Write-Host "MCP client config (stdio):" -ForegroundColor White
    Write-Host $configSnippet
    Write-Host ""
    if ($Configure -ne "none") {
        Write-Host "Restart the MCP client so it picks up the new server." -ForegroundColor DarkGray
    } else {
        Write-Host "Add the JSON above to your MCP client, or rerun with -Configure claude-desktop|claude-code|both." -ForegroundColor DarkGray
    }
} finally {
    if ($target.Cleanup -and (Test-Path -LiteralPath $target.Cleanup)) {
        Remove-Item -LiteralPath $target.Cleanup -Recurse -Force -ErrorAction SilentlyContinue
    }
}
