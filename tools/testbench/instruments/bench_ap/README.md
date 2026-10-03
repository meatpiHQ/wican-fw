# bench_ap: the bench's "home WiFi" as an instrument (WiFi-to-USB adapter)

The WiCAN bench's home access point used to be the Pi's mt76 USB adapter
(`wican-bench` on `wtest0`). That adapter stalls as an AP (association status
stall, reason 204); the station then falls back to the ESPNetLink's AP and the
Pi cannot reach the DUT. It lost 13 of 17 sleep-matrix scenarios on 2026-10-01.

`bench_ap` replaces it with an **ESP32-P4-Function-EV board**: a WPA2 soft-AP on
the board's ESP32-C6 (esp_hosted / esp_wifi_remote) and a USB CDC-NCM network
device on the P4's high-speed OTG port, joined by a raw layer-2 forwarder (no IP
stack in between). The Pi shares 10.42.1.0/24 on the NCM interface exactly as it
did on the hotspot, so DHCP, mDNS, the broker and every bench script keep
working. Being firmware we own, the AP can also do what a hotspot never will:
drop a station, go away and come back, change channel, log every association.

## Tools and versions

- ESP32-P4-Function-EV board (the P4, not the P4x; ESP32-C6-MINI on board for WiFi).
  The C6 carries Espressif's esp_hosted slave firmware; the host component
  (`espressif/esp_hosted ~2`) checks the version at boot and says so if the
  slave needs an update.
- ESP-IDF v6.0.2, target `esp32p4`; managed components `espressif/esp_tinyusb ^2`,
  `espressif/esp_wifi_remote`, `espressif/esp_hosted` (see `main/idf_component.yml`).
- rpi001: esptool 5.x in the IDF python env (`~/.espressif/python_env/idf6.0_py3.11_env`),
  NetworkManager, dnsmasq (NM shared mode), pyserial.

## Wiring

- Board **USB-UART** port (CP2102N) -> rpi001 USB: power + console (`/dev/ttyUSB0`
  on 2026-10-02, 115200 baud). Flashing goes through this port.
- Board **USB OTG** (high-speed) port -> rpi001 USB: the CDC-NCM network link.
  Linux binds `cdc_ncm`; NetworkManager sees a new wired interface (`usb0` or `enx…`).
- Nothing on the RJ45 (the Ethernet variant of this instrument was dropped for the USB link).

## Host setup (rpi001)

```
# the shared network on the NCM interface (10.42.1.1/24 + dnsmasq), the same
# subnet the hotspot served; parks BOTH Pi hotspots (autoconnect off: the twin
# came back by itself otherwise), shims dnsmasq-wtest0.leases onto the NCM
# interface's file so every bench's discovery keeps working, and starts the
# console daemon
bash tools/testbench/pi/bench_ap_net.sh up      # detects the cdc_ncm interface
bash tools/testbench/pi/bench_ap_net.sh status
bash tools/testbench/pi/bench_ap_net.sh down    # back to the mt76 hotspot
```

The console must be held open by ONE process: every open of `/dev/ttyUSB0`
resets the board (the cp210x driver asserts DTR/RTS on open and the board's
auto-reset circuit fires, whatever pyserial is told), which drops the AP.
`bench_ap_daemon.py` opens it once (one reset), logs every console line with a
wall-clock stamp to `/tmp/bench_ap.console.log` (the association forensics the
sleep matrix lacked) and relays commands on 127.0.0.1:5590; `bench_ap_cli.py`
talks to it. Use `--direct` only for the very first bring-up.

The AP's credentials live in the board's NVS. Set them once so the DUT's stored
station settings keep working:

```
python tools/testbench/pi/bench_ap_cli.py "ap set WICAN_TEST_AP <the hotspot PSK>"
```

## Build and flash

```
# PC (native PowerShell). build.ps1 sets the IDF 6.0.2 environment for the P4
# and carries the traps: esp_wifi_remote picks its Kconfig by ESP_IDF_VERSION
# and wants "6.0" (with "6.0.2" it includes nothing and WIFI_INIT_CONFIG_DEFAULT
# fails on CONFIG_WIFI_RMT_*), this directory is deep enough to hit Windows'
# object-path limit (the build goes to C:\Users\Ali\idf_tmp\bap), and the P4
# needs the riscv32 toolchain on the PATH (the firmware's build.ps1 has xtensa).
powershell -NoProfile -ExecutionPolicy Bypass -File tools\testbench\instruments\bench_ap\build.ps1
# first time only: idf.py -B C:\Users\Ali\idf_tmp\bap set-target esp32p4
```

Flashing goes through the UART the console daemon holds open, so the daemon
stops first and `bench_ap_net.sh up` starts it again afterwards (its open
resets the board once more, expected):

```
ssh rpi001 'cp -a ~/bench_ap_build ~/bench_ap_build.prev; pkill -f "[b]ench_ap_daemon.py"'
python tools\testbench\instruments\bench_ap\flash_from_pi.py --build C:\Users\Ali\idf_tmp\bap
ssh rpi001 'bash ~/wican/tools/testbench/pi/bench_ap_net.sh up'
ssh rpi001 'cd ~/wican/tools/testbench/pi && python3 bench_ap_cli.py "ap"'   # stations, counters, heap
```

`~/bench_ap_build.prev` on the Pi is the roll-back image: `cd` into it and run
the esptool line `flash_from_pi.py` uses.

## Console commands (UART, `bench_ap>` prompt)

| Command | Does |
|---|---|
| `ap` | status: ssid, channel, on/off, stations (mac, rssi), frame counters, heap (`free`, `min`, `largest`), uptime |
| `ap on` / `ap off` | start / stop the soft-AP (the USB side stays up) |
| `ap kick <mac>` | deauthenticate one station |
| `ap channel <1..13>` | change channel (restarts the AP), stored |
| `ap set <ssid> <psk>` | store the credentials in NVS and apply them |
| `usb` | USB NCM link state and frame counters |
| `restart` | reboot the board |

Events: `BENCH_AP: +sta <mac> aid=<n>` and `BENCH_AP: -sta <mac> aid=<n> reason=<r>`
with the log timestamp; the bench parses them.

## Procedure and pass/fail

1. Flash, open the console: expect `usb ncm up`, `ap on: ssid=… channel=…`, `ready`.
   A warning from `esp_hosted` about the slave version means the C6 firmware
   needs updating before anything else is judged.
2. On the Pi: `ip link` shows the NCM interface; `bench_ap_net.sh up` gives it
   10.42.1.1/24 and dnsmasq; `nmcli con show --active` lists `wican-bench-usb`.
3. Join the AP with the Pi's `wtest1` as a client: it must get a 10.42.1.x lease
   from the Pi's dnsmasq (DHCP crossed the bridge), `ping 10.42.1.1` must answer
   (and `ap` must list wtest1's MAC with an RSSI). That is the pass bar for the
   instrument itself.
4. The DUT with its station on the same SSID/PSK must appear in the Pi's
   `dnsmasq-<ncm-if>.leases` and answer `/api/sleep`; the sleep bench and the
   Quick Setup bench then run unchanged (their discovery reads that leases file).
5. `ap off` must make the DUT's station leave (its console: disconnected), `ap on`
   must bring it back within ~10 s; `ap kick <mac>` must show `-sta … reason=` and
   a reconnect.

## Bring-up log, 2026-10-02

- Flashing: IDF 6 builds for P4 v3.x by default; this board is **chip revision
  v0.1** and the bootloader refused it. `CONFIG_ESP32P4_SELECTS_REV_LESS_V3=y` +
  `CONFIG_ESP32P4_REV_MIN_1=y` (a separate, mutually exclusive build).
- First boot asserted `esp_task_stack_is_sane_cache_disabled` in `nvs_flash_init`:
  the pre-3.0 memory layout hands the 8 KB TCM ("SPM") to the general heap and the
  6 KB main stack landed there. Main and console stacks are 8 KB now and app_main
  reserves the rest of the TCM (`tcm reserved: 6112 bytes`).
- esp_wifi_remote reads `ESP_IDF_VERSION` and wants `6.0`, not `6.0.2`; the deep
  project path hits Windows' object-path limit, so the build dir is short.
- `esp_read_mac(ESP_MAC_WIFI_SOFTAP)` fails on the P4 (no WiFi MAC in efuse); the
  NCM MAC came out as 02:00:00:00:00:55 on the first image. Fixed to derive from
  `ESP_MAC_BASE`; needs the next flash.
- **esp_hosted: `Version mismatch: Host [2.12.0] > Co-proc [0.0.0] ==> Upgrade co-proc`**.
  The C6's slave firmware is old. RPC worked for everything used here (set mode /
  config / start / stop, deauth, station list), but the host warns of RPC
  timeouts; upgrading the C6 is the next hardware task (esp_hosted ships a
  slave-OTA path).
- The DUT's station did not re-associate by itself after its old AP vanished: it
  sat with "STA connected: no" and no attempts for ~10 min, and joined the P4 AP
  on the next boot at once (connected in 3 s, DHCP through the bridge in 7 s,
  RSSI -43). Worth a look in wifi_manager's reconnect cadence; not an AP problem.
- Pass bar met: the Pi's `wtest1` as a client got 10.42.1.244 from the Pi's own
  dnsmasq through the bridge; the DUT got 10.42.1.194 and answered `/api/sleep`;
  `ap` listed it with rssi=-47; `ap kick` -> `-sta ... reason=4`, back in 10 s;
  `ap off` -> unreachable in 8 s, `ap on` -> back in 5 s. Frame counters moved
  both ways (usb->ap 132, ap->usb 116 at that point), zero drops.

## The forwarder leaked every frame (found and fixed 2026-10-02, evening)

Symptom: `assert failed: sdio_process_rx_task sdio_drv.c:1397 (copy_payload)`
and a software reset, nine times on its first day (09:33, 10:49, 13:19, 16:42,
22:03, 22:23, 22:28, 22:58, 23:05). After each reset the AP is back in 4 s with an empty
station table and the DUT stays in a zombie association (its console says
connected, nothing passes) until the AP is cycled (`ap off`, `ap on`). The
AutoPID matrix bench died in leg 5 every run, and the old C6 slave firmware
was the suspect.

Cause: the assert is esp_hosted failing to `malloc` the copy of a received
frame: the P4's heap was empty. `usb_tx_done(buffer, ctx)` freed its SECOND
argument. esp_tinyusb calls the callback as `(buff_free_arg, user_context)`:
the WiFi buffer is the first, the second was NULL, so `free(NULL)` and every
frame forwarded from a station to the Pi stayed on the heap. The board has
about 414 KB free after boot (no PSRAM configured), so it died after about
that much station traffic: hours on a quiet bench, 5 minutes into a matrix run.

Reproduction (old image): 20 downloads of the DUT's 16 KB log ring through the
AP, 327,680 bytes in 3 s, and the board asserted during the 21st.

Fix: the callback frees its first argument, and `ap_rx()` decides who owns a
buffer from a flag the callback clears, not from the return code of
`tinyusb_net_send_sync()` (a send that timed out may still have gone out; the
return code alone would free twice or never). `ap` prints the heap so a leak
shows long before an assert.

Proof (new image): 400 downloads, 6,553,600 bytes in 46 s, no reset, zero
drops, `heap: free 413900` before and `412844` after (`min 401868`).

Still open: the C6 slave firmware is old (`Host [2.12.0] > Co-proc [0.0.0]`),
and the DUT's zombie association after an AP that vanishes and returns (the
DUT's side: wifi_manager does not notice an AP that forgot it).
