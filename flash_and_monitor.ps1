# Build, flash the image of THIS build (build\flash_args names it - the file
# is wican-fw_obd_pro_<git>.bin and changes with every commit), then monitor.
# Usage: .\flash_and_monitor.ps1 [-Port COM40]. Prefer build.ps1 -Flash for
# the full env; this one assumes the IDF PowerShell profile is loaded.
param([string]$Port = "COM40")
idf.py build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Push-Location build
python -m esptool --chip esp32s3 -p $Port -b 2000000 --before default-reset --after hard-reset write-flash "@flash_args"
Pop-Location
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
idf.py -p $Port monitor
