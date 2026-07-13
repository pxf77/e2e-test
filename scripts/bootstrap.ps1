param(
    [switch]$SkipNpm,
    [switch]$SkipPlaywright,
    [switch]$UsePip
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Require-Command {
    param(
        [string]$Name,
        [string]$Hint
    )
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name not found. $Hint"
    }
}

function Test-Python312 {
    param([string[]]$Command)
    try {
        $script = "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
        $null = Invoke-CommandArray -Command $Command -Arguments @("-c", $script) 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Invoke-CommandArray {
    param(
        [string[]]$Command,
        [string[]]$Arguments
    )
    if ($Command.Count -eq 1) {
        & $Command[0] @Arguments
    } else {
        $commandArguments = @($Command[1..($Command.Count - 1)]) + $Arguments
        & $Command[0] @commandArguments
    }
}

function Invoke-CheckedCommandArray {
    param(
        [string[]]$Command,
        [string[]]$Arguments
    )
    Invoke-CommandArray -Command $Command -Arguments $Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($Command -join ' ') $($Arguments -join ' ')"
    }
}

function Resolve-Python312 {
    $candidates = @()
    if ($env:E2E_AGENT_PYTHON) {
        $candidates += ,@($env:E2E_AGENT_PYTHON)
    }
    $venvPython = Join-Path $RepoRoot ".venv/Scripts/python.exe"
    if (Test-Path $venvPython) {
        $candidates += ,@($venvPython)
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $candidates += ,@("python")
    }
    if (Get-Command python3.12 -ErrorAction SilentlyContinue) {
        $candidates += ,@("python3.12")
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += ,@("py", "-3.12")
    }

    foreach ($candidate in $candidates) {
        if (Test-Python312 -Command $candidate) {
            return $candidate
        }
    }
    throw "Python 3.12+ is required. Set E2E_AGENT_PYTHON to a Python 3.12 executable, install Python 3.12, or create .venv with Python 3.12."
}

Write-Step "Checking system tools"
if (-not $SkipNpm) {
    Require-Command npm "Install Node.js and reopen this terminal."
}

$PythonCommand = Resolve-Python312
$pythonVersion = Invoke-CommandArray -Command $PythonCommand -Arguments @("-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))")
Write-Host "Python: $pythonVersion"

Write-Step "Installing Python dependencies"
$hasUv = [bool](Get-Command uv -ErrorAction SilentlyContinue)
if ($hasUv -and -not $UsePip) {
    Invoke-CheckedCommandArray -Command @("uv") -Arguments @("sync", "--all-extras")
} else {
    Invoke-CheckedCommandArray -Command $PythonCommand -Arguments @("-m", "pip", "install", "-e", ".[dev]")
}

$venvPythonAfterInstall = Join-Path $RepoRoot ".venv/Scripts/python.exe"
if (Test-Path $venvPythonAfterInstall) {
    $venvCommand = @($venvPythonAfterInstall)
    if (Test-Python312 -Command $venvCommand) {
        $PythonCommand = $venvCommand
    }
}

if (-not $SkipNpm) {
    Write-Step "Installing Node dependencies"
    Invoke-CheckedCommandArray -Command @("npm") -Arguments @("ci")
}

if (-not $SkipPlaywright) {
    Write-Step "Installing Playwright Chromium"
    Invoke-CheckedCommandArray -Command $PythonCommand -Arguments @("-m", "playwright", "install", "chromium")
}

Write-Step "Preparing local runtime directories"
New-Item -ItemType Directory -Force -Path ".local/e2e-agent/gate-checkpoints" | Out-Null
New-Item -ItemType Directory -Force -Path ".local/e2e-agent/logs" | Out-Null

Write-Step "Running environment doctor"
Invoke-CheckedCommandArray -Command $PythonCommand -Arguments @("-m", "e2e_agent.commands.main", "doctor")

Write-Host ""
Write-Host "Bootstrap complete." -ForegroundColor Green
Write-Host ""
Write-Host "Next commands:"
Write-Host "  .\.venv\Scripts\e2e-agent.exe products"
Write-Host "  .\.venv\Scripts\e2e-agent.exe run --product-input products/travel-product/plan-a/product-input.json"
Write-Host "  .\.venv\Scripts\e2e-agent.exe reports serve --port 8080"
Write-Host ""
Write-Host "If e2e-agent is already on PATH, the equivalent CLI commands are:"
Write-Host "  e2e-agent products"
Write-Host "  e2e-agent run --product-input products/travel-product/plan-a/product-input.json"
Write-Host "  e2e-agent reports serve --port 8080"
