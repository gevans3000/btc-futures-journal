param(
  [Parameter(ValueFromRemainingArguments=$true)]
  [string[]]$Args
)

$ErrorActionPreference="Stop"
if (-not $Args -or $Args.Count -eq 0) {
  Write-Host 'Usage: .\btc-update.ps1 "TP1 hit"'
  exit 2
}

python .\scripts\update.py ($Args -join " ")
