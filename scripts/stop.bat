@echo off
rem Kills the LiveCaption host and any ASR worker it spawned.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'win_host\.py|hf_asr_worker\.py|sherpa_asr_worker\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo LiveCaption stopped.
