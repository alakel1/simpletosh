"""Command line entry point.

  python3 -m oclauncher                 # graphical installer (default)
  python3 -m oclauncher detect [-o r.json]
  python3 -m oclauncher analyze [--report r.json]
  python3 -m oclauncher build --macos 15 --out DIR [--report r.json] [--layout N]
"""

import argparse
import json
import sys

from . import hw, planner


def _report(args):
    return hw.load(args.report) if getattr(args, "report", None) else hw.detect()


def cmd_detect(args):
    r = hw.detect()
    if args.output:
        hw.save(r, args.output)
        print(f"Saved to {args.output}")
    else:
        print(json.dumps(r, indent=2))


def cmd_analyze(args):
    a = planner.analyze(_report(args))
    print("== Hardware")
    for c in a.components:
        rng = f"{c.support.min_macos}–{c.support.max_macos}" if c.support.min_macos else ""
        print(f"  [{c.support.status:12}] {c.category:9} {c.support.summary} {rng}")
        for n in c.support.notes:
            print(f"  {'':14} · {n}")
    print("\n== macOS versions")
    for v in a.verdicts:
        mark = "★" if v.recommended else ("✓" if v.ok else "✗")
        print(f"  {mark} {v.macos.label}")
        for b in v.blockers:
            print(f"      ✗ {b}")
        for c in v.caveats:
            print(f"      ! {c}")
    version = args.macos or (a.recommended.macos.version if a.recommended else None)
    if version:
        print(f"\n== What to expect on macOS {version}")
        print(planner.expectations_text(planner.expectations(a, version)))


def cmd_build(args):
    from . import builder, fetch
    a = planner.analyze(_report(args))
    version = args.macos or (a.recommended.macos.version if a.recommended else None)
    if not version:
        sys.exit("No installable macOS version for this hardware.")
    verdict = next(v for v in a.verdicts if v.macos.version == version)
    if not verdict.ok and not args.force:
        sys.exit(f"{verdict.macos.label} is not installable: {'; '.join(verdict.blockers)} (use --force)")
    plan = planner.make_plan(a, version, tahoe_audio=args.tahoe_audio)
    if plan.audio_codec:
        layouts = fetch.applealc_layouts(plan.audio_codec)
        plan = planner.make_plan(a, version, args.layout or fetch.default_layout(plan.audio_codec, layouts),
                                 tahoe_audio=args.tahoe_audio)
    print(planner.plan_summary(plan))

    last = {"msg": None}
    tty = sys.stdout.isatty()

    def progress(msg, frac=None):
        if tty:
            pct = f" {int(frac * 100):3d}%" if frac is not None else ""
            print(f"\r  {msg}{pct}".ljust(70), end="", flush=True)
        elif msg != last["msg"]:
            print(f"  {msg}")
        last["msg"] = msg

    res = builder.build_efi(plan, a, args.out, progress)
    print()
    ok, out = res["validation"]
    print(f"\nEFI written to {res['efi']} (OpenCore {res['opencore']})")
    print(f"ocvalidate: {'OK' if ok else 'PROBLEMS'}\n{out}")
    for name, err in res["failed_kexts"]:
        print(f"  ! {name}: {err}")
    if args.recovery:
        from . import recovery
        recovery.download(plan.macos, args.out, res["oc_dir"], progress=progress)
        print()


def main(argv=None):
    p = argparse.ArgumentParser(prog="oclauncher")
    sub = p.add_subparsers(dest="cmd")
    d = sub.add_parser("detect", help="detect hardware and print/save the report")
    d.add_argument("-o", "--output")
    an = sub.add_parser("analyze", help="show compatibility analysis")
    an.add_argument("--report")
    an.add_argument("--macos", help="version for the 'what to expect' section (default: recommended)")
    b = sub.add_parser("build", help="build an EFI folder")
    b.add_argument("--report")
    b.add_argument("--macos", help="e.g. 15, 14, 10.15 (default: recommended)")
    b.add_argument("--out", required=True)
    b.add_argument("--layout", type=int)
    b.add_argument("--recovery", action="store_true", help="also download the macOS recovery")
    b.add_argument("--force", action="store_true")
    b.add_argument("--tahoe-audio", action="store_true", help="macOS 26: add the experimental SimpleHDA kext")
    sub.add_parser("gui", help="graphical installer (default)")
    args = p.parse_args(argv)
    if args.cmd == "detect":
        cmd_detect(args)
    elif args.cmd == "analyze":
        cmd_analyze(args)
    elif args.cmd == "build":
        cmd_build(args)
    else:
        from .gui.app import run
        sys.exit(run())


if __name__ == "__main__":
    main()
