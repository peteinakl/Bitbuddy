"""Paper-trading CLI.

    python live_trader.py --once      # evaluate the latest completed bar
    python live_trader.py --loop      # run continuously, one decision per bar
    python live_trader.py --status    # portfolio state
    python live_trader.py --replay    # verify live logic reproduces the backtest

Simulation only: no real money, no credentials, no orders.
"""

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from bitbuddy.live import LiveTrader, replay

LOG_FILE = "live_trader.log"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--replay", action="store_true")
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--force", action="store_true",
                    help="re-process a bar already seen")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
    )

    if args.replay:
        raise SystemExit(0 if replay() else 1)

    trader = LiveTrader(initial_capital=args.capital)

    if args.status:
        print(trader.status())
        return

    if args.once:
        bars = trader.fetch_bars()
        trader.step(bars, force=args.force)
        print(trader.status(float(bars["close"].iloc[-1])))
        return

    logging.info("starting loop; one decision per completed daily bar")
    while True:
        try:
            bars = trader.fetch_bars()
            trader.step(bars)
            print(trader.status(float(bars["close"].iloc[-1])))
        except KeyboardInterrupt:
            logging.info("shutdown requested")
            trader.save()
            print(trader.status())
            break
        except Exception as exc:
            logging.error("cycle failed: %s", exc, exc_info=True)

        # Wake shortly after the next UTC midnight, when the daily bar closes
        now = datetime.now(timezone.utc)
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        sleep_s = max(60, (nxt - now).total_seconds())
        logging.info("next evaluation %s (%.1fh)", nxt.isoformat(), sleep_s / 3600)
        try:
            time.sleep(sleep_s)
        except KeyboardInterrupt:
            logging.info("shutdown requested")
            trader.save()
            break


if __name__ == "__main__":
    main()
