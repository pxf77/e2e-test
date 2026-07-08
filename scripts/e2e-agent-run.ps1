param(
    [Parameter(Mandatory = $true)]
    [string]$ProductInput,

    [switch]$VisibleAgent4,
    [switch]$DisableAgent3Live
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

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
    throw "Python 3.12+ is required. Run .\scripts\bootstrap.ps1 first, or set E2E_AGENT_PYTHON."
}

$env:E2E_AGENT_GATE_CHECKPOINT_DIR = if ($env:E2E_AGENT_GATE_CHECKPOINT_DIR) {
    $env:E2E_AGENT_GATE_CHECKPOINT_DIR
} else {
    Join-Path $RepoRoot ".local/e2e-agent/gate-checkpoints"
}
$env:AGENT3_HEADLESS = if ($env:AGENT3_HEADLESS) { $env:AGENT3_HEADLESS } else { "1" }
$env:PLAYWRIGHT_HTML_OPEN = if ($env:PLAYWRIGHT_HTML_OPEN) { $env:PLAYWRIGHT_HTML_OPEN } else { "never" }

if ($VisibleAgent4) {
    $env:AGENT4_VISIBLE_BROWSER = "1"
}
if ($DisableAgent3Live) {
    $env:AGENT3_DISABLE_LIVE = "1"
}

New-Item -ItemType Directory -Force -Path $env:E2E_AGENT_GATE_CHECKPOINT_DIR | Out-Null

$PythonCommand = Resolve-Python312
Invoke-CheckedCommandArray -Command $PythonCommand -Arguments @("-m", "e2e_agent.cli", "run", "--product-input", $ProductInput)
