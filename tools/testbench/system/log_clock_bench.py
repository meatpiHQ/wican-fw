#!/usr/bin/env python3
"""Log timestamps that survive the clock's history (issue #984, 2026-10-13):
the bench.

The claim: every row the logger writes is stamped in UTC epoch
milliseconds, a row queued before the clock was set or stepped is written
with the corrected stamp, the files are rotated when the clock becomes
valid or moves by a minute or more, the device writes down what its clock
did (`/sd/logs/clock.jsonl`, in-band markers where the format has room),
the relative formats never run backwards, and a file written before a
step can be re-based from the device's own record.

Rig (TESTING.md): the DUT on the bench AP through rpi001 (HTTP through an
ssh tunnel, `--dut localhost:18081`; test.ps1 opens it), the console
commands over the ws_cli bridge on the same tunnel, the SNTP actor
`tools/testbench/actors/sntp_server.py` on the Pi (UDP 123 as root, a
transient systemd unit; its offset is a number in /tmp/sntp_offset), the
OWON PSU on this PC for the power cycle of the unsynced-boot leg. The
DUT's `rtc_manager.ntp_server` points at the Pi for the run (one planned
restart to apply, one to restore); the logger runs params as jsonl + CAN
as mf4 for L1..L3 and params as binary for L4..L5 (one more restart).

Legs:
  L0  preflight: the firmware has the clock surfaces, the card is in, the
      settings staged (one restart), the journal has this boot's line
  L1  a sync through the real path: the actor at +0, POST /api/rtc/sync,
      the step measured (against the Pi's clock), journaled with the two
      open files, rotated iff a minute or more
  L2  a forward step of +1 h through SNTP: (a) rows drained before the
      step stay in the old file, which ends with the marker, the new file
      is named by the corrected clock; (b) rows QUEUED before the step and
      written after it carry corrected stamps (the writer's re-base); the
      export route never emits a marker as a point
  L3  a backward step of -2 h through the manual set with mf4 frames: the
      old mf4 parses with monotonic non-negative times and a header start
      the corrected clock agrees with; the new file is named newest + 1
  L4  the field case: `rtc --forget`, the actor down, a PSU power cycle:
      the boot has no clock (valid false, one W line, no E line), rows at
      1970 + uptime in a file of its own; the actor up and a sync: the
      journal's `valid` line (56 years, the files), the rotation, the
      chip rewritten
  L5  the tools: wdl_dump.py prints the 0x04 marker and passes --strict,
      log_clock.py re-bases the unsynced .wdl and the L2a .jsonl into the
      right windows
  L6  restore: settings as found (one restart), the actor stopped, no
      unexpected reset, no E line of data_logger / rtc_manager, the
      writer's stack headroom, the heap floor

  python tools/testbench/system/log_clock_bench.py [--dut localhost:18081]
      [--dut-ip 10.42.1.194] [--pi rpi001] [--pi-ip 10.42.1.1] [--psu auto|none]
      [--only L1,L2] [--logdir DIR]

Prints `LOG CLOCK PASS` (or `LOG CLOCK PARTIAL PASS (...)` with --only or
--psu none, which skips L4 and L5).
"""
import argparse
import calendar
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402
from ssh_channel import SshChannel  # noqa: E402
from wsmin import WS, OP_TEXT  # noqa: E402

# the public tools live in wican-fw/tools (WICAN_FW_PATH or the sibling layout)
_HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = None
for _cand in ([os.path.join(os.environ["WICAN_FW_PATH"], "tools")]
              if os.environ.get("WICAN_FW_PATH") else []) + [
        os.path.join(_HERE, "..", "..")]:
    if os.path.isfile(os.path.join(_cand, "wdl_dump.py")):
        TOOLS = os.path.abspath(_cand)
        sys.path.insert(0, TOOLS)
        break
import wdl_dump  # noqa: E402

OWN_TAGS = ("data_logger", "rtc_manager")
LEGS = ["L0", "L1", "L2", "L3", "L4", "L5", "L6"]
LOG_DIR = "/sd/logs"
JOURNAL = LOG_DIR + "/clock.jsonl"
HOUR = 3600000
ROTATE_MS = 60000
OFFSET_FILE = "/tmp/sntp_offset"
UNIT = "sntp_bench"


def raw(base, path, timeout=60):
    """(status, bytes) of one GET."""
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e).encode()


def cli(hostport, cmd, marker, timeout=90):
    """One console command over /ws/cli; the output up to `marker`."""
    host, _, port = hostport.partition(":")
    ws = WS(host, int(port or 80), "/ws/cli", timeout=10)
    try:
        ws.send((cmd + "\n").encode(), opcode=OP_TEXT)
        out = ""
        end = time.time() + timeout
        while time.time() < end:
            try:
                op, payload = ws.recv_frame()
            except Exception:
                continue
            if op in (OP_TEXT, 2):
                out += payload.decode("utf-8", "replace")
                if marker in out:
                    return out
        raise pcbench.Bench(f"cli '{cmd}': no '{marker}' in: {out[-200:]}")
    finally:
        try:
            ws.s.close()
        except Exception:
            pass


def parse_mf4(blob):
    """Minimal spec-offset MDF4 reader: the header start (ms) and the
    records as (t_seconds, id, data)."""
    assert blob[:8] == b"MDF     ", "bad MDF magic"
    assert blob[64:68] == b"##HD", "no HD block at 64"

    def u64(off):
        return struct.unpack_from("<Q", blob, off)[0]

    start_ms = u64(64 + 24 + 48) // 1000000
    dg = u64(64 + 24)
    cg = u64(dg + 24 + 8)
    dt = u64(dg + 24 + 16)
    assert blob[cg:cg + 4] == b"##CG" and blob[dt:dt + 4] == b"##DT"
    cycles = u64(cg + 24 + 48 + 8)
    assert u64(dt + 8) == 24 + 22 * cycles, "DT length vs cycle count"
    out = []
    off = dt + 24
    for _ in range(cycles):
        t, fid, flags, dlc, data = struct.unpack_from("<dIBB8s", blob, off)
        out.append((t, fid, data[:dlc]))
        off += 22
    return start_ms, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="", help="host:port of the DUT's API "
                    "(a tunnel); default: open one to --dut-ip through --pi")
    ap.add_argument("--dut-ip", default="10.42.1.194")
    ap.add_argument("--pi", default="rpi001")
    ap.add_argument("--pi-ip", default="10.42.1.1",
                    help="the Pi's address on the DUT's network (the NTP server)")
    ap.add_argument("--psu", default="auto", help="the OWON's port, or none")
    ap.add_argument("--only", default="", help="comma list of legs")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()

    only = [x.strip() for x in a.only.split(",") if x.strip()]
    legs = [l for l in LEGS if not only or l in only or l in ("L0", "L6")]
    partial = []
    if only:
        partial.append("legs: " + " ".join(legs))
    if a.psu == "none":
        legs = [l for l in legs if l not in ("L4", "L5")]
        partial.append("no PSU: L4 and L5 skipped")
    logdir = a.logdir or os.path.join(
        _HERE, "..", "..", "..", "test-reports", "logs",
        time.strftime("log_clock_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)
    run = pcbench.Run("LOG CLOCK")

    tunnel = None
    hostport = a.dut
    if not hostport:
        hostport = "localhost:18081"
        tunnel = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-N", "-L",
                                   f"18081:{a.dut_ip}:80", a.pi])
        time.sleep(2)
    dut = pcbench.Dut(hostport, own_tags=OWN_TAGS, tasks=("dl_writer", "rtc_sntp"))
    base = dut.base
    pi = SshChannel(a.pi)
    psu = None

    def save(name, data):
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(os.path.join(logdir, name), mode) as f:
            f.write(data)

    def say(msg):
        print(msg, flush=True)

    # ---- the Pi side: the actor and its offset -----------------------------------
    def actor_up(offset_s):
        rc, out = pi.run(f"printf '%s' '{int(offset_s)}' > {OFFSET_FILE}; "
                         f"systemctl is-active {UNIT}")
        if out.strip() != "active":
            pi.put("/tmp/sntp_server.py", open(os.path.join(
                _HERE, "..", "actors", "sntp_server.py"), encoding="utf-8").read())
            rc, out = pi.run(f"sudo -n systemctl stop {UNIT} 2>/dev/null; "
                             f"sudo -n systemd-run --unit={UNIT} --collect "
                             f"python3 -u /tmp/sntp_server.py --offset-file {OFFSET_FILE}")
            for _ in range(10):
                rc, out = pi.run(f"systemctl is-active {UNIT}")
                if out.strip() == "active":
                    break
                time.sleep(0.5)
            if out.strip() != "active":
                rc, j = pi.run(f"sudo -n journalctl -u {UNIT} --no-pager -n 5")
                raise pcbench.Bench(f"the SNTP actor did not start: {j[-300:]}")
        say(f"actor: up at {int(offset_s):+d} s")

    def actor_offset(offset_s):
        pi.run(f"printf '%s' '{int(offset_s)}' > {OFFSET_FILE}")
        say(f"actor: offset {int(offset_s):+d} s")

    def actor_down():
        pi.run(f"sudo -n systemctl stop {UNIT} 2>/dev/null; true")
        say("actor: down")

    def actor_answers():
        rc, out = pi.run(f"sudo -n journalctl -u {UNIT} --no-pager -n 400 | grep -c 'SNTP [0-9]'")
        try:
            return int(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return 0

    def pi_ms():
        rc, out = pi.run("date +%s%3N")
        return int(out.strip().splitlines()[-1])

    # ---- the DUT side -------------------------------------------------------------
    def rtc():
        return dut.get("/api/rtc")

    def logger():
        return dut.get("/api/logger")

    def sync(expect_ok=True):
        code, r = dut.api("/api/rtc/sync", "POST", {}, timeout=45)
        if expect_ok and code != 200:
            raise pcbench.Bench(f"POST /api/rtc/sync: {code} {r}")
        return code, r

    def manual_set(epoch_s):
        code, r = dut.api("/api/rtc", "POST", {"epoch": int(epoch_s)}, timeout=15)
        if code != 200:
            raise pcbench.Bench(f"POST /api/rtc: {code} {r}")
        return r

    def dut_ms():
        t = rtc().get("time", "")
        return calendar.timegm(time.strptime(t, "%Y-%m-%dT%H:%M:%SZ")) * 1000

    def gate(on):
        code, r = dut.api("/api/logger/gate", "POST", {"enabled": on})
        if code != 200:
            raise pcbench.Bench(f"gate {on}: {code} {r}")
        time.sleep(0.8)

    def download(path, tries=6):
        """A closed file. The file being written is locked (409): the
        caller gates the logger around the read (download_active)."""
        last = None
        for _ in range(tries):
            code, blob = raw(base, "/api/fs/download?path=" + path)
            if code == 200:
                return blob
            last = (code, blob[:120])
            if code == 409:
                break
            time.sleep(1.5)
        raise pcbench.Bench(f"download {path}: {last}")

    def download_active(path):
        wait_drained()
        gate(False)
        try:
            return download(path)
        finally:
            gate(True)

    def journal():
        events = []
        for ln in download(JOURNAL).decode("utf-8", "replace").splitlines():
            try:
                events.append(json.loads(ln))
            except ValueError:
                continue
        return events

    def log_files():
        r = dut.get("/api/fs/list?path=" + LOG_DIR)
        return sorted(e["name"] for e in r.get("entries", []) if not e.get("dir"))

    def wait_drained(secs=20):
        end = time.time() + secs
        calm = 0
        while time.time() < end and calm < 2:
            st = logger()
            calm = calm + 1 if (st["queued"] <= 0 and st["can"]["queued"] <= 0) else 0
            time.sleep(0.8)
        time.sleep(0.5)

    def wait_written(key, target, secs=30):
        end = time.time() + secs
        while time.time() < end:
            st = logger()
            v = st["can"]["frames_written"] if key == "frames" else st["written"]
            if v >= target:
                return v
            time.sleep(0.7)
        return v

    def jsonl_rows(blob):
        rows, markers = [], []
        for ln in blob.decode("utf-8", "replace").splitlines():
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            (markers if o.get("event") == "clock_step" else rows).append(o)
        return rows, markers

    def last_line_is_marker(blob):
        lines = [ln for ln in blob.decode("utf-8", "replace").splitlines() if ln]
        return bool(lines) and '"event":"clock_step"' in lines[-1]

    def name_epoch(name):
        return int(name.split("_")[1].split(".")[0])

    def named_by_clock(name, ts_after_ms, names_before):
        """A new file is named by the corrected clock, unless the card holds
        a file named later than that (a clock that ran ahead earlier, this
        bench's own +2 h legs): then the monotonic rule names it newest + 1
        (the order on the card stays the order of writing)."""
        prefix = name.split("_")[0] + "_"
        newest = max((name_epoch(n) for n in names_before
                      if n.startswith(prefix) and re.match(r"^\w+_\d{10}\.", n)), default=0)
        want = max(ts_after_ms // 1000, newest + 1)
        return abs(name_epoch(name) - want) <= 5

    def wait_boot(b0, limit=180):
        """After a planned restart (b != b0) or a power cycle (boot 1)."""
        t0 = time.time()
        time.sleep(3)
        while time.time() - t0 < limit:
            b = dut.boot_count()
            if b is not None and b != b0:
                return time.time() - t0
            time.sleep(1)
        raise pcbench.Bench("the DUT did not come back within 180 s" + pcbench.GONE_HINT)

    def restart_with(changes):
        dut.restart(changes)
        time.sleep(6)   # the writer's first lap, the station, the card

    found = {}
    st0 = None
    base_st = None      # the baseline of the closing reset check
    exit_code = 1
    files_l2a = {}      # the L2a files for L5
    try:
        # ---- L0 ----------------------------------------------------------------
        info = dut.get("/api/info")
        st0 = dut.get("/api/status")
        dut.as_found()
        say(f"DUT {info.get('device_id')} {info.get('fw_version')}")
        r0 = rtc()
        if "clock" not in r0:
            raise pcbench.Bench("the firmware has no clock surfaces on /api/rtc: an old image")
        run.check("L0_card_mounted", st0.get("bits", {}).get("sdcard_mounted") is True)
        found["data_logger"] = dut.settings("data_logger")
        found["rtc_manager"] = dut.settings("rtc_manager")
        save("l0_found_settings.json", json.dumps(found, indent=1))
        run.check("L0_clock_valid_as_found", r0.get("valid") is True
                  and r0["clock"].get("steps_this_boot", 0) >= 1, str(r0.get("clock")))
        restart_with({"data_logger": {"enabled": True, "format": "jsonl", "autopid_log": "off",
                                      "can_log": True, "can_format": "mf4", "flush_ms": 2000,
                                      "batch_rows": 256},
                      "rtc_manager": {"enabled": True, "sntp": True, "ntp_server": a.pi_ip,
                                      "ntp_server2": ""}})
        base_st = dut.get("/api/status")
        lg = logger()
        run.check("L0_logger_running", lg.get("running") and lg.get("storage_ok")
                  and lg["file"].endswith(".jsonl") and lg["can"]["file"].endswith(".mf4"),
                  f"{lg.get('file')} {lg['can'].get('file')} storage_ok={lg.get('storage_ok')}")
        run.check("L0_logger_clock_block", "clock" in lg and lg["clock"].get("valid") is True
                  and lg["clock"].get("journal") == JOURNAL, str(lg.get("clock")))
        boot = base_st.get("boot_count")
        ev = journal()
        save("l0_journal.jsonl", "\n".join(json.dumps(e) for e in ev))
        boots = [e for e in ev if e.get("ev") == "boot" and e.get("boot") == boot]
        run.check("L0_journal_has_this_boot", bool(boots) and boots[-1].get("valid") is True
                  and boots[-1].get("source") in ("rtc", "sntp", "manual"), str(boots[-1:]))
        gen0 = rtc()["clock"]["generation"]

        # ---- L1 the real path --------------------------------------------------------
        if "L1" in legs:
            actor_up(0)
            expect = pi_ms() - dut_ms()
            n0 = actor_answers()
            lg0 = logger()
            files0 = [lg0["file"], lg0["can"]["file"]]
            t0 = time.time()
            code, r = sync()
            run.metric("L1_sync_s", round(time.time() - t0, 1))
            c = r["clock"]
            run.check("L1_sync_answered_200", code == 200 and r.get("valid") is True)
            run.check("L1_actor_answered", actor_answers() > n0, f"{n0} -> {actor_answers()}")
            run.check("L1_step_source_sntp", c.get("source") == "sntp"
                      and c["last_step"]["source"] == "sntp", str(c))
            run.check("L1_generation_moved", c["generation"] > gen0, f"{gen0} -> {c['generation']}")
            delta = c["last_step"]["delta_ms"]
            run.metric("L1_delta_ms", delta)
            run.check("L1_delta_is_the_pi_minus_dut", abs(delta - expect) < 3000,
                      f"delta {delta}, expected {expect} (Pi - DUT before the sync)")
            run.check("L1_last_sync_set", r["sntp"].get("last_sync") not in (None, "null"))
            time.sleep(3)
            ev = journal()
            steps = [e for e in ev if e.get("ev") in ("valid", "step") and e.get("boot") == boot]
            s = steps[-1] if steps else {}
            run.check("L1_journal_step_line", bool(s) and s.get("source") == "sntp"
                      and abs(s.get("delta_ms", 0) - delta) < 2
                      and sorted(s.get("files", [])) == sorted(files0), str(s)[:300])
            lg1 = logger()
            rotated = lg1["clock"]["rotations_by_clock"]
            run.check("L1_rotated_iff_a_minute_or_more",
                      (rotated >= 2) == (abs(delta) >= ROTATE_MS)
                      and ((lg1["file"] != files0[0]) == (abs(delta) >= ROTATE_MS)),
                      f"|delta| {abs(delta)} ms, rotations_by_clock {rotated}, file {lg1['file']}")
            gen0 = c["generation"]

        # ---- L2 a forward step through SNTP ----------------------------------------
        if "L2" in legs:
            # (a) rows drained BEFORE the step: the old file keeps them, ends with the marker
            lg = logger()
            old = lg["file"]
            w0 = lg["written"]
            cli(hostport, "logger test 100", "queued 100/100")
            run.check("L2a_rows_landed", wait_written("rows", w0 + 100) >= w0 + 100)
            wait_drained()
            names_l2a = log_files()
            actor_offset(3600)
            tb = dut_ms()
            code, r = sync()
            c = r["clock"]
            delta = c["last_step"]["delta_ms"]
            run.metric("L2a_delta_ms", delta)
            run.check("L2a_step_is_plus_one_hour", abs(delta - HOUR) < 3000, str(delta))
            time.sleep(4)
            lg = logger()
            new = lg["file"]
            run.check("L2a_rotated_into_a_new_file", new != old and lg["clock"]["rotations_by_clock"] >= 2,
                      f"{old} -> {new}")
            ev = journal()
            s = [e for e in ev if e.get("ev") == "step" and e.get("boot") == boot][-1]
            rot = [e for e in ev if e.get("ev") == "rotate" and e.get("from") == old]
            run.check("L2a_journal_step_and_rotate", s.get("source") == "sntp" and old in s.get("files", [])
                      and bool(rot) and rot[-1].get("to") == new and rot[-1].get("why") == "clock",
                      f"{str(s)[:200]} | {rot[-1:]}")
            new_epoch = int(new.split("_")[1].split(".")[0])
            run.check("L2a_new_name_is_the_corrected_clock",
                      named_by_clock(new, s["ts_after"], names_l2a), f"{new} vs ts_after {s['ts_after']}")
            blob = download(LOG_DIR + "/" + old)
            save("l2a_" + old, blob)
            rows, markers = jsonl_rows(blob)
            run.check("L2a_old_file_ends_with_the_marker", last_line_is_marker(blob)
                      and len(markers) >= 1 and markers[-1]["delta_ms"] == delta
                      and markers[-1]["ts"] == s["ts_after"] and markers[-1]["source"] == "sntp",
                      str(markers[-1:]))
            run.check("L2a_old_rows_in_the_old_frame",
                      len(rows) >= 100 and all(r_["ts"] <= s["ts_before"] + 2000 for r_ in rows[-100:]),
                      f"{len(rows)} rows, last ts {rows[-1]['ts'] if rows else None} vs ts_before {s['ts_before']}")
            files_l2a = {"old": old, "step": s}
            # (b) rows QUEUED before the step, written after it: the re-base
            gate(False)
            w0 = logger()["written"]
            cli(hostport, "logger test 100", "queued 100/100")
            actor_offset(7200)
            code, r = sync()
            c2 = r["clock"]
            delta2 = c2["last_step"]["delta_ms"]
            run.check("L2b_second_step_plus_one_hour", abs(delta2 - HOUR) < 3000, str(delta2))
            gate(True)
            run.check("L2b_rows_landed_after_the_step", wait_written("rows", w0 + 100) >= w0 + 100)
            wait_drained()
            lg = logger()
            newest = lg["file"]
            ev = journal()
            s2 = [e for e in ev if e.get("ev") == "step" and e.get("boot") == boot][-1]
            run.check("L2b_rotated_again", newest != new and new in s2.get("files", []), f"{new} -> {newest}")
            blob = download_active(LOG_DIR + "/" + newest)   # the file being written
            rows, _ = jsonl_rows(blob)
            save("l2b_" + newest, blob)
            inside = [r_ for r_ in rows if s2["ts_after"] - 8000 <= r_["ts"] <= s2["ts_after"] + 30000]
            run.check("L2b_queued_rows_carry_corrected_stamps",
                      len(rows) >= 100 and len(inside) >= 100,
                      f"{len(inside)} of {len(rows)} rows within 8 s before / 30 s after ts_after {s2['ts_after']}")
            blob = download(LOG_DIR + "/" + new)
            run.check("L2b_middle_file_ends_with_its_marker", last_line_is_marker(blob))
            code, txt = raw(base, "/api/logger/export?stream=params&since=0:0&limit=2000")
            run.check("L2_export_skips_markers", code == 200 and b"clock_step" not in txt
                      and b'"_cursor"' in txt, f"{code} {len(txt)} B")
            gen0 = c2["generation"]

        # ---- L3 a backward step through the manual set, mf4 frames ------------------
        if "L3" in legs:
            lg = logger()
            old_mf4 = lg["can"]["file"]
            f0 = lg["can"]["frames_written"]
            cli(hostport, "logger frametest 300", "queued 300/300")
            run.check("L3_frames_landed", wait_written("frames", f0 + 300) >= f0 + 300)
            wait_drained()
            t_frames = dut_ms()     # the frames are on the card by now (the old clock)
            target = dut_ms() // 1000 - 7200
            r = manual_set(target)
            c = r["clock"]
            delta = c["last_step"]["delta_ms"]
            run.metric("L3_delta_ms", delta)
            run.check("L3_manual_step_minus_two_hours", c["last_step"]["source"] == "manual"
                      and abs(delta + 7200000) < 3000, str(c["last_step"]))
            time.sleep(4)
            lg = logger()
            new_mf4 = lg["can"]["file"]
            run.check("L3_mf4_rotated", new_mf4 != old_mf4, f"{old_mf4} -> {new_mf4}")
            oe, ne = int(old_mf4.split("_")[1].split(".")[0]), int(new_mf4.split("_")[1].split(".")[0])
            run.check("L3_backward_step_keeps_names_monotonic", ne == oe + 1, f"{oe} -> {ne}")
            f0 = lg["can"]["frames_written"]
            cli(hostport, "logger frametest 300", "queued 300/300")
            run.check("L3_frames_landed_after_the_step", wait_written("frames", f0 + 300) >= f0 + 300)
            wait_drained()
            blob = download(LOG_DIR + "/" + old_mf4)
            save("l3_" + old_mf4, blob)
            try:
                start_ms, recs = parse_mf4(blob)
                ts = [t for t, _, _ in recs]
                mono = all(b >= a_ for a_, b in zip(ts, ts[1:]))
                run.check("L3_old_mf4_relative_times_monotonic_nonnegative",
                          len(recs) >= 300 and mono and ts[0] >= 0 and ts[-1] < 600,
                          f"{len(recs)} records, t {ts[0] if ts else None}..{ts[-1] if ts else None}")
                # the last frame, placed by the header's start: on the corrected
                # clock it sits where the frames were written, two hours before
                # the old clock said (a start left on the old clock would miss by 2 h)
                last_ms = start_ms + int(ts[-1] * 1000) if ts else 0
                want = t_frames + delta
                run.check("L3_old_mf4_header_start_follows_the_corrected_clock",
                          abs(last_ms - want) < 30000,
                          f"start {start_ms} + last t {ts[-1] if ts else None} s = {last_ms} vs the frames at "
                          f"{t_frames} + delta {delta} = {want}")
            except AssertionError as e:
                run.check("L3_old_mf4_parses", False, str(e))
            ev = journal()
            s3 = [e for e in ev if e.get("ev") == "step" and e.get("boot") == boot][-1]
            run.check("L3_journal_manual_step", s3.get("source") == "manual" and old_mf4 in s3.get("files", [])
                      and abs(s3.get("delta_ms", 0) - delta) < 2, str(s3)[:200])
            try:
                from asammdf import MDF  # noqa: F401
                with tempfile.NamedTemporaryFile(suffix=".mf4", delete=False) as f:
                    f.write(blob)
                    p = f.name
                with MDF(p) as m:
                    sig = m.get("CAN_DataFrame.ID")
                    run.check("L3_asammdf_reads_the_old_mf4", len(sig.samples) >= 300, str(len(sig.samples)))
                os.unlink(p)
            except ImportError:
                say("L3: asammdf not installed: the spec-offset parser is the cross-check")
            except Exception as e:  # noqa: BLE001
                run.check("L3_asammdf_reads_the_old_mf4", False, str(e)[:200])
            actor_offset(0)
            sync()
            gen0 = rtc()["clock"]["generation"]

        # ---- L4 the field case: an unsynced boot --------------------------------------
        if "L4" in legs:
            import bench_ports
            import owon_psu
            psu = owon_psu.OwonPsu(bench_ports.resolve(a.psu, "psu", "COM50"))
            try:
                psu.meas_current()
            except (AssertionError, ValueError):
                pass
            restart_with({"data_logger": {"format": "binary", "can_log": False}})
            actor_down()
            out = cli(hostport, "rtc --forget", "OK")
            run.check("L4_forget_accepted", "2000-01-01" in out, out[-160:])
            names_before = log_files()
            say("L4: PSU power cycle (output off 4 s)")
            psu.output(False)
            time.sleep(4)
            psu.output(True)
            took = wait_boot(None)
            run.metric("L4_boot_s", round(took, 1))
            time.sleep(8)
            base_st = dut.get("/api/status")      # a fresh power session: the new baseline
            dut.restarts = 0
            dut.unexpected = None
            dut.old_resets = set()
            dut.reset_notes = []
            dut.ring0 = set()
            r4 = rtc()
            run.check("L4_boot_without_a_clock", r4.get("valid") is False
                      and r4["clock"]["source"] == "none" and r4["clock"]["steps_this_boot"] == 0
                      and r4.get("rtc") in (None, "null"), str({k: r4.get(k) for k in ("valid", "rtc", "clock")}))
            ring = dut.ring_lines()
            run.check("L4_one_W_line_no_E_line",
                      any("rtc_manager" in ln and "implausible" in ln and ln.startswith("W (") for ln in ring)
                      and not any(ln.startswith("E (") and ("rtc_manager" in ln or "data_logger" in ln) for ln in ring),
                      " | ".join(ln for ln in ring if "rtc_manager" in ln)[:300])
            lg = logger()
            uf = lg["file"]
            run.check("L4_logger_runs_without_a_clock", lg.get("running") and lg.get("storage_ok")
                      and uf.endswith(".wdl") and lg["clock"]["valid"] is False, f"{uf} {lg.get('clock')}")
            newest_before = max((n for n in names_before if n.startswith("dl_")), default="")
            run.check("L4_unsynced_file_is_its_own_newest_plus_one",
                      uf not in names_before and uf > newest_before, f"{uf} after {newest_before}")
            boot4 = base_st.get("boot_count")
            ev = journal()
            b4 = [e for e in ev if e.get("ev") == "boot" and e.get("boot") == boot4]
            run.check("L4_journal_boot_line_unsynced", bool(b4) and b4[-1].get("valid") is False
                      and b4[-1].get("source") == "none" and b4[-1].get("ts", 10**13) < 10**12, str(b4[-1:]))
            w0 = lg["written"]
            cli(hostport, "logger test 100", "queued 100/100")
            run.check("L4_rows_landed_unsynced", wait_written("rows", w0 + 100) >= w0 + 100)
            wait_drained()
            actor_up(0)
            tb = pi_ms()
            code, r = sync()
            c = r["clock"]
            delta = c["last_step"]["delta_ms"]
            run.metric("L4_delta_years", round(delta / 31557600000, 1))
            run.check("L4_sync_makes_the_clock_valid", r.get("valid") is True and c["source"] == "sntp"
                      and delta > 1_700_000_000_000, str(c))
            time.sleep(4)
            ev = journal()
            v = [e for e in ev if e.get("ev") == "valid" and e.get("boot") == boot4]
            run.check("L4_journal_valid_line", bool(v) and v[-1].get("delta_ms", 0) > 1_700_000_000_000
                      and uf in v[-1].get("files", []) and v[-1].get("ts_before", 10**13) < 10**12,
                      str(v[-1:])[:300])
            lg = logger()
            nf = lg["file"]
            run.check("L4_rotated_into_a_file_named_now", nf != uf
                      and named_by_clock(nf, v[-1]["ts_after"], names_before + [uf])
                      and lg["clock"]["rotations_by_clock"] >= 1, f"{uf} -> {nf}")
            out = cli(hostport, "rtc -r", "OK")
            run.check("L4_chip_rewritten", "2026-" in out or "202" in out.split("OK")[0], out[-120:])
            blob = download(LOG_DIR + "/" + uf)
            save("l4_" + uf, blob)
            with tempfile.NamedTemporaryFile(suffix=".wdl", delete=False) as f:
                f.write(blob)
                uf_path = f.name
            recs = list(wdl_dump.decode(uf_path, strict=True))
            params = [x for x in recs if x[0] == "param"]
            run.check("L4_unsynced_rows_at_1970", len(params) >= 100
                      and all(x[1] < 10**12 for x in params[-100:]),
                      f"{len(params)} rows, last ts {params[-1][1] if params else None}")
            run.check("L4_wdl_ends_with_the_0x04_marker", bool(recs) and recs[-1][0] == "clock"
                      and recs[-1][2] == delta and recs[-1][3] == "sntp", str(recs[-1:]))
            found["l4"] = {"uf": uf, "uf_path": uf_path, "valid": v[-1] if v else None}

        # ---- L5 the tools ----------------------------------------------------------------
        if "L5" in legs and found.get("l4"):
            py = sys.executable
            jpath = os.path.join(logdir, "clock.jsonl")
            save("clock.jsonl", download(JOURNAL))
            uf_path = found["l4"]["uf_path"]
            uf_name = found["l4"]["uf"]
            # the tool wants the device's file name: a copy under it
            named = os.path.join(logdir, uf_name)
            with open(uf_path, "rb") as f:
                with open(named, "wb") as g:
                    g.write(f.read())
            p = subprocess.run([py, os.path.join(TOOLS, "wdl_dump.py"), named, "--strict"],
                               capture_output=True, text=True, timeout=60)
            run.check("L5_wdl_dump_strict_passes_and_prints_the_marker",
                      p.returncode == 0 and "clock step" in p.stdout and "1 clock step" in p.stdout,
                      (p.stdout[-200:] + p.stderr[-200:]).replace("\n", " | "))
            p = subprocess.run([py, os.path.join(TOOLS, "log_clock.py"), "--journal", jpath,
                                "--rebase", named], capture_output=True, text=True, timeout=60)
            reb = os.path.splitext(named)[0] + ".rebased.wdl"
            ok = p.returncode == 0 and os.path.isfile(reb)
            run.check("L5_rebase_tool_ran", ok, (p.stdout + p.stderr)[-300:].replace("\n", " | "))
            if ok:
                after = found["l4"]["valid"]["ts_after"]
                rows = [x for x in wdl_dump.decode(reb, strict=True) if x[0] == "param"]
                inside = [x for x in rows if after - 900000 <= x[1] <= after + 2000]
                run.check("L5_rebased_unsynced_rows_land_before_the_sync",
                          len(rows) >= 100 and len(inside) == len(rows),
                          f"{len(inside)} of {len(rows)} within 15 min before ts_after {after}")
            p = subprocess.run([py, os.path.join(TOOLS, "log_clock.py"), "--show", jpath],
                               capture_output=True, text=True, timeout=60)
            run.check("L5_show_prints_the_table", p.returncode == 0 and "valid" in p.stdout
                      and "rotate" in p.stdout, p.stdout[-200:].replace("\n", " | "))
            if files_l2a:
                old = files_l2a["old"]
                s = files_l2a["step"]
                named = os.path.join(logdir, old)
                with open(os.path.join(logdir, "l2a_" + old), "rb") as f:
                    with open(named, "wb") as g:
                        g.write(f.read())
                p = subprocess.run([py, os.path.join(TOOLS, "log_clock.py"), "--journal", jpath,
                                    "--rebase", named], capture_output=True, text=True, timeout=60)
                reb = os.path.splitext(named)[0] + ".rebased.jsonl"
                if p.returncode == 0 and os.path.isfile(reb):
                    rows, markers = jsonl_rows(open(reb, "rb").read())
                    inside = [r_ for r_ in rows[-100:] if s["ts_after"] - 60000 <= r_["ts"] <= s["ts_after"] + 2000]
                    run.check("L5_rebased_l2a_rows_land_at_the_corrected_time",
                              len(inside) == 100 and not markers,
                              f"{len(inside)} of {len(rows[-100:])} within a minute before ts_after {s['ts_after']}")
                else:
                    run.check("L5_rebase_l2a_ran", False, (p.stdout + p.stderr)[-300:])

        # ---- L6 restore -------------------------------------------------------------------
        if psu is not None:
            psu.close()
        try:
            actor_up(0)
            sync(expect_ok=False)   # leave the DUT on the Pi's clock
        except pcbench.Bench as e:
            say(f"L6: last sync skipped: {e}")
        actor_down()
        pi.run(f"rm -f {OFFSET_FILE}; true")
        changes = {}
        for comp in ("data_logger", "rtc_manager"):
            if dut.settings(comp) != found[comp]:
                changes[comp] = found[comp]
        if changes:
            dut.restart(changes)
            time.sleep(4)
        run.check("L6_settings_as_found", all(dut.settings(c) == found[c] for c in ("data_logger", "rtc_manager")))
        dut.reset_check(run, base_st if base_st is not None else st0)
        dut.passing_checks(run)
        exit_code = run.verdict("; ".join(partial))
    except pcbench.Bench as e:
        print("BENCH:", e, flush=True)
        try:
            actor_down()
        except Exception:
            pass
        exit_code = 2
    finally:
        pi.close()
        if tunnel is not None:
            tunnel.terminate()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
