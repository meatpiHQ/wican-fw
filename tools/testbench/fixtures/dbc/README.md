# DBC test fixtures

Real-world DBC files for `tools/testbench/dbc_real_test.py` — the
device's parser (`autopid_dbc_codec.c`) is cross-checked field-by-field
against an independent reference parser over these.

| File | Why |
|---|---|
| `bmw_e9x_e8x.dbc` | small classic layout, many messages, Vector orphan pseudo-message present |
| `tesla_model3_party.dbc` | heavy multiplexing (the listed-with-reason path) |
| `ford_lincoln_base_pt.dbc` | 807 KB / 2150 signals + thousands of `CM_ SG_` comment lines (parser noise + size stress) |
| `j1939_database.dbc` | SAE J1939: **extended 29-bit ids** (bit-31 flag masking), truck PGNs (EEC1/CCVS1/…), sub-byte Intel + signed-with-offset signals |

Sources (fetched 2026-07-08, committed rather than downloaded at test
time so the bench stays deterministic and offline):
- bmw/tesla/ford: https://github.com/commaai/opendbc (`opendbc/dbc/`),
  MIT license.
- j1939: https://github.com/arthithadee/J1939-DBC-Database
  (`j1939_database.dbc`), MIT license.
