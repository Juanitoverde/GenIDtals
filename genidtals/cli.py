"""Command-line interface."""

import argparse
import json
import platform
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from . import __version__, synthetic
from .config import SECURITY_LEVELS
from .engine import DEFAULT_STORE, BiometricEngine, EnrollmentError
from .preprocessing import ImageError
from .storage import StorageError


def _engine(args) -> BiometricEngine:
    return BiometricEngine(security_level=args.level, store_dir=args.store)


def cmd_enroll(args) -> int:
    _engine(args).enroll_user(args.user, args.images, overwrite=args.overwrite)
    print(f"Enrolled {args.user!r} with {len(args.images)} reference image(s).")
    return 0


def cmd_authenticate(args) -> int:
    result = _engine(args).authenticate(args.user, args.image, args.frames or ())
    if args.json:
        out = {k: v for k, v in result.__dict__.items() if k != "liveness"}
        if result.liveness:
            out["liveness"] = result.liveness.__dict__
        print(json.dumps(out, indent=2))
    elif result.authenticated:
        print(f"Authentication successful. Confidence: {result.confidence}% "
              f"({result.elapsed_ms:.0f} ms)")
    else:
        print(f"Authentication failed: {result.reason}. Confidence: {result.confidence}% "
              f"(threshold {result.threshold}%)")
    return 0 if result.authenticated else 1


def cmd_delete(args) -> int:
    if _engine(args).delete_user(args.user):
        print(f"Deleted templates for {args.user!r}.")
        return 0
    print(f"No templates found for {args.user!r}.")
    return 1


def cmd_diagnostics(args) -> int:
    engine = _engine(args)
    print(f"GenIDtals {__version__}")
    print(f"  Python      {platform.python_version()} ({platform.system()} {platform.machine()})")
    print(f"  OpenCV      {cv2.__version__}")
    print(f"  NumPy       {np.__version__}")
    print(f"  Store       {engine.store.root} ({engine.store.count()} enrolled)")
    print(f"  Security    {engine.profile.name} (threshold {engine.profile.match_threshold}%)")

    print("Self-test (synthetic specimens):")
    rng = np.random.default_rng(1234)
    a, b = synthetic.make_specimen(1), synthetic.make_specimen(2)
    ref = engine.extract_template(synthetic.capture(a, rng))
    from .matching import compare, fuse

    t0 = time.perf_counter()
    probe = engine.extract_template(synthetic.capture(a, rng))
    genuine = fuse(compare(probe, ref))
    elapsed = (time.perf_counter() - t0) * 1000
    impostor = fuse(compare(engine.extract_template(synthetic.capture(b, rng)), ref))
    ok = genuine >= engine.profile.match_threshold > impostor
    print(f"  Genuine score   {genuine:6.2f}%")
    print(f"  Impostor score  {impostor:6.2f}%")
    print(f"  Probe pipeline  {elapsed:6.1f} ms")
    print(f"  Result          {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def cmd_synth(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    specimen = synthetic.make_specimen(args.identity)
    for i in range(args.count):
        path = out / f"specimen{args.identity}_{i + 1}.png"
        cv2.imwrite(str(path), synthetic.capture(specimen, rng))
        print(path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="genidtals", description="GenIDtals biometric authentication")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--store", default=str(DEFAULT_STORE), help="template store directory")
    p.add_argument("--level", default="standard", choices=list(SECURITY_LEVELS),
                   help="security level")
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("enroll", help="enroll a user from reference images")
    e.add_argument("--user", required=True)
    e.add_argument("--images", nargs="+", required=True)
    e.add_argument("--overwrite", action="store_true", help="replace an existing enrollment")
    e.set_defaults(func=cmd_enroll)

    a = sub.add_parser("authenticate", help="verify a user against an image")
    a.add_argument("--user", required=True)
    a.add_argument("--image", required=True)
    a.add_argument("--frames", nargs="*", help="additional burst frames for motion liveness")
    a.add_argument("--json", action="store_true", help="machine-readable output")
    a.set_defaults(func=cmd_authenticate)

    d = sub.add_parser("delete", help="remove a user's templates")
    d.add_argument("--user", required=True)
    d.set_defaults(func=cmd_delete)

    sub.add_parser("diagnostics", help="system status and self-test").set_defaults(func=cmd_diagnostics)

    s = sub.add_parser("synth", help="write synthetic specimen images for testing")
    s.add_argument("--identity", type=int, default=1)
    s.add_argument("--count", type=int, default=3)
    s.add_argument("--seed", type=int, default=None)
    s.add_argument("--out", default="samples")
    s.set_defaults(func=cmd_synth)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (EnrollmentError, ImageError, StorageError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
