#!/usr/bin/env python3
"""Every settings group, key by key, against its own schema (Ali,
2026-11-16: "go through the settings one by one and test them").

What a settings page relies on, for every registered component and every
key, without applying anything (no submit, so no restart: the values the
device RUNS on never change):

  s0  the listing: every component has a document and a schema; none is
      degraded (running on defaults after a failed load) and none waits for
      a restart; every stored key is in its schema and fits it (type, enum,
      minimum, maximum, maxLength); every schema key is in the document
  s1  save without an edit: the document as read is PUT back and answers
      `changed:false` (a page saved untouched must not ask for a restart;
      the redacted passwords must not count as an edit)
  s2  refusals: per key one value of the wrong type, one outside the enum,
      one under the minimum, one over the maximum, one string longer than
      maxLength: each must answer 400 with a reason, and the document must
      read back as it was
  s3  round trip: per key another valid value (a boolean flipped, the next
      enum value, the other bound of a number): 200, it reads back, the
      document says `pending_reboot`, then the first value is PUT back and
      the document is as found with nothing pending. A valid-by-schema value
      refused by the component's own rule (two keys that depend on each
      other) is reported with the rule's words, not failed.
  s4  as found: every document equal to the first read, nothing pending, no
      boot in between, no E line from settings_manager

Never swept in s3: passwords and keys (a blank on PUT keeps the stored
secret, so a test value could not be taken back), free text (addresses,
names: their meaning is the component's, the feature benches cover them)
and tables (bridges, channels, servers, rules: the same).

  python tools/testbench/system/settings_sweep_bench.py --dut localhost:18090
      [--only wifi_manager,sleep_manager] [--skip s3] [--logdir DIR]

Prints `SETTINGS SWEEP PASS` (or FAIL with the failed checks); the per-key
table goes to <logdir>/settings_sweep.json and .md. About 3 min.
Written 2026-11-16.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import pcbench  # noqa: E402

SECRET = re.compile(r"password|passkey|psk|_key$|^key$|token|secret|sim_pin|private", re.I)
INJECTED = ("degraded", "pending_reboot")


def clean(doc):
    return {k: v for k, v in doc.items() if k not in INJECTED}


def type_ok(v, t):
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    return True


def fits(v, prop):
    """'' when the value fits its property schema, else why not."""
    t = prop.get("type")
    if t and not type_ok(v, t):
        return f"type {type(v).__name__}, schema says {t}"
    if "enum" in prop and v not in prop["enum"]:
        return f"not in the enum {prop['enum']}"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in prop and v < prop["minimum"]:
            return f"under the minimum {prop['minimum']}"
        if "maximum" in prop and v > prop["maximum"]:
            return f"over the maximum {prop['maximum']}"
    if isinstance(v, str) and "maxLength" in prop and len(v) > prop["maxLength"]:
        return f"longer than maxLength {prop['maxLength']}"
    return ""


def bad_values(v, prop):
    """(label, value) pairs the schema must refuse for this key."""
    t = prop.get("type")
    out = []
    if t == "boolean":
        out.append(("wrong type", "yes"))
    elif t in ("integer", "number"):
        out.append(("wrong type", "x"))
        if "minimum" in prop:
            out.append(("under the minimum", prop["minimum"] - 1))
        if "maximum" in prop:
            out.append(("over the maximum", prop["maximum"] + 1))
        if t == "integer":
            base = prop.get("minimum", 0)
            out.append(("a fraction", base + 0.5))
    elif t == "string":
        out.append(("wrong type", 5))
        if "maxLength" in prop:
            out.append(("too long", "a" * (prop["maxLength"] + 1)))
    elif t == "array":
        out.append(("wrong type", "x"))
    if "enum" in prop:
        out.append(("outside the enum", "__nope__" if t == "string" else 987654))
    return out


def other_value(v, prop):
    """Another valid value for this key, or None when there is none to pick."""
    t = prop.get("type")
    if "enum" in prop:
        vals = prop["enum"]
        if len(vals) < 2 or v not in vals:
            return None
        return vals[(vals.index(v) + 1) % len(vals)]
    if t == "boolean":
        return not v
    if t in ("integer", "number") and isinstance(v, (int, float)) and not isinstance(v, bool):
        lo, hi = prop.get("minimum"), prop.get("maximum")
        if lo is not None and v != lo:
            return lo
        if hi is not None and v != hi:
            return hi
        if lo is None and hi is None:
            return v + 1
        if lo is not None and hi is None:
            return v + 1
        if hi is not None and lo is None:
            return v - 1
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dut", default="localhost:18090")
    ap.add_argument("--only", default="")
    ap.add_argument("--skip", default="", help="legs to leave out: s1,s2,s3")
    ap.add_argument("--logdir", default="")
    a = ap.parse_args()
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    skip = [x.strip() for x in a.skip.split(",") if x.strip()]
    logdir = a.logdir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "test-reports", "logs", time.strftime("settings_sweep_%Y%m%d_%H%M"))
    os.makedirs(logdir, exist_ok=True)

    run = pcbench.Run("SETTINGS SWEEP")
    dut = pcbench.Dut(a.dut, own_tags=("settings_manager", "api_http"))
    table = []                  # one row per key
    notes = []                  # a component's own rule that refused a valid value
    found = {}
    schemas = {}

    def put(name, doc):
        return dut.api(f"/api/settings/{name}", "PUT", doc, timeout=15)

    def read(name):
        return dut.get(f"/api/settings/{name}")

    try:
        info = dut.get("/api/info")
        st0 = dut.get("/api/status")
        dut.as_found()
        print(f"DUT {info.get('device_id')} {info.get('fw_version')} {info.get('partition')}",
              flush=True)

        # ---- s0: the listing ------------------------------------------------------
        print("== s0 the listing", flush=True)
        listing = dut.get("/api/settings")
        comps = listing.get("components", listing) if isinstance(listing, dict) else listing
        names = sorted(c["name"] if isinstance(c, dict) else c for c in comps)
        if only:
            names = [n for n in names if n in only]
        run.check("s0_components_listed", len(names) > 0, f"{len(names)} components")
        degraded, pending, no_schema, misfit, missing, extra = [], [], [], [], [], []
        for n in names:
            raw = read(n)
            if raw.get("degraded"):
                degraded.append(n)
            if raw.get("pending_reboot"):
                pending.append(n)
            found[n] = clean(raw)
            code, sch = dut.api(f"/api/settings/{n}/schema", timeout=15)
            if code != 200 or not isinstance(sch, dict) or "properties" not in sch:
                no_schema.append(f"{n} (http {code})")
                schemas[n] = {"properties": {}}
                continue
            schemas[n] = sch
            props = sch["properties"]
            for k, v in found[n].items():
                if k not in props:
                    extra.append(f"{n}.{k}")
                    continue
                why = fits(v, props[k])
                if why:
                    misfit.append(f"{n}.{k}: {why}")
            for k in props:
                if k not in found[n]:
                    missing.append(f"{n}.{k}")
        keys_total = sum(len(d) for d in found.values())
        run.check("s0_every_component_has_a_schema", not no_schema, ", ".join(no_schema))
        run.check("s0_none_degraded", not degraded, ", ".join(degraded))
        run.check("s0_none_waiting_for_a_restart", not pending, ", ".join(pending))
        run.check("s0_every_stored_value_fits_its_schema", not misfit, " | ".join(misfit)[:400])
        run.check("s0_every_stored_key_is_in_its_schema", not extra, ", ".join(extra)[:300])
        run.check("s0_every_schema_key_is_in_its_document", not missing, ", ".join(missing)[:300])
        run.metric("components", len(names))
        run.metric("keys", keys_total)

        # ---- s1: save without an edit ----------------------------------------------
        if "s1" not in skip:
            print("== s1 save without an edit", flush=True)
            bad = []
            for n in names:
                code, r = put(n, found[n])
                if code != 200 or not isinstance(r, dict) or r.get("changed") is not False:
                    bad.append(f"{n}: http {code} {str(r)[:80]}")
            run.check("s1_untouched_save_changes_nothing", not bad, " | ".join(bad)[:400])

        # ---- s2: refusals -----------------------------------------------------------
        accepted = []
        silent = []
        n_refused = 0
        if "s2" not in skip:
            print("== s2 refusals", flush=True)
            for n in names:
                props = schemas[n]["properties"]
                for k, v in found[n].items():
                    if k not in props:
                        continue
                    for label, badv in bad_values(v, props[k]):
                        doc = dict(found[n])
                        doc[k] = badv
                        code, r = put(n, doc)
                        if code == 400:
                            n_refused += 1
                            msg = r.get("error") if isinstance(r, dict) else str(r)
                            if not msg:
                                silent.append(f"{n}.{k} ({label})")
                        else:
                            accepted.append(f"{n}.{k} {label} ({badv!r}): http {code} "
                                            f"{str(r)[:60]}")
                            put(n, found[n])        # take it back at once
                now = clean(read(n))
                if now != found[n]:
                    accepted.append(f"{n}: the document changed in the refusal pass")
                    put(n, found[n])
            run.check("s2_every_bad_value_refused", not accepted, " | ".join(accepted)[:600])
            run.check("s2_every_refusal_says_why", not silent, ", ".join(silent)[:300])
            run.metric("refusals_checked", n_refused)

        # ---- s3: round trip -----------------------------------------------------------
        if "s3" not in skip:
            print("== s3 round trip", flush=True)
            failed = []
            swept = not_swept = 0
            for n in names:
                props = schemas[n]["properties"]
                for k, v in found[n].items():
                    row = {"component": n, "key": k, "value": "<secret>" if SECRET.search(k)
                           else v, "type": (props.get(k) or {}).get("type", "?"),
                           "result": ""}
                    table.append(row)
                    prop = props.get(k)
                    if prop is None:
                        row["result"] = "not in the schema"
                        continue
                    if SECRET.search(k):
                        row["result"] = "not swept: a secret"
                        not_swept += 1
                        continue
                    alt = other_value(v, prop)
                    if alt is None:
                        row["result"] = "not swept: " + ("a table" if prop.get("type") == "array"
                                                          else "free text" if prop.get("type") == "string"
                                                          else "no other value to pick")
                        not_swept += 1
                        continue
                    doc = dict(found[n])
                    doc[k] = alt
                    code, r = put(n, doc)
                    if code == 400:
                        msg = r.get("error") if isinstance(r, dict) else str(r)
                        row["result"] = f"{alt!r} refused by the component's rule: {msg}"
                        notes.append(f"{n}.{k} = {alt!r}: {msg}")
                        if clean(read(n)) != found[n]:
                            failed.append(f"{n}.{k}: refused, yet the document changed")
                            put(n, found[n])
                        continue
                    if code != 200:
                        failed.append(f"{n}.{k} = {alt!r}: http {code} {str(r)[:60]}")
                        continue
                    back = read(n)
                    ok = back.get(k) == alt and back.get("pending_reboot") is True \
                        and isinstance(r, dict) and r.get("changed") is True
                    code2, r2 = put(n, found[n])
                    after = read(n)
                    ok2 = code2 == 200 and clean(after) == found[n] \
                        and after.get("pending_reboot") is False
                    if ok and ok2:
                        row["result"] = f"{alt!r} taken, read back, pending; put back"
                        swept += 1
                    else:
                        failed.append(f"{n}.{k} = {alt!r}: read back {back.get(k)!r}, pending "
                                      f"{back.get('pending_reboot')}, changed "
                                      f"{r.get('changed') if isinstance(r, dict) else r}; put back "
                                      f"http {code2}, as found {clean(after) == found[n]}, pending "
                                      f"{after.get('pending_reboot')}")
            run.check("s3_every_key_round_trips", not failed, " | ".join(failed)[:800])
            run.metric("keys_round_tripped", swept)
            run.metric("keys_not_swept", not_swept)
            run.metric("refused_by_a_components_own_rule", len(notes))
            for line in notes:
                print("  own rule: " + line[:200], flush=True)
    except pcbench.Bench as e:
        run.check("rig", False, str(e))
    finally:
        # ---- s4: as found -------------------------------------------------------------
        print("== s4 as found", flush=True)
        differ, still = [], []
        for n, doc in found.items():
            try:
                now = read(n)
                if clean(now) != doc:
                    put(n, doc)
                    now = read(n)
                if clean(now) != doc:
                    differ.append(n)
                if now.get("pending_reboot"):
                    still.append(n)
            except pcbench.Bench as e:
                differ.append(f"{n} ({e})")
        run.check("s4_every_document_as_found", not differ, ", ".join(differ)[:300])
        run.check("s4_nothing_waits_for_a_restart", not still, ", ".join(still)[:300])
        try:
            st1 = dut.get("/api/status")
            run.check("s4_no_boot_during_the_sweep",
                      st1.get("boot_count") == st0.get("boot_count"),
                      f"boot_count {st0.get('boot_count')} -> {st1.get('boot_count')}")
            dut.passing_checks(run)
        except (pcbench.Bench, NameError) as e:
            run.check("s4_closing", False, str(e))
        with open(os.path.join(logdir, "settings_sweep.json"), "w", encoding="utf-8") as f:
            json.dump({"table": table, "own_rules": notes, "found": {
                n: {k: ("<secret>" if SECRET.search(k) and v else v) for k, v in d.items()}
                for n, d in found.items()}}, f, indent=1)
        with open(os.path.join(logdir, "settings_sweep.md"), "w", encoding="utf-8") as f:
            f.write("| Component | Key | Type | Stored | Result |\n|---|---|---|---|---|\n")
            for r in table:
                val = json.dumps(r["value"])
                f.write(f"| {r['component']} | {r['key']} | {r['type']} | "
                        f"{val if len(val) < 50 else val[:47] + '...'} | {r['result']} |\n")
        print(f"table: {os.path.join(logdir, 'settings_sweep.md')}", flush=True)
    return run.verdict()


if __name__ == "__main__":
    sys.exit(main())
