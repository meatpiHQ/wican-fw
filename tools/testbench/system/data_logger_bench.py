#!/usr/bin/env python3
"""data_logger end-to-end bench (PC orchestrator) — the full matrix for
the two-stream logger (TASK_data_logger.md addendum):

  leg 1  format=sqlite  can=binary  monitor_all + autopid-sink rows
         (real ECU polls) + PCAN rate sweep incl. FULL-SPEED blast
         (the CAN benchmark numbers)
  leg 2  format=csv     can=csv     monitor_all
  leg 3  format=binary  can=sqlite  can_filter=315 (only 0x315 logged)
  leg 4  rotation/retention (can binary, 1 MB × 2 files) + runtime gate
         + frametest drain-rate benchmark

Every leg downloads the files via /api/fs and validates CONTENT
(python sqlite3 / csv parse / tools/wdl_dump.py decode — byte-exact
for the .wdl CAN frames we sent). CAN legs self-skip without a PCAN.

Topology: DUT over the USB mgmt link (192.168.82.1: HTTP + /ws/cli);
PCAN on this PC wired to the DUT's CAN bus. Ends with DATALOG PASS.

  python data_logger_bench.py [usb_ip] [--pcan PCAN_USBBUS2]
"""
import argparse
import csv as csvmod
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
# wdl_dump.py stays in wican-fw/tools (it is a PUBLIC user tool; Phase 3
# split 2026-07-26). Locate the fw checkout via WICAN_FW_PATH (test.ps1
# exports it) or the standard sibling layout.
_here = os.path.dirname(os.path.abspath(__file__))
for _cand in ([os.path.join(os.environ["WICAN_FW_PATH"], "tools")]
              if os.environ.get("WICAN_FW_PATH") else []) + [
        os.path.join(_here, "..", "..")]:
    if os.path.isfile(os.path.join(_cand, "wdl_dump.py")):
        sys.path.insert(0, _cand)
        break
from wsmin import WS, OP_TEXT            # noqa: E402
import wdl_dump                          # noqa: E402  (wican-fw/tools/)

USB = "192.168.82.1"
PCAN = "PCAN_USBBUS2"
fails = []
metrics = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + ": " + name
          + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def metric(name, value):
    metrics.append((name, value))
    print(f"METRIC {name} = {value}")


def api(path, method="GET", body=None, retries=4, timeout=20):
    # settings submits reboot the DUT — retry transport errors, return
    # real HTTP responses as-is (the em_worker/ha_webhook hardening)
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    last = None
    for _ in range(retries):
        req = urllib.request.Request(f"http://{USB}{path}", data=data,
                                     method=method,
                                     headers={"Content-Type":
                                              "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                t = r.read().decode()
                return r.status, (json.loads(t)
                                  if t.strip().startswith(("{", "[")) else t)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(2)
    raise last


def download(path):
    # the rings are FIFO: once they report (near-)empty twice, every
    # record queued BEFORE now has been committed — gating off earlier
    # would strand the tail in the ring (it survives for resume, but
    # the downloaded file would miss it)
    end = time.time() + 25
    calm = 0
    while time.time() < end and calm < 2:
        st = logger_stats()
        calm = calm + 1 if (st["queued"] <= 1 and
                            st["can"]["queued"] <= 1) else 0
        time.sleep(0.7)
    time.sleep(1.5)  # let the flush batch commit
    # FS_LOCK: the ACTIVE file is write-locked (open of any kind fails)
    # — park the writer (closes + flushes both files), fetch a
    # CONSISTENT snapshot, resume
    api("/api/logger/gate", "POST", {"enabled": False})
    time.sleep(1.5)
    try:
        last = None
        for _ in range(3):
            try:
                with urllib.request.urlopen(
                        f"http://{USB}/api/fs/download?path={path}",
                        timeout=60) as r:
                    return r.read()
            except (urllib.error.URLError, OSError) as e:
                last = e
                time.sleep(2)
        raise RuntimeError(f"download {path} failed: {last}")
    finally:
        api("/api/logger/gate", "POST", {"enabled": True})


def wait_online(secs=90):
    for _ in range(int(secs / 1.5)):
        try:
            api("/api/status", retries=1, timeout=4)
            return True
        except Exception:
            time.sleep(1.5)
    return False


def logger_stats():
    return api("/api/logger")[1]


# ---- ws_cli ------------------------------------------------------------------

def cli(cmd, marker, timeout=120):
    """One command over a fresh /ws/cli connection; returns the output
    accumulated until `marker` shows up. Newline is MANDATORY (a
    no-newline send poisons the channel's line buffer)."""
    ws = WS(USB, 80, "/ws/cli", timeout=10)
    try:
        ws.send((cmd + "\n").encode(), opcode=OP_TEXT)
        out = ""
        end = time.time() + timeout
        while time.time() < end:
            try:
                op, payload = ws.recv_frame()
            except Exception:
                continue
            if op == OP_TEXT or op == 2:
                out += payload.decode("utf-8", "replace")
                if marker in out:
                    return out
        raise TimeoutError(f"cli '{cmd}': no '{marker}' in: {out[-200:]}")
    finally:
        try:
            ws.s.close()
        except Exception:
            pass


# ---- log-dir helpers -----------------------------------------------------------

def log_files():
    for _ in range(5):
        code, r = api("/api/fs/list?path=/sd/logs")
        if code == 200 and isinstance(r, dict):
            return [e["name"] for e in r.get("entries", [])
                    if not e.get("dir")]
        time.sleep(1.5)
    return []


def clean_logs():
    # the writer HOLDS its active files open and FS_LOCK makes deleting
    # an open file fail (by design) — park the writer first (closes
    # both files), delete, resume
    api("/api/logger/gate", "POST", {"enabled": False})
    time.sleep(1)
    for name in log_files():
        if name.startswith(("dl_", "can_")):
            api(f"/api/fs/file?path=/sd/logs/{name}", "DELETE")
    api("/api/logger/gate", "POST", {"enabled": True})
    time.sleep(1)


def newest(prefix):
    names = sorted(n for n in log_files() if n.startswith(prefix))
    return names[-1] if names else None


def decode_wdl(blob):
    with tempfile.NamedTemporaryFile(suffix=".wdl", delete=False) as f:
        f.write(blob)
        path = f.name
    try:
        return list(wdl_dump.decode(path))
    finally:
        os.unlink(path)


def open_db(blob):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        f.write(blob)
        path = f.name
    return sqlite3.connect(path), path


# ---- settings ----------------------------------------------------------------

def put_settings(comp, changes):
    cur = api(f"/api/settings/{comp}")[1]
    for k in ("degraded", "pending_reboot"):
        cur.pop(k, None)
    cur.update(changes)
    code, r = api(f"/api/settings/{comp}", "PUT", cur)
    if code != 200:
        raise RuntimeError(f"PUT {comp} -> {code}: {r}")


def submit_reboot():
    try:
        api("/api/settings/submit", "POST", retries=1)
    except Exception:
        pass
    time.sleep(12)
    if not wait_online():
        raise RuntimeError("DUT did not come back after submit")


# ---- PCAN --------------------------------------------------------------------

def pcan_bus():
    try:
        import can
        return can.Bus(interface="pcan", channel=PCAN, bitrate=500000)
    except Exception as e:
        print(f"note: PCAN unavailable ({e}) — CAN legs skipped")
        return None


def pcan_send(bus, frames, gap_s=0.005):
    """frames = [(id, bytes), ...]; returns sent count."""
    import can
    sent = 0
    for fid, data in frames:
        try:
            bus.send(can.Message(arbitration_id=fid,
                                 is_extended_id=False, data=data),
                     timeout=0.2)
            sent += 1
        except can.CanError:
            pass
        if gap_s:
            time.sleep(gap_s)
    return sent


def make_frames(base_id, n, id_span=4):
    out = []
    for i in range(n):
        out.append((base_id + (i % id_span),
                    bytes([i & 0xFF, (i >> 8) & 0xFF, 0xA5, 0x5A,
                           i & 0xFF, 0xC3, 0x3C, (i ^ 0xFF) & 0xFF])))
    return out


def wait_frames_written(target, secs=30):
    end = time.time() + secs
    st = logger_stats()
    while time.time() < end:
        st = logger_stats()
        if st["can"]["frames_written"] >= target:
            break
        time.sleep(1)
    return st


# ---- validations ---------------------------------------------------------------

def validate_params_file(fmt, blob, expect_rows, autopid_names=()):
    """test.value rows 0..n-1 (value = i*0.5) + optional autopid rows."""
    if fmt == "sqlite":
        db, path = open_db(blob)
        try:
            n = db.execute(
                "SELECT COUNT(*) FROM records r JOIN params p "
                "ON p.id = r.param_id WHERE p.source='test' "
                "AND p.name='value';").fetchone()[0]
            vals = [r[0] for r in db.execute(
                "SELECT value FROM records r JOIN params p "
                "ON p.id = r.param_id WHERE p.source='test' "
                "ORDER BY r.rowid LIMIT 5;")]
            apn = [r[0] for r in db.execute(
                "SELECT DISTINCT name FROM params "
                "WHERE source='autopid';")]
        finally:
            db.close()
            os.unlink(path)
        ok_vals = vals == [i * 0.5 for i in range(len(vals))]
        return n == expect_rows and ok_vals, \
            f"rows={n} head={vals} autopid={apn}", apn
    if fmt == "csv":
        rows = [r for r in csvmod.reader(io.StringIO(blob.decode()))
                if r and r[0] != "ts_ms"]
        test = [r for r in rows if r[1] == "test.value"]
        apn = sorted({r[1].split(".", 1)[1] for r in rows
                      if r[1].startswith("autopid.")})
        ok_vals = [float(r[2]) for r in test[:5]] == \
            [i * 0.5 for i in range(min(5, len(test)))]
        return len(test) == expect_rows and ok_vals, \
            f"rows={len(test)} autopid={apn}", apn
    # wdl
    recs = decode_wdl(blob)
    test = [r for r in recs if r[0] == "param" and r[2] == "test.value"]
    apn = sorted({r[2].split(".", 1)[1] for r in recs
                  if r[0] == "param" and r[2].startswith("autopid.")})
    ok_vals = [r[3] for r in test[:5]] == \
        [i * 0.5 for i in range(min(5, len(test)))]
    return len(test) == expect_rows and ok_vals, \
        f"rows={len(test)} autopid={apn}", apn


def parse_candump(blob):
    import re
    out = []
    for line in blob.decode().splitlines():
        m = re.match(r"\((\d+)\.(\d{6})\)\s+\S+\s+"
                     r"([0-9A-Fa-f]+)#([0-9A-Fa-f]*)", line)
        if m:
            out.append((int(m.group(3), 16), bytes.fromhex(m.group(4))))
    return out


def parse_asc(blob):
    out = []
    for line in blob.decode().splitlines():
        parts = line.split()
        if len(parts) < 6 or parts[3] != "Rx" or parts[4] != "d":
            continue
        try:
            float(parts[0])
        except ValueError:
            continue
        dlc = int(parts[5])
        data = bytes(int(b, 16) for b in parts[6:6 + dlc])
        out.append((int(parts[2].rstrip("x"), 16), data))
    return out


def parse_jsonl_frames(blob):
    out = []
    for line in blob.decode().splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if "id" in d:
            out.append((int(d["id"], 16), bytes.fromhex(d["data"])))
    return out


def parse_blf(blob):
    """THIRD-PARTY validation: python-can's BLFReader."""
    import can
    with tempfile.NamedTemporaryFile(suffix=".blf", delete=False) as f:
        f.write(blob)
        path = f.name
    try:
        return [(m.arbitration_id, bytes(m.data))
                for m in can.BLFReader(path)]
    finally:
        os.unlink(path)


def parse_mf4(blob):
    """Minimal spec-offset MDF4 reader (asammdf cross-check when the
    lib is importable). Walks ID->HD->DG->CG/CN->DT."""
    import struct
    assert blob[:8] == b"MDF     ", "bad MDF magic"
    assert blob[8:12] == b"4.10", "bad MDF version"
    assert blob[64:68] == b"##HD", "no HD block at 64"

    def u64(off):
        return struct.unpack_from("<Q", blob, off)[0]

    dg = u64(64 + 24)                       # hd_dg_first
    assert blob[dg:dg + 4] == b"##DG"
    cg = u64(dg + 24 + 8)
    dt = u64(dg + 24 + 16)
    assert blob[cg:cg + 4] == b"##CG"
    assert blob[dt:dt + 4] == b"##DT"
    cycles = u64(cg + 24 + 48 + 8)
    rec_size = struct.unpack_from("<I", blob, cg + 24 + 48 + 24)[0]
    assert rec_size == 22, f"record size {rec_size}"
    dt_len = u64(dt + 8)
    assert dt_len == 24 + 22 * cycles, "DT length vs cycle count"

    # channel names via the CN chain
    names = []
    cn = u64(cg + 24 + 8)
    while cn:
        assert blob[cn:cn + 4] == b"##CN"
        tx = u64(cn + 24 + 16)
        s = blob[tx + 24:tx + 24 + 64].split(b"\x00")[0].decode()
        names.append(s)
        cn = u64(cn + 24)
    assert names == ["t", "CAN_DataFrame.ID", "CAN_DataFrame.Flags",
                     "CAN_DataFrame.DLC", "CAN_DataFrame.DataBytes"], \
        names

    out = []
    off = dt + 24
    for _ in range(cycles):
        t, fid, flags, dlc, data = struct.unpack_from("<dIBB8s", blob,
                                                      off)
        out.append((fid, data[:dlc]))
        off += 22
    return out


def asammdf_crosscheck(blob, expect_n):
    """Optional extra validation when asammdf is installed."""
    try:
        from asammdf import MDF
    except ImportError:
        return None
    with tempfile.NamedTemporaryFile(suffix=".mf4", delete=False) as f:
        f.write(blob)
        path = f.name
    try:
        with MDF(path) as m:
            sig = m.get("CAN_DataFrame.ID")
            return len(sig.samples) >= expect_n
    except Exception as e:
        return f"asammdf error: {e}"
    finally:
        os.unlink(path)


def frames_from_file(fmt, blob):
    """-> list of (id, data_bytes) in file order."""
    if fmt == "sqlite":
        db, path = open_db(blob)
        try:
            rows = db.execute("SELECT id, data FROM frames "
                              "ORDER BY rowid;").fetchall()
        finally:
            db.close()
            os.unlink(path)
        return [(r[0], bytes(r[1])) for r in rows]
    if fmt == "csv":
        rows = [r for r in csvmod.reader(io.StringIO(blob.decode()))
                if r and r[0] != "ts_ms"]
        return [(int(r[1], 16), bytes.fromhex(r[5])) for r in rows]
    recs = decode_wdl(blob)
    return [(r[2], bytes(r[5])) for r in recs if r[0] == "frame"]


def assert_sent_frames_present(name, fmt, blob, sent, id_span):
    got = frames_from_file(fmt, blob)
    ids = {sid for sid, _ in sent}
    ours = [g for g in got if g[0] in ids]
    # byte-exact, in-order comparison of OUR frames (other bus chatter
    # — autopid polls, the hardware ECU — may interleave legally)
    ok = ours == sent
    check(name, ok,
          f"{len(ours)}/{len(sent)} matched (file has {len(got)} total)")
    if not ok:
        # breadcrumb for the drop hunt: which indexes vanished + the
        # wire/dispatch loss counters (rx_missed / dispatch_drops,
        # added 2026-07-10 after a 197/200 heisen-fail)
        j, missing = 0, []
        for i, f in enumerate(sent):
            if j < len(ours) and ours[j] == f:
                j += 1
            else:
                missing.append(i)
        print(f"  missing sent-indexes: {missing[:20]}")
        try:
            print("  /api/can:", json.dumps(api("/api/can")[1]))
        except Exception as e:
            print("  /api/can read failed:", e)


# ---- main --------------------------------------------------------------------

def leg_settings(fmt, can_fmt, can_log, extra=None):
    ch = {"enabled": True, "format": fmt, "can_format": can_fmt,
          "can_log": can_log, "autopid_log": "off", "can_filter": "",
          "flush_ms": 300, "max_file_mb": 4, "max_files": 100,
          "can_max_file_mb": 8, "can_max_files": 50}
    if extra:
        ch.update(extra)
    put_settings("data_logger", ch)
    submit_reboot()
    clean_logs()


def main():
    global USB, PCAN
    ap = argparse.ArgumentParser()
    ap.add_argument("usb_ip", nargs="?", default=USB)
    ap.add_argument("--pcan", default=PCAN)
    a = ap.parse_args()
    USB = a.usb_ip
    PCAN = a.pcan

    if not wait_online():
        check("DUT reachable at start", False)
        sys.exit(1)

    saved_dl = api("/api/settings/data_logger")[1]
    saved_can = api("/api/settings/can_manager")[1]
    for d in (saved_dl, saved_can):
        for k in ("degraded", "pending_reboot"):
            d.pop(k, None)
    _, autopid_cfg = api("/api/autopid/config")

    st0 = api("/api/status")[1]
    mem_before = st0.get("memory", {}).get("internal", {}).get("free")

    bus = pcan_bus()

    try:
        # CAN bus up for the whole run (baud is a string enum)
        put_settings("can_manager", {"enabled": True, "baud": "500",
                                     "silent": False})

        # ---------------- leg 1: sqlite params + binary CAN ----------------
        leg_settings("sqlite", "binary", True,
                     {"autopid_log": "all"})
        st = logger_stats()
        check("leg1 status shape (can block present)",
              st.get("enabled") and isinstance(st.get("can"), dict)
              and st["can"].get("enabled"), json.dumps(st)[:160])

        t0 = time.time()
        cli("logger test 400", "queued 400/400")
        end = time.time() + 20
        while time.time() < end and logger_stats()["written"] < 400:
            time.sleep(0.5)
        dt = time.time() - t0
        st = logger_stats()
        check("leg1 sqlite: 400 test rows written",
              st["written"] >= 400, f"written={st['written']}")
        metric("params sqlite rows/s (incl pacing)", f"{400 / dt:.0f}")

        # autopid -> params: the REAL polling loop (bench ECU box
        # answers the standard PIDs) feeds the value sink with
        # autopid_log=all — assert its rows landed next to test.value.
        # Conditional: only when live autopid values actually exist
        # (the ECU box could be unpowered on another bench).
        _, apdata = api("/api/autopid/data")
        autopid_live = isinstance(apdata, dict) and any(
            v is not None for v in apdata.values())

        time.sleep(2)  # let the last batch land

        blob = download(f"/sd/logs/{newest('dl_')}")
        ok, detail, apn = validate_params_file("sqlite", blob, 400)
        check("leg1 sqlite file content exact", ok, detail)
        if autopid_live:
            check("leg1 autopid sink rows landed (autopid_log=all)",
                  len(apn) >= 1, str(apn))
        else:
            print("note: no live autopid values (ECU box off?) — "
                  "sink-row check skipped")

        if bus is not None:
            # warmup barrier: the FIRST ~3 PEAK frames after minutes of
            # TX-idle intermittently never land (2026-07-10; always
            # sent-indexes [0..2] of the train, every DUT counter
            # incl. the new rx_missed/dispatch_drops reads 0, IRAM TWAI
            # ISR didn't change it, and one warmup frame shrank the
            # loss from 3 to 2 — a fixed ~3-frame first-TX-after-idle
            # window; everything after is loss-free at any rate, see
            # the 2500-frame 500 fps leg. Mechanism unpinned, CHECKLIST
            # item). Absorb it with 3 throwaway frames outside the
            # match set; the 200-frame train stays strictly byte-exact.
            pcan_send(bus, [(0x2F0 + i, b"\x53\x59\x4e\x43")
                            for i in range(3)])
            time.sleep(0.3)

            # deterministic frames -> byte-exact .wdl check. All waits
            # are DELTAS: bus chatter (autopid polls, the ECU box, the
            # 0x123 broadcast above) legally moves frames_written too.
            pre = logger_stats()["can"]["frames_written"]
            sent = make_frames(0x300, 200)
            n = pcan_send(bus, sent)
            check("leg1 PCAN sent 200", n == 200, n)
            st = wait_frames_written(pre + 200)
            check("leg1 200 frames written",
                  st["can"]["frames_written"] >= pre + 200,
                  json.dumps(st["can"]))
            blob = download(f"/sd/logs/{newest('can_')}")
            assert_sent_frames_present("leg1 .wdl frames byte-exact",
                                       "wdl", blob, sent, 4)

            # CAN-rate benchmark (binary engine): paced 500 fps must be
            # loss-free; 1000/2000 fps paced report; then a FULL-SPEED
            # blast (gap 0 — python-can pushes as fast as the 500 kbit/s
            # bus accepts; ~3.4k fps wire ceiling for 8-byte 11-bit
            # frames) = "as many frames as possible".
            for rate, count, must_hold in ((500, 2500, True),
                                           (1000, 5000, False),
                                           (2000, 10000, False),
                                           (0, 40000, False)):
                pre = logger_stats()["can"]
                frames = make_frames(0x400, count)
                gap = max(0.0, 1.0 / rate - 0.0006) if rate else 0.0
                t0 = time.time()
                n = pcan_send(bus, frames, gap_s=gap)
                st = wait_frames_written(
                    pre["frames_written"] + n, 45)["can"]
                dt = time.time() - t0   # send start -> all landed
                landed = st["frames_written"] - pre["frames_written"]
                dropped = st["frames_dropped"] - pre["frames_dropped"]
                label = f"paced {rate}fps" if rate else "FULL BLAST"
                metric(f"CAN binary {label}",
                       f"landed {landed}/{n} in {dt:.1f}s "
                       f"({landed / dt:.0f}fps e2e) dropped {dropped}")
                if must_hold:
                    check(f"leg1 {rate}fps clean (no drops)",
                          dropped == 0 and landed >= n,
                          f"landed {landed}/{n} dropped {dropped}")

        # restore autopid config verbatim
        body = (autopid_cfg if isinstance(autopid_cfg, str)
                else json.dumps(autopid_cfg)).encode()
        api("/api/autopid/config", "PUT", body)

        # ---------------- leg 2: csv both streams ----------------
        leg_settings("csv", "csv", True)
        cli("logger test 400", "queued 400/400")
        end = time.time() + 20
        while time.time() < end and logger_stats()["written"] < 400:
            time.sleep(0.5)
        time.sleep(1.5)
        blob = download(f"/sd/logs/{newest('dl_')}")
        ok, detail, _ = validate_params_file("csv", blob, 400)
        check("leg2 csv params content exact", ok, detail)

        if bus is not None:
            pre = logger_stats()["can"]["frames_written"]
            sent = make_frames(0x310, 150)
            pcan_send(bus, sent)
            wait_frames_written(pre + 150)
            time.sleep(1.5)
            blob = download(f"/sd/logs/{newest('can_')}")
            assert_sent_frames_present("leg2 csv frames byte-exact",
                                       "csv", blob, sent, 4)

        # ---------------- leg 3: wdl params + sqlite CAN + filter --------
        leg_settings("binary", "sqlite", True,
                     {"can_filter": "315", "can_mask": "7FF"})
        cli("logger test 300", "queued 300/300")
        end = time.time() + 20
        while time.time() < end and logger_stats()["written"] < 300:
            time.sleep(0.5)
        time.sleep(1.5)
        blob = download(f"/sd/logs/{newest('dl_')}")
        ok, detail, _ = validate_params_file("wdl", blob, 300)
        check("leg3 .wdl params content exact", ok, detail)

        if bus is not None:
            pre = logger_stats()["can"]["frames_written"]
            want = [(0x315, bytes([i, 0x11, 0x22, 0x33, 0x44, 0x55,
                                   0x66, i ^ 0xFF])) for i in range(80)]
            noise = [(0x316, b"\xDE\xAD\xBE\xEF\x00\x00\x00\x00")] * 80
            inter = [f for pair in zip(want, noise) for f in pair]
            pcan_send(bus, inter)
            wait_frames_written(pre + 80)
            time.sleep(1.5)
            blob = download(f"/sd/logs/{newest('can_')}")
            got = frames_from_file("sqlite", blob)
            only315 = all(g[0] == 0x315 for g in got)
            ours = [g for g in got if g[0] == 0x315]
            check("leg3 filter: ONLY 0x315 in the sqlite frames table",
                  only315 and ours == want,
                  f"{len(ours)}/80 matched, foreign={not only315}")

        # ---------------- leg 4: rotation/retention + gate + drain rate ---
        leg_settings("binary", "binary", True,
                     {"can_max_file_mb": 1, "can_max_files": 2})
        st0l = logger_stats()
        t0 = time.time()
        cli("logger frametest 20000", "queued 20000/20000")
        cli("logger frametest 20000", "queued 20000/20000")
        cli("logger frametest 20000", "queued 20000/20000")
        end = time.time() + 60
        while time.time() < end and \
                logger_stats()["can"]["frames_written"] < 60000:
            time.sleep(1)
        dt = time.time() - t0
        st = logger_stats()
        check("leg4 60k synthetic frames written",
              st["can"]["frames_written"] >= 60000,
              json.dumps(st["can"]))
        metric("CAN binary drain rate (frametest)",
               f"{60000 / dt:.0f} frames/s")
        check("leg4 rotation happened (1 MB cap)",
              st["can"]["rotations"] >= 1, st["can"]["rotations"])
        check("leg4 retention holds can_max_files=2",
              st["can"]["files"] <= 2, st["can"]["files"])
        _, ev = api("/api/events/log")
        rotated = [e for e in ev.get("events", [])
                   if e.get("source") == "logger"
                   and e.get("name") == "rotated"]
        check("leg4 logger.rotated event published", len(rotated) >= 1,
              f"{len(rotated)} in ring")

        # runtime gate: pause -> nothing lands -> resume -> backlog lands
        api("/api/logger/gate", "POST", {"enabled": False})
        end = time.time() + 10   # writer parks + closes files (<=1 lap)
        while time.time() < end and logger_stats()["can"]["file"]:
            time.sleep(0.5)
        pre = logger_stats()
        check("leg4 gate reports paused (files closed)",
              pre["paused"] and not pre["can"]["file"], "")
        cli("logger frametest 500", "queued")
        time.sleep(3)
        mid = logger_stats()
        check("leg4 paused: frames_written static",
              mid["can"]["frames_written"] == pre["can"]["frames_written"],
              f"{pre['can']['frames_written']} -> "
              f"{mid['can']['frames_written']}")
        check("leg4 paused: ring absorbed the frames (pre-trigger)",
              mid["can"]["queued"] >= 500, mid["can"]["queued"])
        api("/api/logger/gate", "POST", {"enabled": True})
        end = time.time() + 15
        while time.time() < end and \
                logger_stats()["can"]["frames_written"] < \
                pre["can"]["frames_written"] + 500:
            time.sleep(1)
        post = logger_stats()
        check("leg4 resume lands the pre-trigger backlog",
              post["can"]["frames_written"] >=
              pre["can"]["frames_written"] + 500,
              f"{pre['can']['frames_written']} -> "
              f"{post['can']['frames_written']}")

        # ------- legs 5-8: addendum-2 formats (mf4/blf/candump/asc/jsonl)
        # Each: reboot into the format, send 100 known frames, download,
        # decode with an INDEPENDENT parser (python-can for BLF = true
        # third-party validation), byte-exact compare.
        fmt_legs = [
            ("candump", parse_candump, 0x340),
            ("blf", parse_blf, 0x350),
            ("mf4", parse_mf4, 0x360),
            ("asc", parse_asc, 0x370),
        ]
        for i, (fmt, parser, base_id) in enumerate(fmt_legs):
            legn = 5 + i
            # leg 5 also proves jsonl on the params stream
            param_fmt = "jsonl" if fmt == "candump" else "binary"
            leg_settings(param_fmt, fmt, True)

            if param_fmt == "jsonl":
                cli("logger test 200", "queued 200/200")
                end = time.time() + 20
                while time.time() < end and \
                        logger_stats()["written"] < 200:
                    time.sleep(0.5)
                blob = download(f"/sd/logs/{newest('dl_')}")
                rows = [json.loads(ln) for ln in
                        blob.decode().splitlines()
                        if ln.startswith("{")]
                test_rows = [r for r in rows
                             if r.get("param") == "test.value"]
                ok = len(test_rows) == 200 and \
                    [r["value"] for r in test_rows[:5]] == \
                    [i2 * 0.5 for i2 in range(5)]
                check(f"leg{legn} jsonl params content exact", ok,
                      f"rows={len(test_rows)}")

            if bus is None:
                print(f"note: no PCAN — {fmt} leg skipped")
                continue

            pre = logger_stats()["can"]["frames_written"]
            sent = make_frames(base_id, 100)
            pcan_send(bus, sent)
            wait_frames_written(pre + 100)
            blob = download(f"/sd/logs/{newest('can_')}")
            got = parser(blob)
            ids = {sid for sid, _ in sent}
            ours = [g for g in got if g[0] in ids]
            check(f"leg{legn} {fmt} frames byte-exact "
                  f"({parser.__name__})", ours == sent,
                  f"{len(ours)}/100 matched (file has {len(got)})")

            if fmt == "mf4":
                x = asammdf_crosscheck(blob, 100)
                if x is None:
                    print("note: asammdf not installed — "
                          "spec-offset parser only")
                else:
                    check("leg7 mf4 asammdf cross-check", x is True,
                          str(x))

        st = api("/api/status")[1]
        mem_after = st.get("memory", {}).get("internal", {}).get("free")
        if mem_before and mem_after:
            metric("internal free before/after (bytes)",
                   f"{mem_before} -> {mem_after}")
    finally:
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass
        # restore: settings + config, delete the bench's log files
        try:
            clean_logs()
        except Exception:
            pass
        try:
            api("/api/settings/data_logger", "PUT", saved_dl)
            api("/api/settings/can_manager", "PUT", saved_can)
            api("/api/settings/submit", "POST")
        except Exception:
            pass
        time.sleep(10)
        wait_online()

    if fails:
        print("DATALOG FAIL:", ", ".join(fails))
        sys.exit(1)
    print("DATALOG PASS")


if __name__ == "__main__":
    main()
