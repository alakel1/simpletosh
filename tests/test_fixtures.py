"""For every fixture: analyze, plan the recommended macOS, build the EFI, run ocvalidate.

Run: python3 tests/test_fixtures.py [--no-build] [name-filter]
Downloads are cached in $OCL_CACHE (default ~/.cache/oclauncher).
"""

import glob
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oclauncher import builder, fetch, hw, planner  # noqa: E402

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def main():
    build = "--no-build" not in sys.argv
    filt = [a for a in sys.argv[1:] if not a.startswith("--")]
    oc = fetch.opencore() if build else None
    failures = 0
    for path in sorted(glob.glob(os.path.join(HERE, "*.json"))):
        name = os.path.basename(path)[:-5]
        if filt and not any(f in name for f in filt):
            continue
        a = planner.analyze(hw.load(path))
        rec = a.recommended
        ok_versions = [v.macos.version for v in a.installable]
        print(f"\n### {name}\n  platform={a.platform} laptop={a.laptop} installable={ok_versions} "
              f"recommended={rec.macos.version if rec else None}")
        if not rec:
            for v in a.verdicts[-1:]:
                print("  blockers:", v.blockers)
            continue
        for version in sorted({rec.macos.version, ok_versions[-1]}):
            plan = planner.make_plan(a, version, 1)
            print(f"  [{version}] smbios={plan.smbios} kexts={[k.name for k in plan.kexts]}")
            print(f"         ssdts={[s.name for s in plan.ssdts]} args={' '.join(plan.boot_args)}")
            if not build:
                continue
            with tempfile.TemporaryDirectory() as tmp:
                res = builder.build_efi(plan, a, tmp, oc=oc)
                ok, out = res["validation"]
                issues = [l for l in out.splitlines() if l.startswith(("OC", "Serial")) and "NOTE" not in l]
                status = "OK" if ok else "FAIL"
                print(f"         ocvalidate={status} failed_kexts={[k for k, _ in res['failed_kexts']]}")
                if not ok or res["failed_kexts"]:
                    failures += 1
                    for l in issues[:15]:
                        print("           ", l)
                    for k, e in res["failed_kexts"]:
                        print("           ", k, e[:200])
    print(f"\n{'ALL GOOD' if not failures else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
