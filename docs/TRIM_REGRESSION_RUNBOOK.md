# Datalogger-Trim Device Regression Runbook (issue #5)

Run this checklist on hardware after **every phase** of `REFACTOR_PLAN.md`.
A phase is not done until every line passes. Bench device: WiCAN PRO at
`192.168.1.169` (mDNS `wican_dcb4d91511b9.local`), live MX-5 NC ECU attached.

## Build & flash

```powershell
# one-shot ESP-IDF env + build (export.ps1 venv gotcha: pin the py3.10 env)
$env:Path = "C:\Users\dufre\.espressif\python_env\idf5.5_py3.10_env\Scripts;" + $env:Path
$env:IDF_PYTHON_ENV_PATH = "C:\Users\dufre\.espressif\python_env\idf5.5_py3.10_env"
& "C:\esp\esp-idf-v5.5.3\export.ps1" *>$null
idf.py build
```

OTA (in-field update + rollback path — this IS the recovery drill):

```bash
curl -F "file=@build/wican-fw_obd_pro_<ver>.bin" http://192.168.1.169/upload/ota.bin
```

Keep the previous known-good `.bin` around before every OTA
(`rollback_trim_baseline.bin` = pre-trim wican-pro tip build).

## Checklist

- [ ] **Boots cleanly** — device answers `GET /check_status` within ~30 s of
      OTA; `git_version` matches the build you just flashed.
- [ ] **STA joins Wi-Fi** — `sta_status == "Connected"`, IP `192.168.1.169`.
      (AP comes up too: `ap_ssid` present in `/check_status`.)
- [ ] **Web UI loads at `/`** — only the tabs expected for the current phase
      render; browser console free of 404s for embedded assets.
- [ ] **Datalog trip** — protocol `poll_log` (or `auto_pid`) with the custom
      PID list; run ≥ 60 s; CSV lands on SD (`/csv_logger?op=list` newest
      entry grows); download it from the Files tab; it parses as CSV.
- [ ] **NC Flash dry-run (port 35001)** — the coexist port answers a
      version ping (`NCFRv6`+) and the desktop tool / bench tool connects and
      identifies the ECU **without** a device reboot. ⚠️ Routine regression
      uses **dry-run mode 'D' only** — `NCFW_ALLOW_LIVE=1` in this build, so
      mode 'L' performs a real ECU write.
- [ ] **`/datalog` lease intact** — `GET /datalog` returns the dead-man state
      JSON (pause/resume/claim ops still registered).
- [ ] **OTA rollback** — `POST /upload/ota.bin` accepts the previous build,
      device reboots into it (`git_version` flips back), then re-flash the
      current build. Dual-slot A/B must keep working at every phase.
- [ ] **Recovery AP** — 5-second button hold → safe-mode AP serves the OTA
      form. (Physical-access step: run when at the bench; not remotely
      testable.)
- [ ] **Event log** — `/event_log` shows the boot + OTA entries, no error
      spam.

## Un-brick guardrails (never trimmed — verify still present in source)

`safe_mode_check()` / `main/safemode.c`, `POST /upload/ota.bin`,
`main/multipart_upload.c`, recovery AP, dual OTA slots in both partition CSVs,
`main/slcan_port.c` (35001), `ncflash_fastread.c` / `ncflash_fastwrite.c`,
`/upload/sd/*`, `POST/GET /datalog` + `datalog_lease_task.c`,
`can.c` `FLASH_ACTIVE_BIT` interlock.
