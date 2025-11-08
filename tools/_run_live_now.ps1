# Temp PowerShell runner to set MT5 creds in this process and run the live enrichment
# SECURITY: Do NOT hardcode credentials. Read MT5_* from environment or a secrets manager.

# Require MT5 credentials in environment
if (-not $env:MT5_LOGIN) {
	Write-Output "ERROR: MT5_LOGIN not set in environment. Aborting."
	exit 2
}
if (-not $env:MT5_PASSWORD) {
	Write-Output "ERROR: MT5_PASSWORD not set in environment. Aborting."
	exit 2
}
if (-not $env:MT5_SERVER) {
	Write-Output "WARNING: MT5_SERVER not set. Defaulting to 'FTMO-Demo'"
	$env:MT5_SERVER = 'FTMO-Demo'
}

# Run the Python live orchestrator: 1 iteration, confirm-live, quota 50/day, only weekdays
python -u scripts/online_live_learning.py --mode live --iterations 1 --confirm-live --orders-per-instrument 50 --only-weekdays
