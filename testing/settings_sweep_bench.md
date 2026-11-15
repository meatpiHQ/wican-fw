# Every settings group, key by key, against its own schema

Command: `python tools\testbench\system\settings_sweep_bench.py --dut localhost:<port> [--only <component>,...] [--skip s1,s2,s3] [--logdir DIR]`

Needs: PC + a tunnel to the DUT through rpi001 (`ssh -N -L <port>:<DutIp>:80 rpi001`)

Index row and the other stages: [../TESTING.md](../TESTING.md).

**What it covers.** Ali, 2026-11-16: "go through the settings one by one
and test them". What every settings page relies on, for every registered
component and every key, without applying anything: the bench never
submits, so the unit never restarts and the values it RUNS on never
change. Prints `SETTINGS SWEEP PASS`, about 3 min; the per-key table goes
to `<logdir>/settings_sweep.md` and `.json`.

Legs: s0 the listing (every component has a document and a schema, none
is degraded, none waits for a restart, every stored key is in its schema
and fits it by type, enum, minimum, maximum and maxLength, every schema
key is in the document); s1 save without an edit (the document as read is
PUT back and must answer `changed:false`: a page saved untouched must not
ask for a restart, and the redacted passwords must not count as an edit);
s2 refusals (per key one value of the wrong type, one outside the enum,
one under the minimum, one over the maximum, a fraction for an integer,
one string longer than maxLength: each must answer 400 with a reason and
the document must read back as it was); s3 round trip (per key another
valid value: a boolean flipped, the next enum value, the other bound of a
number: 200 with `changed:true`, it reads back, the document says
`pending_reboot`, then the first value is PUT back and the document is as
found with nothing pending); s4 as found (every document equal to the
first read, nothing pending, no boot in between, no E line of
`settings_manager` or `api_http`, the heap's floor).

A value that is valid by the schema and refused by the component's own
rule (two keys that depend on each other) is reported with the rule's
words, not failed: the refusal is the component working.

Never swept in s3: passwords and keys (a blank on PUT keeps the stored
secret, so a test value could not be taken back), free text (addresses,
names: their meaning is the component's, the feature benches cover them)
and tables (bridges, channels, servers, rules: the same).

What it does not cover: that a value does what it says once applied.
That is each feature's own bench (the index in `TESTING.md`); this one
is the contract every page shares.

**Records.**

- 2026-11-16 03:34, 6.0.0-beta.11 (the release bytes) on unit
  `14c19f457fd9` (`test-reports/logs/beta11_fulltest_20261116/
  c0_settings_sweep_run1.log`, the table beside it): **`SETTINGS SWEEP
  PASS`**. 44 components, 315 keys; the untouched save of all 44 changed
  nothing; 626 bad values refused, each with a reason; 207 keys round
  tripped; 96 not swept (secrets, free text, tables); 12 valid values
  refused by a component's own rule, every one a rule between two keys
  with a message that names the other key: `ble_manager.phy` and
  `.advertising` under the Lean memory profile, `http_server_manager.
  auth_enabled` without a password, `log_sinks.udp_enabled` without a
  host, `obd_chip.wake_voltage_mv` under the sleep voltage,
  `vpn_manager.enabled` without a peer key, and `sta_ip_mode` /
  `fallbackN_ip_mode` static without an address. No boot, no E line,
  heap floor 44,724 B. About 1100 PUTs in 2 min.

**Traps.** Every accepted PUT is a write of that component's file: the
sweep costs about 400 small flash writes, so it is a bench for a test
unit, not a loop. A run that dies midway leaves at most one key changed
and nothing applied; the closing leg puts every document back, and so
does a second run.
