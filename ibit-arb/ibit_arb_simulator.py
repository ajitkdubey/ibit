#!/usr/bin/env python3
"""
IBIT ETF Creation/Redemption Arbitrage — Phase 2: Live IBKR Simulator

Connects to IBKR TWS/Gateway (paper trading) and monitors real-time IBIT
premium/discount vs BTC spot, logging simulated arb trades.

Usage:
  python ibit_arb_simulator.py              # Live mode (requires IBKR TWS/Gateway)
  python ibit_arb_simulator.py --dry-run    # Simulated prices for testing
"""

import os
import sys
import csv
import time
import signal
import random
import argparse
import yaml
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Config & logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ibit-arb")


def load_config(path: str = "config.yaml") -> Dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MarketState:
    ibit_bid: float = 0.0
    ibit_ask: float = 0.0
    ibit_last: float = 0.0
    btc_price: float = 0.0
    timestamp: Optional[datetime] = None

    @property
    def ibit_mid(self) -> float:
        if self.ibit_bid > 0 and self.ibit_ask > 0:
            return (self.ibit_bid + self.ibit_ask) / 2
        return self.ibit_last

    @property
    def nav_estimate(self) -> float:
        return self.btc_price * self._ratio if self.btc_price > 0 else 0.0

    _ratio: float = 0.0

    @property
    def prem_disc_bps(self) -> float:
        nav = self.nav_estimate
        if nav <= 0:
            return 0.0
        return (self.ibit_mid - nav) / nav * 10_000


@dataclass
class Trade:
    timestamp: str
    signal_type: str   # "CREATE" or "REDEEM"
    ibit_price: float
    btc_price: float
    nav_estimate: float
    spread_bps: float
    estimated_pnl: float


@dataclass
class Session:
    trades: List[Trade] = field(default_factory=list)
    start_time: Optional[datetime] = None

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def total_pnl(self) -> float:
        return sum(t.estimated_pnl for t in self.trades)

    @property
    def win_count(self) -> int:
        return sum(1 for t in self.trades if t.estimated_pnl > 0)

    @property
    def win_rate(self) -> float:
        if self.trade_count == 0:
            return 0.0
        return self.win_count / self.trade_count * 100


# ---------------------------------------------------------------------------
# IBKR Live Feed
# ---------------------------------------------------------------------------

class IBKRFeed:
    """Real-time data from IBKR TWS/Gateway via ib_insync."""

    def __init__(self, cfg: Dict):
        from ib_insync import IB, Stock, Crypto
        self.ib = IB()
        self.cfg = cfg
        self.ibit_contract = Stock(cfg["etf"]["ticker"], "SMART", "USD")
        # IBKR crypto — try PAXOS exchange
        self.btc_contract = Crypto("BTC", "PAXOS", "USD")
        self.state = MarketState()

    def connect(self) -> None:
        ibcfg = self.cfg["ibkr"]
        log.info(f"Connecting to IBKR at {ibcfg['host']}:{ibcfg['port']} ...")
        self.ib.connect(ibcfg["host"], ibcfg["port"], clientId=ibcfg["client_id"])
        log.info("Connected to IBKR")

        # Qualify contracts
        self.ib.qualifyContracts(self.ibit_contract)
        self.ib.qualifyContracts(self.btc_contract)

        # Subscribe to market data
        self.ib.reqMktData(self.ibit_contract)
        self.ib.reqMktData(self.btc_contract)
        log.info("Subscribed to IBIT and BTC market data")

    def update(self) -> MarketState:
        self.ib.sleep(0.1)  # Process events
        ibit_ticker = self.ib.ticker(self.ibit_contract)
        btc_ticker = self.ib.ticker(self.btc_contract)

        if ibit_ticker:
            self.state.ibit_bid = ibit_ticker.bid or 0.0
            self.state.ibit_ask = ibit_ticker.ask or 0.0
            self.state.ibit_last = ibit_ticker.last or 0.0
        if btc_ticker:
            self.state.btc_price = btc_ticker.last or btc_ticker.bid or 0.0

        self.state.timestamp = datetime.now()
        return self.state

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()
            log.info("Disconnected from IBKR")


class DryRunFeed:
    """Simulated price feed for testing without IBKR."""

    def __init__(self, cfg: Dict):
        self.cfg = cfg
        self.state = MarketState()
        # Start with realistic base prices
        self._btc_base = 85000.0
        self._ibit_ratio = 0.0005  # rough IBIT/BTC ratio

    def connect(self) -> None:
        log.info("[DRY RUN] Using simulated price feed")
        self.state._ratio = self._ibit_ratio

    def update(self) -> MarketState:
        # Random walk BTC
        self._btc_base *= (1 + random.gauss(0, 0.001))
        btc = self._btc_base

        # IBIT tracks BTC with some noise (simulates premium/discount)
        noise_bps = random.gauss(0, 30)  # ±30bps noise to create arb signals
        ibit_fair = btc * self._ibit_ratio
        ibit_mid = ibit_fair * (1 + noise_bps / 10_000)
        spread = ibit_mid * 0.0003  # 3bps bid-ask spread

        self.state.btc_price = btc
        self.state.ibit_bid = ibit_mid - spread / 2
        self.state.ibit_ask = ibit_mid + spread / 2
        self.state.ibit_last = ibit_mid
        self.state._ratio = self._ibit_ratio
        self.state.timestamp = datetime.now()
        return self.state

    def disconnect(self) -> None:
        log.info("[DRY RUN] Feed stopped")


# ---------------------------------------------------------------------------
# Core simulator
# ---------------------------------------------------------------------------

class ArbitrageSimulator:
    def __init__(self, cfg: Dict, feed, ratio: float):
        self.cfg = cfg
        self.feed = feed
        self.session = Session(start_time=datetime.now())
        self.ratio = ratio
        self.running = True

        # Cost model
        costs = cfg["costs"]
        cu = cfg["etf"]["creation_unit_shares"]
        # We'll compute fee_bps dynamically based on current price
        self.cost_params = costs
        self.cu = cu
        self.threshold_bps = cfg["signals"]["min_spread_after_costs_bps"]

        # CSV log
        self.csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.csv")
        self._init_csv()

    def _init_csv(self) -> None:
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "signal", "ibit_price", "btc_price",
                    "nav_estimate", "spread_bps", "pnl_usd",
                ])

    def _total_cost_bps(self, ibit_price: float) -> float:
        c = self.cost_params
        fee_bps = (c["creation_redemption_fee_usd"] / (self.cu * ibit_price)) * 10_000
        comm_bps = (c["etf_commission_per_share"] / ibit_price) * 10_000
        return (
            fee_bps
            + comm_bps
            + c["btc_execution_bps"]
            + c["market_impact_bps"] * 2
            + c["btc_spot_spread_bps"]
        )

    def check_signal(self, state: MarketState) -> Optional[Trade]:
        state._ratio = self.ratio
        nav = state.nav_estimate
        if nav <= 0 or state.ibit_mid <= 0:
            return None

        prem_bps = state.prem_disc_bps
        cost_bps = self._total_cost_bps(state.ibit_mid)
        trigger = cost_bps + self.threshold_bps

        if abs(prem_bps) > trigger:
            sig = "CREATE" if prem_bps > 0 else "REDEEM"
            spread = abs(prem_bps) - cost_bps
            pnl = spread / 10_000 * self.cu * state.ibit_mid

            trade = Trade(
                timestamp=datetime.now().isoformat(),
                signal_type=sig,
                ibit_price=state.ibit_mid,
                btc_price=state.btc_price,
                nav_estimate=nav,
                spread_bps=spread,
                estimated_pnl=pnl,
            )
            return trade
        return None

    def log_trade(self, trade: Trade) -> None:
        self.session.trades.append(trade)
        with open(self.csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                trade.timestamp, trade.signal_type, f"{trade.ibit_price:.4f}",
                f"{trade.btc_price:.2f}", f"{trade.nav_estimate:.4f}",
                f"{trade.spread_bps:.2f}", f"{trade.estimated_pnl:.2f}",
            ])
        log.info(
            f"{'🟢' if trade.signal_type == 'CREATE' else '🔴'} {trade.signal_type} signal | "
            f"Spread: {trade.spread_bps:.1f} bps | P&L: ${trade.estimated_pnl:,.2f}"
        )

    def print_dashboard(self, state: MarketState) -> None:
        state._ratio = self.ratio
        nav = state.nav_estimate
        prem = state.prem_disc_bps
        cost = self._total_cost_bps(state.ibit_mid) if state.ibit_mid > 0 else 0
        trigger = cost + self.threshold_bps

        if prem > trigger:
            signal_str = "🟢 CREATE_SIGNAL"
        elif prem < -trigger:
            signal_str = "🔴 REDEEM_SIGNAL"
        else:
            signal_str = "⚪ NEUTRAL"

        # Clear screen and print
        os.system("clear" if os.name != "nt" else "cls")
        ts = state.timestamp.strftime("%H:%M:%S") if state.timestamp else "N/A"
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  IBIT ARBITRAGE MONITOR              {ts:>12}           ║
╠══════════════════════════════════════════════════════════════╣
║  IBIT  Bid: ${state.ibit_bid:>10.4f}   Ask: ${state.ibit_ask:>10.4f}  Mid: ${state.ibit_mid:>10.4f}  ║
║  BTC   Price: ${state.btc_price:>12.2f}                                  ║
║  NAV   Est:   ${nav:>12.4f}                                  ║
╠══════════════════════════════════════════════════════════════╣
║  Premium/Discount: {prem:>+8.1f} bps                                ║
║  Cost threshold:   {trigger:>+8.1f} bps                                ║
║  Signal: {signal_str:<20}                                ║
╠══════════════════════════════════════════════════════════════╣
║  Session Trades: {self.session.trade_count:>4}   Win Rate: {self.session.win_rate:>5.1f}%                   ║
║  Session P&L:    ${self.session.total_pnl:>12,.2f}                             ║
╚══════════════════════════════════════════════════════════════╝
        """)

    def run(self) -> None:
        log.info("Starting arbitrage monitor...")
        self.feed.connect()

        # Set up graceful shutdown
        def shutdown(sig, frame):
            self.running = False
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        tick = 0
        try:
            while self.running:
                state = self.feed.update()

                # Check for arb signal
                trade = self.check_signal(state)
                if trade:
                    self.log_trade(trade)

                # Dashboard every 5 seconds
                if tick % 5 == 0:
                    self.print_dashboard(state)

                time.sleep(1)
                tick += 1

        except KeyboardInterrupt:
            pass
        finally:
            self.feed.disconnect()
            self._print_final_summary()

    def _print_final_summary(self) -> None:
        s = self.session
        elapsed = (datetime.now() - s.start_time).total_seconds() / 60 if s.start_time else 0
        print(f"\n{'='*55}")
        print(f"  SESSION SUMMARY")
        print(f"{'='*55}")
        print(f"  Duration      : {elapsed:.1f} minutes")
        print(f"  Total trades  : {s.trade_count}")
        print(f"  Win rate      : {s.win_rate:.1f}%")
        print(f"  Total P&L     : ${s.total_pnl:,.2f}")
        if s.trade_count > 0:
            print(f"  Avg P&L/trade : ${s.total_pnl / s.trade_count:,.2f}")
        print(f"  Trade log     : {self.csv_path}")
        print(f"{'='*55}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="IBIT Arbitrage Live Simulator")
    parser.add_argument("--dry-run", action="store_true", help="Use simulated prices (no IBKR required)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    cfg = load_config(args.config)

    # Estimate IBIT/BTC ratio (use approximate current value)
    # In production you'd fetch this from IBIT holdings data
    ratio = 0.000588  # ~1 IBIT share ≈ 0.000588 BTC (approximate)

    if args.dry_run:
        feed = DryRunFeed(cfg)
        feed._ibit_ratio = ratio
    else:
        feed = IBKRFeed(cfg)

    sim = ArbitrageSimulator(cfg, feed, ratio=ratio)
    sim.run()


if __name__ == "__main__":
    main()
