"""Server-side admin commands (run on the server, never paste secrets into chat).

    python -m tradebot.cli hash-password           # prompts; prints an argon2 hash for .env
    python -m tradebot.cli totp-setup              # prints a new TOTP secret + otpauth URI
    python -m tradebot.cli check-broker [--env paper|live]   # read-only connectivity check
    python -m tradebot.cli demo --days 20 [--cash-usd 1300]  # replay demo into TRADEBOT_DATA_DIR
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tradebot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hash-password")
    sub.add_parser("totp-setup")
    cb = sub.add_parser("check-broker")
    cb.add_argument("--env", choices=["paper", "live"], default="paper")
    dm = sub.add_parser("demo")
    dm.add_argument("--days", type=int, default=20)
    dm.add_argument("--start", default="2018-01-29")
    dm.add_argument("--cash-usd", type=float, default=1300.0)
    a = ap.parse_args(argv)

    if a.cmd == "hash-password":
        from .auth import hash_password
        pw = getpass.getpass("New admin password (min 12 chars): ")
        if len(pw) < 12 or pw != getpass.getpass("Repeat: "):
            print("Passwords must match and be at least 12 characters.", file=sys.stderr)
            return 1
        print(hash_password(pw))
        return 0

    if a.cmd == "totp-setup":
        import pyotp
        secret = pyotp.random_base32()
        print("Add to your server secrets as TRADEBOT_TOTP_SECRET, and to your authenticator app:")
        print(secret)
        print(pyotp.TOTP(secret).provisioning_uri(name="admin", issuer_name="Tradebot"))
        return 0

    if a.cmd == "check-broker":
        from .config import Settings
        from .runtime import Runtime
        s = Settings.from_env()
        rt = Runtime(s)
        out: dict = {"broker": s.broker, "env": a.env}
        try:
            b = rt.broker(a.env)
            acct = b.get_account()
            out["account"] = {"currency": acct.currency, "status": acct.status, "trading_blocked": acct.trading_blocked,
                              "cash": str(acct.cash), "equity": str(acct.equity)}
            clk = b.get_clock()
            out["clock"] = None if clk is None else {"is_open": clk.is_open, "next_open": str(clk.next_open)}
            out["positions"] = len(b.get_positions())
            out["open_orders"] = len(b.list_open_orders())
            for sym in ("SPY", "QQQ"):
                asset = b.get_asset(sym)
                out[f"asset_{sym}"] = {"tradable": asset.tradable, "fractionable": asset.fractionable}
            q = rt.data().latest_quote("SPY")
            out["quote_SPY"] = {"bid": str(q.bid), "ask": str(q.ask), "ts": q.ts.isoformat(), "source": q.source,
                                "feed": q.feed, "replay": rt.data().is_replay}
            out["result"] = "OK - read-only checks passed (no orders were placed)"
        except Exception as e:  # noqa: BLE001
            out["result"] = f"FAILED: {type(e).__name__}: {e}"
        print(json.dumps(out, indent=2))
        return 0 if out["result"].startswith("OK") else 2

    if a.cmd == "demo":
        from .clock import SimClock
        from .config import Settings
        from .data.replay import replay_start
        from .runtime import Runtime
        s = Settings.from_env()
        s = s.model_copy(update={"broker": "sim", "market_data": "replay", "sim_starting_cash_usd": a.cash_usd})
        rt = Runtime(s, clock=SimClock(replay_start(a.start, 13, 0)))
        eng = rt.engine()
        eng.command("start")
        for _ in range(a.days * 288):
            rt.tick_once()
            rt.clock.advance(minutes=5)
        rt.control.set("replay_clock", rt.clock.now().date().isoformat())
        n = eng.store.one("SELECT COUNT(*) AS n FROM fills")["n"]
        print(f"Replay demo complete: {a.days} days, {n} fills, data in {s.data_dir}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
