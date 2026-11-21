#!/usr/bin/env python3
"""Regenerate oclauncher/data/kext_index.json from the latest kext releases.

Run from the repo root: python3 tools/update_kext_index.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oclauncher import kextdb  # noqa: E402

index = kextdb.build(lambda m, f=None: None)
with open(kextdb.SNAPSHOT, "w") as f:
    json.dump(index, f, indent=1)
for name, data in index["kexts"].items():
    print(f"{name:24} {data['kind']:10} pci={len(data['pci']):3} usb={len(data['usb']):3}")
missing = set(kextdb.INDEXED) - set(index["kexts"])
if missing:
    print("missing:", ", ".join(sorted(missing)))
    sys.exit(1)
