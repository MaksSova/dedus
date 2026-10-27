param([ValidateSet('console','preview','demo','poll','serve','check-token','subscribe')][string]$Mode='console')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m max_tutor $Mode
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m max_tutor $Mode
} else {
    throw 'Install Python 3.11+ and run: python -m max_tutor console'
}
exit $LASTEXITCODE
