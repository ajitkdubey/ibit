#!/usr/bin/env python3
"""
IBIT ETF Creation/Redemption Arbitrage — Phase 1: Historical Economic Analysis

Analyzes historical premium/discount of IBIT vs estimated NAV (derived from BTC spot)
and models arbitrage P&L with realistic cost assumptions.
"""

import os
import sys
import yaml
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime
from typing import Dict, Tuple, Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> Dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_data(ticker: str, start: str, end: Optional[str] = None) -> pd.DataFrame:
    """Fetch daily OHLCV from yfinance."""
    import yfinance as yf
    try:
        df = yf.download(ticker, start=start, end=end, progress=False)
        if df.empty:
            raise ValueError(f"No data returned for {ticker}")
        # yfinance >= 0.2.31 may return MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"[ERROR] Failed to fetch {ticker}: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Core calculations
# ---------------------------------------------------------------------------

def build_analysis(cfg: Dict) -> pd.DataFrame:
    """
    Merge IBIT and BTC data, estimate NAV, compute premium/discount and arb P&L.
    Returns a DataFrame with all analysis columns.
    """
    start = cfg["analysis"]["start_date"]
    end = cfg["analysis"].get("end_date")  # None → today

    print(f"Fetching IBIT data from {start} ...")
    ibit = fetch_data(cfg["etf"]["ticker"], start, end)
    print(f"  → {len(ibit)} trading days")

    print(f"Fetching BTC-USD data from {start} ...")
    btc = fetch_data(cfg["bitcoin"]["ticker"], start, end)
    print(f"  → {len(btc)} days")

    # Merge on date (inner join — only dates both traded)
    df = pd.DataFrame({
        "ibit_close": ibit["Close"].squeeze(),
        "ibit_volume": ibit["Volume"].squeeze(),
        "btc_close": btc["Close"].squeeze(),
    }).dropna()

    # Estimate shares-per-BTC ratio from first trading day
    # IBIT shares outstanding / BTC held ≈ constant scaling factor
    first_ibit = float(df["ibit_close"].iloc[0])
    first_btc = float(df["btc_close"].iloc[0])
    ratio = first_ibit / first_btc
    print(f"Estimated IBIT/BTC ratio (day 1): {ratio:.8f}")

    # NAV estimate
    df["nav_estimate"] = df["btc_close"] * ratio

    # Premium / discount (in bps)
    df["prem_disc_pct"] = (df["ibit_close"] - df["nav_estimate"]) / df["nav_estimate"]
    df["prem_disc_bps"] = df["prem_disc_pct"] * 10_000

    # ---- Cost model (all in bps) ----
    costs = cfg["costs"]
    cu = cfg["etf"]["creation_unit_shares"]

    # Creation/redemption fee as bps of creation unit notional
    avg_price = df["ibit_close"].mean()
    fee_bps = (costs["creation_redemption_fee_usd"] / (cu * avg_price)) * 10_000

    # ETF commission as bps (commission_per_share / avg_price * 10000)
    comm_bps = (costs["etf_commission_per_share"] / avg_price) * 10_000

    total_cost_bps = (
        fee_bps
        + comm_bps
        + costs["btc_execution_bps"]
        + costs["market_impact_bps"] * 2   # both legs
        + costs["btc_spot_spread_bps"]
    )

    print(f"\n--- Cost Breakdown (bps) ---")
    print(f"  Create/Redeem fee : {fee_bps:>6.2f}")
    print(f"  ETF commission    : {comm_bps:>6.2f}")
    print(f"  BTC execution     : {costs['btc_execution_bps']:>6.2f}")
    print(f"  Market impact (x2): {costs['market_impact_bps']*2:>6.2f}")
    print(f"  BTC spot spread   : {costs['btc_spot_spread_bps']:>6.2f}")
    print(f"  TOTAL round-trip  : {total_cost_bps:>6.2f} bps")

    df["total_cost_bps"] = total_cost_bps

    # Signals
    threshold_bps = cfg["signals"]["min_spread_after_costs_bps"]
    trigger_bps = total_cost_bps + threshold_bps

    df["create_signal"] = df["prem_disc_bps"] > trigger_bps
    df["redeem_signal"] = df["prem_disc_bps"] < -trigger_bps

    # P&L per trade (in USD)
    # Spread captured = |prem_disc_bps| - total_cost_bps
    df["spread_captured_bps"] = np.where(
        df["create_signal"] | df["redeem_signal"],
        df["prem_disc_bps"].abs() - total_cost_bps,
        0.0,
    )
    df["pnl_per_trade"] = df["spread_captured_bps"] / 10_000 * cu * df["ibit_close"]

    return df


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_summary(df: pd.DataFrame, cfg: Dict) -> None:
    cu = cfg["etf"]["creation_unit_shares"]
    avg_price = df["ibit_close"].mean()
    capital = cu * avg_price

    creates = df[df["create_signal"]]
    redeems = df[df["redeem_signal"]]
    trades = df[df["create_signal"] | df["redeem_signal"]]
    total_days = len(df)
    years = total_days / 252

    print(f"\n{'='*55}")
    print(f" IBIT ARBITRAGE — HISTORICAL ANALYSIS")
    print(f"{'='*55}")
    print(f" Period          : {df.index[0].strftime('%Y-%m-%d')} → {df.index[-1].strftime('%Y-%m-%d')}")
    print(f" Trading days    : {total_days}")
    print(f" Years           : {years:.2f}")
    print(f"")
    print(f" Premium/Discount Stats (bps):")
    print(f"   Mean           : {df['prem_disc_bps'].mean():>8.2f}")
    print(f"   Median         : {df['prem_disc_bps'].median():>8.2f}")
    print(f"   Std Dev        : {df['prem_disc_bps'].std():>8.2f}")
    print(f"   Min            : {df['prem_disc_bps'].min():>8.2f}")
    print(f"   Max            : {df['prem_disc_bps'].max():>8.2f}")
    print(f"")
    print(f" Actionable Opportunities:")
    print(f"   Create signals : {len(creates):>6}  ({len(creates)/total_days*100:.1f}% of days)")
    print(f"   Redeem signals : {len(redeems):>6}  ({len(redeems)/total_days*100:.1f}% of days)")
    print(f"   Total trades   : {len(trades):>6}  ({len(trades)/total_days*100:.1f}% of days)")
    print(f"")

    if len(trades) > 0:
        avg_pnl = trades["pnl_per_trade"].mean()
        total_pnl = trades["pnl_per_trade"].sum()
        annual_pnl = total_pnl / years if years > 0 else total_pnl
        annual_return = annual_pnl / capital * 100
        avg_spread = trades["spread_captured_bps"].mean()

        print(f" P&L Analysis (creation unit = {cu:,} shares):")
        print(f"   Capital required : ${capital:>14,.2f}")
        print(f"   Avg spread capt. : {avg_spread:>8.2f} bps")
        print(f"   Avg P&L / trade  : ${avg_pnl:>14,.2f}")
        print(f"   Total P&L        : ${total_pnl:>14,.2f}")
        print(f"   Annualized P&L   : ${annual_pnl:>14,.2f}")
        print(f"   Annualized Return: {annual_return:>8.2f}%")
    else:
        print(f" No actionable opportunities found at current thresholds.")

    print(f"{'='*55}")


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def generate_charts(df: pd.DataFrame, cfg: Dict, output_dir: str = ".") -> None:
    total_cost_bps = df["total_cost_bps"].iloc[0]
    threshold_bps = cfg["signals"]["min_spread_after_costs_bps"]
    trigger_bps = total_cost_bps + threshold_bps

    # --- 1. Premium/Discount Time Series ---
    fig, ax = plt.subplots(figsize=(16, 6))
    ax.plot(df.index, df["prem_disc_bps"], linewidth=0.8, color="steelblue", alpha=0.8, label="Premium/Discount")
    ax.axhline(trigger_bps, color="green", linestyle="--", linewidth=0.8, alpha=0.7, label=f"Create threshold (+{trigger_bps:.0f} bps)")
    ax.axhline(-trigger_bps, color="red", linestyle="--", linewidth=0.8, alpha=0.7, label=f"Redeem threshold (-{trigger_bps:.0f} bps)")
    ax.axhline(0, color="gray", linestyle="-", linewidth=0.5, alpha=0.5)

    creates = df[df["create_signal"]]
    redeems = df[df["redeem_signal"]]
    if len(creates) > 0:
        ax.scatter(creates.index, creates["prem_disc_bps"], color="green", s=20, zorder=5, label=f"Create signal ({len(creates)})")
    if len(redeems) > 0:
        ax.scatter(redeems.index, redeems["prem_disc_bps"], color="red", s=20, zorder=5, label=f"Redeem signal ({len(redeems)})")

    ax.set_title("IBIT Premium/Discount vs Estimated NAV", fontsize=14, fontweight="bold")
    ax.set_ylabel("Basis Points (bps)")
    ax.legend(fontsize=9, loc="upper right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path1 = os.path.join(output_dir, "premium_discount_timeseries.png")
    fig.savefig(path1, dpi=150)
    print(f"  Saved: {path1}")
    plt.close(fig)

    # --- 2. Histogram ---
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.hist(df["prem_disc_bps"], bins=80, color="steelblue", alpha=0.7, edgecolor="white", linewidth=0.5)
    ax.axvline(trigger_bps, color="green", linestyle="--", linewidth=1.2, label=f"Create threshold (+{trigger_bps:.0f} bps)")
    ax.axvline(-trigger_bps, color="red", linestyle="--", linewidth=1.2, label=f"Redeem threshold (-{trigger_bps:.0f} bps)")
    ax.set_title("Distribution of IBIT Premium/Discount", fontsize=14, fontweight="bold")
    ax.set_xlabel("Basis Points (bps)")
    ax.set_ylabel("Frequency (days)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path2 = os.path.join(output_dir, "premium_discount_histogram.png")
    fig.savefig(path2, dpi=150)
    print(f"  Saved: {path2}")
    plt.close(fig)

    # --- 3. Cumulative P&L ---
    fig, ax = plt.subplots(figsize=(16, 6))
    cum_pnl = df["pnl_per_trade"].cumsum()
    ax.fill_between(df.index, 0, cum_pnl, alpha=0.3, color="green")
    ax.plot(df.index, cum_pnl, linewidth=1.2, color="green")
    ax.set_title("Cumulative Arbitrage P&L (IBIT Create/Redeem)", fontsize=14, fontweight="bold")
    ax.set_ylabel("Cumulative P&L (USD)")
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"${x:,.0f}"))
    fig.tight_layout()
    path3 = os.path.join(output_dir, "cumulative_pnl.png")
    fig.savefig(path3, dpi=150)
    print(f"  Saved: {path3}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    cfg = load_config("config.yaml")
    df = build_analysis(cfg)
    print_summary(df, cfg)

    print("\nGenerating charts...")
    generate_charts(df, cfg, output_dir=".")
    print("\nDone! Check the PNG files in this directory.")
