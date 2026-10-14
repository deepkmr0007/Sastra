"""SASTRA command line.

  python -m sastra serve [--host 127.0.0.1] [--port 8787]   API + in-process worker
  python -m sastra worker                                   standalone worker
  python -m sastra migrate                                  apply DB migrations
  python -m sastra setup-code                               show the one-time owner setup code
  python -m sastra gen-key                                  print a new SASTRA_DATA_KEY
  python -m sastra backup                                   encrypted backup now
  python -m sastra restore FILE                             restore (server must be stopped)
  python -m sastra verify-audit                             check the audit hash chain
  python -m sastra status                                   print system status
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sastra")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default=os.environ.get("SASTRA_HOST", "127.0.0.1"))
    sv.add_argument("--port", type=int, default=int(os.environ.get("SASTRA_PORT", "8787")))
    for name in ("worker", "migrate", "setup-code", "gen-key", "backup", "verify-audit", "status"):
        sub.add_parser(name)
    rs = sub.add_parser("restore")
    rs.add_argument("file")
    args = ap.parse_args(argv)
    logging.basicConfig(level=os.environ.get("SASTRA_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "gen-key":
        from .security.crypto import generate_data_key
        print(generate_data_key())
        return 0
    if args.cmd == "restore":
        from .backup import restore_backup
        from .config import load_settings
        s = load_settings()
        if not s.data_key_b64:
            print("SASTRA_DATA_KEY is required to restore", file=sys.stderr)
            return 2
        print(json.dumps(restore_backup(Path(args.file), s.data_dir, s.data_key_b64), indent=2))
        return 0

    from .core import App
    app = App()

    if args.cmd == "migrate":
        print("Migrations up to date")
        return 0
    if args.cmd == "setup-code":
        code = app.auth.setup_code()
        print(code or "Owner already exists — no setup code needed")
        return 0
    if args.cmd == "backup":
        from .backup import create_backup
        print(json.dumps(create_backup(app), indent=2))
        return 0
    if args.cmd == "verify-audit":
        ok, bad = app.audit.verify()
        print("Audit chain intact" if ok else f"AUDIT CHAIN BROKEN at seq {bad}")
        return 0 if ok else 1
    if args.cmd == "status":
        print(json.dumps(app.status(), indent=2, default=str))
        return 0

    from .worker import Worker
    worker = Worker(app, app.settings.worker_poll_s)
    if args.cmd == "worker":
        signal.signal(signal.SIGTERM, lambda *_: worker.stop())
        try:
            worker.loop()
        except KeyboardInterrupt:
            worker.stop()
        return 0

    # serve
    import uvicorn

    from .api.app import build
    code = app.auth.setup_code()
    if code:
        logging.getLogger("sastra").warning("No owner yet. One-time setup code: %s  (use it in the app's first-run screen)", code)
    if not app.cipher.enabled:
        logging.getLogger("sastra").warning("SASTRA_DATA_KEY not set: encryption at rest, TOTP and backups are disabled")
    if app.sandbox is None:
        logging.getLogger("sastra").warning("No code sandbox available (install Docker). Code execution tools will refuse to run.")
    if app.settings.worker_in_process:
        worker.start()
    try:
        uvicorn.run(build(app), host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1",
                    server_header=False, log_level="info")
    finally:
        worker.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
