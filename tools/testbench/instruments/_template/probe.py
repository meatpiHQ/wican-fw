#!/usr/bin/env python3
"""Template health probe. Contract (plan §5b): exit 0 = healthy, LAST
stdout line = status shown on the BenchBoard chip, side-effect free."""
import argparse
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--port", required=True)
args = ap.parse_args()

# ... talk to the instrument read-only here ...
ok = False
if ok:
    print(f"MYTOOL OK {args.port}")
    sys.exit(0)
print(f"MYTOOL absent on {args.port}")
sys.exit(1)
