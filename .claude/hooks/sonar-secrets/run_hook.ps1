# Resolve a Python 3 interpreter the way PRKS expects on native Windows:
# prefer python.exe, then py -3, and only then python3. Never require Node.
$ErrorActionPreference = 'Stop'
$hook = if ($args.Count -ge 1) { [string]$args[0] } else { '' }
$script = Join-Path $PSScriptRoot 'run_hook.py'

if (-not (Get-Command sonar -ErrorAction SilentlyContinue)) {
    exit 0
}

function Invoke-PrksPythonHook {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$PrefixArgs = @()
    )
    & $Executable @PrefixArgs $script $hook
    exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    Invoke-PrksPythonHook -Executable $python.Source
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    Invoke-PrksPythonHook -Executable $py.Source -PrefixArgs @('-3')
}

$python3 = Get-Command python3 -ErrorAction SilentlyContinue
if ($python3) {
    Invoke-PrksPythonHook -Executable $python3.Source
}

# No interpreter on PATH — same soft no-op as a missing sonar CLI.
exit 0
