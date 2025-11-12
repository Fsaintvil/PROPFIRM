Set-Location 'C:\Users\saint\Documents\PROPFIRM'
# Set required operational env flags to allow live sends
$Env:ALLOW_MT5_SEND = '1'
$Env:AUTO_APPLY = '1'
$Env:AUTO_DEPLOY = '1'
$Env:AUTO_LEARN = '1'
$Env:AUTO_ADAPT = '1'
$Env:AUTO_ENRICH = '1'
# Run the Python wrapper
& '.\.venv\Scripts\python.exe' '.\tmp\run_close_wrapper.py'
