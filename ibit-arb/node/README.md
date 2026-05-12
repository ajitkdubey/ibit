# IBIT Arb — Node.js Monitor & Dashboard

Real-time arbitrage monitor and web dashboard for **IBIT (BlackRock iShares Bitcoin Trust ETF)**.

## Structure

```
node/
├── analyze.js       # Phase 1: Historical analysis (Yahoo Finance)
├── monitor.js       # Phase 2: Live terminal monitor (Coinbase WS + Yahoo Finance)
├── dashboard.js     # Phase 3: Express + Socket.IO web dashboard
├── lib/
│   ├── utils.js     # Shared: cost model, formatters
│   └── discord.js   # Discord alerts (#ibit-arb-alerts channel)
├── public/
│   └── index.html   # Web UI — dark BlackRock theme (Chart.js live chart)
├── config.json      # All parameters
└── .env             # Discord credentials (optional)
```

## IBIT vs ARKB Key Differences

| | IBIT (BlackRock) | ARKB (ARK 21Shares) |
|---|---|---|
| Sponsor | BlackRock / iShares | ARK Investment Management / 21Shares |
| Exchange | Nasdaq | Cboe BZX |
| Creation unit | **40,000 shares** | 5,000 shares |
| BTC per share | **~0.000569** (Apr 2026) | ~0.000303 |
| Mgmt fee | **0.25%/yr** | 0.21%/yr |
| Creation fee | **$750/order** | ~$200 (estimated) |
| AUM | ~$70B+ (largest Bitcoin ETF) | ~$5B |

## Quick Start

```bash
cd IBIT-arb/node/
npm install

# Phase 1: Historical analysis
node analyze.js

# Phase 2: Terminal monitor (dry run)
npm run monitor

# Phase 3: Web dashboard (dry run) → http://localhost:5000
npm run dashboard
```

## Live Mode

```bash
node monitor.js         # Coinbase WS + Yahoo Finance
node dashboard.js       # Web UI at localhost:5000
node dashboard.js --port 8080  # Custom port
```

## BTC Per Share — Dynamic

`btcPerShare` is calculated live from real prices:
```
btcPerShare = IBIT_mid / BTC_price
```
- Starts from `config.json` value (0.000569) — shown as `(config)`
- Updates every 10s in live mode from Yahoo Finance — shown as `(live)`
- IBIT launched at ~$40.50 when BTC was ~$46k → 0.000880 BTC/share at inception
- Now ~0.000569 (Apr 2026) as fees accrue and more shares issued

## Discord Alerts (optional)

```
DISCORD_TOKEN=your_bot_token
DISCORD_USER_ID=your_user_id
DISCORD_GUILD_ID=your_guild_id
```

Posts CREATE/REDEEM signal embeds to `#ibit-arb-alerts`.
