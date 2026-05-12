# IBIT ETF Arbitrage Simulator

Simulates creation/redemption arbitrage for the iShares Bitcoin Trust (IBIT) ETF.

## Phase 1: Historical Economic Analysis

Analyzes historical IBIT premium/discount vs estimated NAV (from BTC spot price) and models arb P&L with realistic costs.

```bash
pip install yfinance pandas numpy matplotlib pyyaml
python ibit_arb_model.py
```

**Outputs:**
- Console summary: premium/discount stats, opportunity count, P&L analysis
- `premium_discount_timeseries.png` — spread over time with entry signals
- `premium_discount_histogram.png` — distribution of premium/discount
- `cumulative_pnl.png` — cumulative arb P&L

## Phase 2: Live IBKR Simulator

Real-time monitoring of IBIT vs BTC with simulated trade execution.

**Prerequisites:** IBKR TWS or IB Gateway running (paper trading on port 7497)

```bash
pip install ib_insync
python ibit_arb_simulator.py           # Live (requires IBKR)
python ibit_arb_simulator.py --dry-run  # Simulated prices for testing
```

**Outputs:**
- Live console dashboard (refreshes every 5s)
- `trades.csv` — log of all simulated arb trades

## Configuration

Edit `config.yaml` to adjust:
- Cost assumptions (fees, commissions, market impact)
- Signal thresholds
- IBKR connection settings
- Analysis date range

## How the Arb Works

IBIT is a **cash-create/redeem** spot Bitcoin ETF:

- **Premium (create):** IBIT trades above NAV → Buy BTC as hedge, create ETF shares via AP, sell shares at premium
- **Discount (redeem):** IBIT trades below NAV → Buy cheap IBIT shares, redeem for cash at NAV, sell BTC hedge

The simulator monitors the spread and signals when it exceeds transaction costs + minimum threshold.
