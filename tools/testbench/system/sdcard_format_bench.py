#!/usr/bin/env python3
"""The SD card format from the API (2026-10-25): the bench.

The claim: a card in the slot that WiCAN cannot read (exFAT, the factory
format of every card over 32 GB; NTFS; a blank card; a damaged boot sector)
is reported by what it holds, probed BEFORE any mount is tried (one W line,
no E line, no boot_errors fault), the format route refuses everything but
that card and the typed phrase, a format through it makes the card a
mounted FAT32 volume the filesystem API can use at once, and the next boot
mounts it quietly.

Rig: the DUT on the bench access point, reached over HTTP through an ssh
tunnel on the Pi; a card in the slot. The full run wants a card WiCAN
cannot read (a card formatted exFAT or NTFS on a computer, or one wiped
with `diskpart clean`; the 64 GB factory card of 2026-10-25); with a
mounted card the legs that need the unreadable card are skipped and the
verdict is PARTIAL. --format is required for the format itself: it erases
the card.

Legs:
  s0  as found: GET /api/sdcard answers with every key, its state agrees
      with /api/status (`bits.sdcard_mounted`, the `sd` summary)
  s1  the unreadable card: state unreadable with a reason the probe knows,
      the card named with its capacity, the `sd` summary says the same, the
      log ring of this boot has the W line and NO E line of the storage
      tags, /api/fs/info?path=/sd answers 503 (needs the unreadable card)
  s2  the refusals: no body 400, a wrong phrase 400, the phrase on a mounted
      card 409 (mounted card only), a second request while one runs 409
  s3  the format (--format, the unreadable card): 202, state formatting,
      then mounted within --format-wait s with format.state done and
      took_ms; /api/fs/info?path=/sd within 3 % of the card's capacity and
      nearly empty; a 64 KB file uploaded, downloaded byte-exact and deleted
      through /api/fs; the `sd` summary reads mounted; no E line
  s4  the boot after (--format): a planned restart, the card mounted at boot,
      no E line of the storage tags, no new fault
  s5  the sweep: planned restarts only, no E line of the own tags, the
      internal heap floor

  python tools/testbench/system/sdcard_format_bench.py --dut localhost:18081
      [--format] [--format-wait 120] [--only s0,s2] [--logdir DIR]

Prints `SD FORMAT PASS` (or `SD FORMAT PARTIAL PASS (legs: ...)` when a leg
was skipped: a mounted card as found, or --format not given).
"""
import argparse
import hashlib
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import pcbench  # noqa: E402

OWN_TAGS = ("external_storage", "vfs_fat_sdmmc", "vfs_fat", "sdmmc_cmd",
            "sdmmc_sd", "sdmmc_common", "sdmmc_periph", "ff_diskio")
LEGS = ("s0", "s1", "s2", "s3", "s4", "s5")
REASONS = ("exfat", "ntfs", "blank", "gpt", "unknown", "fat_corrupt")
PHRASE = "format sdcard"
TEST_FILE = "/sd/sdfmt_bench.bin"


def ring_this_boot(dut):
    """The ring's lines after the last boot marker (the tracker's `boot #`
    line), the whole ring when there is none."""
    lines = dut.ring_lines()
    idx = [i for i, ln in enumerate(lines) if "restart_tracker: boot #" in ln]
    return lines[idx[-1]:] if idx else lines


def storage_e_lines(lines):
    out = []
    for ln in lines:
        if not ln.startswith("E (") or ") " not in ln:
            continue
        tag = ln.split(") ", 1)[1].split(":", 1)[0].strip()
        if tag in OWN_TAGS:
            out.append(ln)
    return out


def faults_count(dut):
    code, f = dut.api("/api/faults")
    return len(f.get("faults", [])) if code == 200 and isinstance(f, dict) else None


def wait_state(dut, want, timeout):
    """Poll GET /api/sdcard until state is in `want`; (doc, seconds)."""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        code, d = dut.api("/api/sdcard", timeout=5)
        if code == 200 and isinstance(d, dict):
            last = d
            if d.get("state") in want:
                return d, time.time() - t0
        time.sleep(1)
    return last, time.time() - t0


def upload_raw(base, path, data):
    req = urllib.request.Request(base + "/api/fs/upload?path=" + path, data=data,
                                 method="POST",
                                 headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def download_raw(base, path):
    with urllib.request.urlopen(base + "/api/fs/download?path=" + path, timeout=60) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dut", default="localhost:18081")
    ap.add_argument("--format", action="store_true",
                    help="really format the unreadable card (erases it)")
    ap.add_argument("--format-wait", type=int, default=120)
    ap.add_argument("--only", default="")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()
    only = set(x.strip() for x in a.only.split(",") if x.strip())
    legs = [l for l in LEGS if not only or l in only or l in ("s0", "s5")]
    skipped = []

    run = pcbench.Run("SD FORMAT")
    dut = pcbench.Dut(a.dut, own_tags=OWN_TAGS, tasks=("sd_detect",))
    st0 = dut.get("/api/status")
    dut.as_found()

    # ---- s0: as found ----------------------------------------------------------
    sd = dut.get("/api/sdcard")
    run.check("s0_sdcard_keys", all(k in sd for k in ("present", "mounted", "state",
                                                      "reason", "card", "format")),
              str(sorted(sd.keys())))
    run.check("s0_format_keys", all(k in (sd.get("format") or {}) for k in
                                    ("state", "error", "took_ms", "age_s", "cluster_kb", "phrase")),
              str(sd.get("format")))
    bits = st0.get("bits", {})
    summary = st0.get("sd")
    run.check("s0_status_bit_agrees", bits.get("sdcard_mounted") == bool(sd.get("mounted")),
              f"bit {bits.get('sdcard_mounted')} vs mounted {sd.get('mounted')}")
    if sd.get("present"):
        run.check("s0_status_summary_agrees", isinstance(summary, dict)
                  and summary.get("state") == sd.get("state")
                  and summary.get("reason") == sd.get("reason"), str(summary))
    else:
        run.check("s0_status_summary_absent_for_empty_slot", summary is None, str(summary))
    print(f"as found: state {sd.get('state')} reason {sd.get('reason')!r} "
          f"card {sd.get('card')}", flush=True)
    unreadable = sd.get("state") == "unreadable"
    mounted = sd.get("state") == "mounted"

    # ---- s1: the unreadable card -----------------------------------------------
    if "s1" in legs:
        if not unreadable:
            skipped.append("s1")
            print("s1 skipped: the card in the slot is readable (or absent)", flush=True)
        else:
            run.check("s1_reason_known", sd.get("reason") in REASONS, sd.get("reason"))
            card = sd.get("card") or {}
            run.check("s1_card_named", bool(card.get("name")) and card.get("capacity_bytes", 0) > 0
                      and len(card.get("serial", "")) == 8, str(card))
            run.check("s1_summary_names_card", isinstance(summary, dict)
                      and summary.get("name") == card.get("name")
                      and summary.get("capacity_bytes") == card.get("capacity_bytes"),
                      str(summary))
            lines = ring_this_boot(dut)
            w_lines = [ln for ln in lines if "external_storage" in ln and "not readable" in ln
                       or "external_storage" in ln and "mount refused" in ln]
            run.check("s1_one_W_line_names_what_the_card_holds",
                      any(ln.startswith("W (") and sd.get("reason") in ln for ln in w_lines),
                      " | ".join(w_lines)[:300])
            e_lines = storage_e_lines(lines)
            run.check("s1_no_E_line_for_an_unreadable_card", not e_lines,
                      " | ".join(e_lines)[:300])
            code, info = dut.api("/api/fs/info?path=/sd")
            run.check("s1_fs_info_sd_503", code == 503, f"{code} {info}")
            run.check("s1_format_idle_as_found", (sd.get("format") or {}).get("state") == "idle",
                      str(sd.get("format")))

    # ---- s2: the refusals --------------------------------------------------------
    if "s2" in legs:
        code, r = dut.api("/api/sdcard/format", "POST", {})
        run.check("s2_no_phrase_400", code == 400 and "confirm" in str(r), f"{code} {r}")
        code, r = dut.api("/api/sdcard/format", "POST", {"confirm": "yes please"})
        run.check("s2_wrong_phrase_400", code == 400, f"{code} {r}")
        if mounted:
            code, r = dut.api("/api/sdcard/format", "POST", {"confirm": PHRASE})
            run.check("s2_mounted_card_refused_409", code == 409 and "mounted" in str(r),
                      f"{code} {r}")
            sd2 = dut.get("/api/sdcard")
            run.check("s2_mounted_card_untouched", sd2.get("state") == "mounted"
                      and (sd2.get("format") or {}).get("state") == "idle")
        elif not sd.get("present"):
            code, r = dut.api("/api/sdcard/format", "POST", {"confirm": PHRASE})
            run.check("s2_empty_slot_refused_409", code == 409 and "no card" in str(r),
                      f"{code} {r}")
        elif sd.get("state") == "failed":
            code, r = dut.api("/api/sdcard/format", "POST", {"confirm": PHRASE})
            run.check("s2_dead_card_refused_409", code == 409 and "not answer" in str(r),
                      f"{code} {r}")

    # ---- s3: the format ----------------------------------------------------------
    formatted = False
    if "s3" in legs:
        if not (unreadable and a.format):
            skipped.append("s3")
            print("s3 skipped: " + ("no unreadable card in the slot" if not unreadable
                                    else "--format not given (it erases the card)"), flush=True)
        else:
            cap = (sd.get("card") or {}).get("capacity_bytes", 0)
            faults0 = faults_count(dut)
            code, r = dut.api("/api/sdcard/format", "POST", {"confirm": PHRASE})
            run.check("s3_format_accepted_202", code == 202 and r.get("state") == "formatting",
                      f"{code} {r}")
            code, r2 = dut.api("/api/sdcard/format", "POST", {"confirm": PHRASE})
            run.check("s3_second_request_409_while_running", code in (409,)
                      or (code == 202),  # a very fast card may already be done
                      f"{code} {r2}")
            d, took = wait_state(dut, ("mounted", "unreadable", "failed", "absent"),
                                 a.format_wait)
            run.check("s3_mounted_after_format", d is not None and d.get("state") == "mounted"
                      and (d.get("format") or {}).get("state") == "done",
                      f"{d.get('state') if d else None} {d.get('format') if d else None} "
                      f"after {took:.1f} s")
            if d and d.get("state") == "mounted":
                formatted = True
                run.metric("format_took_ms", (d.get("format") or {}).get("took_ms"))
                run.metric("format_seen_after_s", round(took, 1))
                fs = d.get("fs") or {}
                run.check("s3_fs_size_matches_card",
                          cap > 0 and abs(fs.get("total", 0) - cap) <= cap * 0.03
                          and fs.get("used", 1 << 40) < 2 * 1024 * 1024,
                          f"total {fs.get('total')} used {fs.get('used')} card {cap}")
                code, info = dut.api("/api/fs/info?path=/sd")
                run.check("s3_fs_info_sd_200", code == 200 and info.get("total") == fs.get("total"),
                          f"{code} {info}")
                st = dut.get("/api/status")
                run.check("s3_status_mounted", st.get("bits", {}).get("sdcard_mounted") is True
                          and (st.get("sd") or {}).get("state") == "mounted", str(st.get("sd")))
                data = hashlib.sha256(b"sdfmt").digest() * 2048  # 64 KB
                try:
                    code = upload_raw(dut.base, TEST_FILE, data)
                    back = download_raw(dut.base, TEST_FILE)
                    run.check("s3_file_round_trip_byte_exact", code == 200 and back == data,
                              f"upload {code}, {len(back)} bytes back")
                finally:
                    dut.api("/api/fs/file?path=" + TEST_FILE, "DELETE")
                code, lst = dut.api("/api/fs/list?path=/sd")
                run.check("s3_test_file_deleted", code == 200 and not any(
                    e.get("name") == os.path.basename(TEST_FILE) for e in lst.get("entries", [])),
                    f"{code} {lst}")
                run.check("s3_no_new_fault", faults_count(dut) == faults0,
                          f"{faults0} -> {faults_count(dut)}")
                e_lines = storage_e_lines(ring_this_boot(dut))
                run.check("s3_no_E_line_through_the_format", not e_lines,
                          " | ".join(e_lines)[:300])

    # ---- s4: the boot after -------------------------------------------------------
    if "s4" in legs:
        if not formatted:
            skipped.append("s4")
            print("s4 skipped: nothing was formatted", flush=True)
        else:
            faults0 = faults_count(dut)
            dut.restart()
            time.sleep(3)
            d, took = wait_state(dut, ("mounted",), 30)
            run.check("s4_mounted_at_boot", d is not None and d.get("state") == "mounted",
                      f"{d.get('state') if d else None} after {took:.1f} s")
            lines = ring_this_boot(dut)
            run.check("s4_mounted_line_this_boot", any("external_storage: mounted /sd" in ln
                                                       for ln in lines))
            e_lines = storage_e_lines(lines)
            run.check("s4_no_E_line_at_boot", not e_lines, " | ".join(e_lines)[:300])
            run.check("s4_no_new_fault", faults_count(dut) == faults0,
                      f"{faults0} -> {faults_count(dut)}")

    # ---- s5: the sweep -----------------------------------------------------------
    dut.reset_check(run, st0)
    dut.passing_checks(run)

    partial = ("legs: " + " ".join(l for l in legs if l not in skipped)) if skipped or only else ""
    return run.verdict(partial)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except pcbench.Bench as e:
        print(f"BENCH: {e}")
        sys.exit(2)
