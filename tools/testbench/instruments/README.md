# BenchBoard instruments — one folder per piece of bench equipment

Every external tool the tests need (power supply, CAN adapter, ECU
simulator, the bench Pi, …) lives here as **one self-contained folder**:

```
instruments/
  psu/
    instrument.toml     <- identity + config + health probe (required)
    probe.py            <- the probe script (optional, any language)
  _template/            <- copy this to add a new instrument
```

The folder name **is** the instrument id (letters/digits/_ only). Folders
whose name starts with `_` or `.` are ignored. `benchboard.toml` points
here via `[project] instruments_dir`.

## instrument.toml

```toml
label    = "OWON PSU"          # chip text in the UI
desc     = "car-battery sim"   # tooltip
optional = true                # absent = grey chip, not a red fault
doc      = "free-form notes shown in the UI tooltip"

[config]                       # machine-specific knobs, all overridable
port = "COM2016"               # in benchboard.local.toml

[health]                       # side-effect-free health probe
cmd = "${python} \"${dir}/probe.py\" --port ${port}"
timeout_s = 20
```

Health probe contract (plan §5b): **exit 0 = healthy**, the **last stdout
line** is the status shown on the chip, and the probe must be
**side-effect free** (never open the DUT console COM port — that resets
the DUT). BenchBoard probes all instruments in parallel at launch; a chip
click re-probes one.

`${...}` substitutions available in `cmd`:

| token                  | meaning                                        |
|------------------------|------------------------------------------------|
| `${python}`            | the interpreter running BenchBoard (quoted if the path has spaces) |
| `${dir}`               | this instrument's folder (absolute path)       |
| `${port}` etc.         | this instrument's own `[config]` keys          |
| `${<id>.<key>}`        | any other instrument's config, e.g. `${pi.ssh}`|

The same `${python}` / `${<id>.<key>}` tokens work in every `cmd` in
`benchboard.toml` (tests, actions, flow steps) — so config like a COM
port or the Pi's ssh alias is written in exactly one place.

## Porting to YOUR bench (clone-and-run)

Nothing in this folder needs editing to run on a different machine.
Create **`benchboard.local.toml`** next to `benchboard.toml` (it is
gitignored — copy `benchboard.local.example.toml`):

```toml
[instruments.psu.config]
port = "COM7"                  # my OWON is on COM7

[instruments.pcan]
enabled = false                # I don't have a PCAN — hides the chip and
                               # blocks tests that need it, with a clear
                               # message instead of a confusing failure

[defaults]
id = "aabbccddeeff"            # my WiCAN's device id (overrides the
dut_ip = "10.42.0.62"          # default of every test param named 'id')
```

Overriding a config key that does not exist is a load error (typo guard).

## Adding a new instrument

1. `cp -r _template mytool` (folder name = id).
2. Fill in `instrument.toml`; write `probe.py` (or point `cmd` at any
   existing CLI — `ssh`, `ping`, `curl` one-liners are fine).
3. Reference it from tests: `instruments = ["dut", "mytool"]` — that also
   makes it a **lock**: two runs needing `mytool` never run concurrently.
4. Restart BenchBoard — the chip appears and is probed automatically.
