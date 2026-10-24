"""Command-line interface."""

import argparse
import json
import os
import platform
import secrets
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from . import __version__, synthetic
from ._version import ALGORITHM_VERSION
from .config import SECURITY_LEVELS, EngineConfig
from .crypto import KeyringFile, StaticKeyProvider, encode_key, generate_key
from .engine import BiometricEngine, default_home
from .errors import GenIDtalsError
from .stores import FileStore, SQLiteStore


def _keys(args):
    if os.environ.get("GENIDTALS_KEYS", "").strip():
        return StaticKeyProvider.from_env()
    return KeyringFile(os.environ.get("GENIDTALS_KEYRING") or Path(args.home) / "keyring.json")


def _store(args):
    if args.store:
        path = Path(args.store).expanduser()
        return SQLiteStore(path) if path.suffix in (".db", ".sqlite", ".sqlite3") else FileStore(path)
    return FileStore(Path(args.home) / "templates")


def _engine(args) -> BiometricEngine:
    config = EngineConfig(security_level=args.level, calibration=args.calibration,
                          expose_scores=getattr(args, "expose_scores", False))
    return BiometricEngine(config=config, store=_store(args), keys=_keys(args))


def _print(args, data, text):
    print(json.dumps(data, indent=2, default=str) if args.json else text)


def cmd_enroll(args) -> int:
    r = _engine(args).enroll_user(args.user, args.images, overwrite=args.overwrite)
    _print(args, r.__dict__, f"Enrolled {args.user!r} with {r.references} reference image(s).")
    return 0


def cmd_authenticate(args) -> int:
    result = _engine(args).authenticate(args.user, args.image, args.frames or ())
    if result.authenticated:
        text = "Authentication successful."
    else:
        text = f"Authentication failed ({result.reason}): {result.message}."
    if result.confidence is not None:
        text += f" Confidence {result.confidence}% (threshold {result.threshold}%)."
    _print(args, result.to_dict(), text + f" [{result.elapsed_ms:.0f} ms]")
    return 0 if result.authenticated else 1


def cmd_delete(args) -> int:
    deleted = _engine(args).delete_user(args.user)
    _print(args, {"deleted": deleted},
           f"Deleted templates for {args.user!r}." if deleted else f"No templates found for {args.user!r}.")
    return 0 if deleted else 1


def cmd_unlock(args) -> int:
    _engine(args).unlock_user(args.user)
    _print(args, {"unlocked": True}, f"Unlocked {args.user!r}.")
    return 0


def cmd_status(args) -> int:
    engine = _engine(args)
    st = engine.lockout_status(args.user)
    data = {"user_id": args.user, "enrolled": engine.is_enrolled(args.user),
            "locked": st.locked, "locked_until": st.locked_until}
    lock = f"locked until {time.ctime(st.locked_until)}" if st.locked else "not locked"
    _print(args, data, f"{args.user!r}: {'enrolled' if data['enrolled'] else 'not enrolled'}, {lock}")
    return 0


def cmd_quality(args) -> int:
    report = _engine(args).assess_quality(args.image)
    text = f"Quality {report.score:.0f}/100" + (": " + "; ".join(report.issues) if report.issues else "")
    _print(args, report.to_dict(), text)
    return 0


def cmd_diagnostics(args) -> int:
    engine = _engine(args)
    print(f"GenIDtals {__version__} (algorithm v{ALGORITHM_VERSION})")
    print(f"  Python       {platform.python_version()} ({platform.system()} {platform.machine()})")
    print(f"  OpenCV       {cv2.__version__}")
    print(f"  NumPy        {np.__version__}")
    print(f"  Store        {type(engine.store).__name__} ({engine.enrolled_count()} enrolled)")
    print(f"  Keys         {type(engine.keys).__name__}, current {engine.keys.current_key_id}, "
          f"{len(engine.keys.key_ids())} total")
    print(f"  Security     {engine.profile.name} (target FAR {engine.profile.target_far:g}, "
          f"threshold {engine.threshold})")
    print(f"  Calibration  {engine.calibration.source}")

    print("Self-test (synthetic specimens, ephemeral engine):")
    test = BiometricEngine.ephemeral(args.level)
    rng = np.random.default_rng(1234)
    a, b = synthetic.make_specimen(1), synthetic.make_specimen(2)
    n = test.profile.min_enroll_images
    test.enroll_user("self-test", [synthetic.capture(a, rng, hard=True) for _ in range(n)])
    genuine = test.authenticate("self-test", synthetic.capture(a, rng, hard=True))
    impostor = test.authenticate("self-test", synthetic.capture(b, rng, hard=True))
    ok = genuine.authenticated and not impostor.authenticated
    verdict = "accepted" if genuine.authenticated else "REJECTED"
    print(f"  Genuine      {verdict} ({genuine.elapsed_ms:.0f} ms)")
    print(f"  Impostor     {'rejected' if not impostor.authenticated else 'ACCEPTED'}")
    print(f"  Result       {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def cmd_keygen(args) -> int:
    if args.api_key:
        print(secrets.token_urlsafe(32))
    else:
        print(encode_key(generate_key()))
    return 0


def cmd_rotate_keys(args) -> int:
    keys = _keys(args)
    if not args.no_new_key:
        if not isinstance(keys, KeyringFile):
            print("error: keys come from GENIDTALS_KEYS; put a new key first in that variable "
                  "(keeping the old ones), then run `genidtals rotate-keys --no-new-key`.", file=sys.stderr)
            return 2
        print(f"New current key: {keys.rotate()}")
    engine = BiometricEngine(config=EngineConfig(security_level=args.level), store=_store(args), keys=keys)
    print(f"Re-sealed {engine.rotate_keys()} record(s) under key {keys.current_key_id}.")
    return 0


def _report(args, subjects, calibration) -> int:
    from .evaluation import collect_scores, report

    scores = collect_scores(subjects, args.enroll, calibration)
    rep = report(scores, calibration)
    data = rep.to_dict(calibration)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Comparisons   {data['genuine_comparisons']} genuine, {data['impostor_comparisons']} impostor")
    print(f"EER           {data['eer'] * 100:.3f}% (at score {data['eer_threshold']})")
    print(f"Genuine       min {data['genuine_min']}  mean {data['genuine_mean']}")
    print(f"Impostor      max {data['impostor_max']}  mean {data['impostor_mean']}")
    print(f"FTE / FTA     {data['failed_to_enroll']} / {data['failed_to_acquire']}")
    for level, op in data["operating_points"].items():
        print(f"  {level:9s} threshold {op['threshold']:6.2f}  FAR {op['far']:.5f}  FRR {op['frr']:.5f}")
    return 0


def _subjects(args):
    from .evaluation import load_dataset, synthetic_subjects

    if args.dataset:
        return load_dataset(args.dataset)
    return synthetic_subjects(args.subjects, args.images, hard=not args.easy, seed=args.seed)


def cmd_benchmark(args) -> int:
    from .calibration import load_calibration

    return _report(args, _subjects(args), load_calibration(args.calibration))


def cmd_calibrate(args) -> int:
    from .calibration import fit
    from .evaluation import collect_scores

    subjects = _subjects(args)
    scores = collect_scores(subjects, args.enroll)
    if args.dataset:
        source = f"dataset:{Path(args.dataset).name}"
    else:
        source = f"synthetic:{args.subjects}x{args.images}"
    calibration = fit(scores.genuine, scores.impostor, source)
    calibration.save(args.out)
    print(f"Wrote {args.out}")
    print(json.dumps(calibration.to_dict()["thresholds"], indent=2))
    return _report(args, subjects, calibration)


def cmd_serve(args) -> int:
    try:
        import uvicorn

        from .server import create_app
    except ImportError:
        print('error: the server needs extra dependencies: pip install "genidtals[server]"', file=sys.stderr)
        return 2
    app = create_app(_engine(args), allow_unauthenticated=args.insecure_no_auth,
                     max_body_bytes=args.max_body_mb * 1024 * 1024, docs=args.docs)
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.ssl_certfile:
        print("warning: serving on a non-local interface without TLS; put a TLS proxy in front.",
              file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, ssl_certfile=args.ssl_certfile,
                ssl_keyfile=args.ssl_keyfile, server_header=False, log_level="info")
    return 0


def cmd_openapi(args) -> int:
    from .server import openapi_schema

    text = json.dumps(openapi_schema(), indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    else:
        print(text)
    return 0


def cmd_synth(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    specimen = synthetic.make_specimen(args.identity)
    for i in range(args.count):
        path = out / f"specimen{args.identity}_{i + 1}.png"
        cv2.imwrite(str(path), synthetic.capture(specimen, rng, hard=args.hard))
        print(path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="genidtals", description="GenIDtals biometric authentication")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--home", default=str(default_home()),
                   help="data directory holding templates/ and keyring.json (env: GENIDTALS_HOME)")
    p.add_argument("--store", help="template store: a directory, or a .db/.sqlite file for SQLite")
    p.add_argument("--level", default="standard", choices=list(SECURITY_LEVELS), help="security level")
    p.add_argument("--calibration", help="calibration JSON from `genidtals calibrate`")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", required=True)

    def user_cmd(name, func, help_):
        c = sub.add_parser(name, help=help_)
        c.add_argument("--user", required=True)
        c.set_defaults(func=func)
        return c

    e = user_cmd("enroll", cmd_enroll, "enroll a user from reference images")
    e.add_argument("--images", nargs="+", required=True)
    e.add_argument("--overwrite", action="store_true", help="replace an existing enrollment")

    a = user_cmd("authenticate", cmd_authenticate, "verify a user against an image")
    a.add_argument("--image", required=True)
    a.add_argument("--frames", nargs="*", help="additional burst frames for motion liveness")
    a.add_argument("--expose-scores", action="store_true", help="include numeric scores in output")

    user_cmd("delete", cmd_delete, "remove a user's templates")
    user_cmd("unlock", cmd_unlock, "clear a user's lockout")
    user_cmd("status", cmd_status, "show enrollment and lockout state")

    q = sub.add_parser("quality", help="assess capture quality")
    q.add_argument("--image", required=True)
    q.set_defaults(func=cmd_quality)

    sub.add_parser("diagnostics", help="system status and self-test").set_defaults(func=cmd_diagnostics)

    k = sub.add_parser("keygen", help="print a new master key (for GENIDTALS_KEYS) or API key")
    k.add_argument("--api-key", action="store_true", help="generate a server API key instead")
    k.set_defaults(func=cmd_keygen)

    r = sub.add_parser("rotate-keys", help="make a new key current and re-seal all records")
    r.add_argument("--no-new-key", action="store_true", help="only re-seal under the current key")
    r.set_defaults(func=cmd_rotate_keys)

    for name, func, help_ in (("benchmark", cmd_benchmark, "measure accuracy (EER, FAR/FRR)"),
                              ("calibrate", cmd_calibrate, "fit fusion weights and thresholds")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("--dataset", help="directory with one sub-directory of images per subject")
        c.add_argument("--subjects", type=int, default=30, help="synthetic subjects (without --dataset)")
        c.add_argument("--images", type=int, default=6, help="synthetic images per subject")
        c.add_argument("--seed", type=int, default=0)
        c.add_argument("--easy", action="store_true", help="synthetic captures without hard conditions")
        c.add_argument("--enroll", type=int, default=3, help="reference images per subject")
        if name == "calibrate":
            c.add_argument("--out", default="calibration.json")
        c.set_defaults(func=func)

    s = sub.add_parser("serve", help="run the REST API server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--ssl-certfile")
    s.add_argument("--ssl-keyfile")
    s.add_argument("--max-body-mb", type=int, default=64)
    s.add_argument("--docs", action="store_true", help="serve /docs and /openapi.json")
    s.add_argument("--expose-scores", action="store_true", help="include numeric scores in responses")
    s.add_argument("--insecure-no-auth", action="store_true", help="allow running without API keys")
    s.set_defaults(func=cmd_serve)

    o = sub.add_parser("openapi", help="write the REST API's OpenAPI document")
    o.add_argument("--out")
    o.set_defaults(func=cmd_openapi)

    y = sub.add_parser("synth", help="write synthetic specimen images for testing")
    y.add_argument("--identity", type=int, default=1)
    y.add_argument("--count", type=int, default=3)
    y.add_argument("--seed", type=int, default=None)
    y.add_argument("--hard", action="store_true", help="simulate difficult capture conditions")
    y.add_argument("--out", default="samples")
    y.set_defaults(func=cmd_synth)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except GenIDtalsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
