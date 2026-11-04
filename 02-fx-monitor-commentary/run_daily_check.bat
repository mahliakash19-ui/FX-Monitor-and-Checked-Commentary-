@echo off
rem Runs the daily FX check from this folder and appends the output to outputs\daily_check.log
cd /d "%~dp0"
if not exist outputs mkdir outputs
echo ==== %date% %time% ==== >> outputs\daily_check.log
python daily_check.py >> outputs\daily_check.log 2>&1
