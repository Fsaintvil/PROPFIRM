# Run both retry scripts with ALLOW_MT5_SEND=1 in this session
$env:ALLOW_MT5_SEND = '1'
python 'c:/Users/saint/Documents/PROPFIRM/tools/apply_sltp_retry_aggressive.py'
$rc1 = $LASTEXITCODE
if ($rc1 -ne 0) { Write-Warning "apply_sltp_retry_aggressive.py exited with $rc1" }
python 'c:/Users/saint/Documents/PROPFIRM/tools/apply_sltp_retry_stops_level.py'
$rc2 = $LASTEXITCODE
if ($rc2 -ne 0) { Write-Warning "apply_sltp_retry_stops_level.py exited with $rc2" }
exit ([int]($rc1 -bor $rc2))
