#!/usr/bin/env node
/**
 * IBIT ETF Creation/Redemption Arbitrage — Phase 1: Historical Analysis
 *
 * Fetches historical IBIT + BTC data, computes premium/discount,
 * identifies arb opportunities, and outputs analysis + CSV.
 *
 * Data sources:
 *   - Yahoo Finance (yahoo-finance2) for IBIT & BTC-USD historical prices
 *
 * IBIT key facts:
 *   - Sponsor:      BlackRock / iShares
 *   - Custodian:    Coinbase Custody
 *   - Ticker:       IBIT (Nasdaq)
 *   - Launch:       January 11, 2024
 *   - Mgmt fee:     0.25% / yr (25 bps)
 *   - Creation unit: 40,000 shares
 *   - BTC per share: ~0.000569 (Apr 2026; drifts down ~0.25%/yr as fees accrue)
 *
 * Usage:
 *   node analyze.js
 */

const fs = require('fs');
const path = require('path');
const YahooFinance = require('yahoo-finance2').default;
const yahooFinance = new YahooFinance({ suppressNotices: ['yahooSurvey'] });
const { createObjectCsvWriter } = require('csv-writer');
const { totalCostBps, fmt, fmtUsd } = require('./lib/utils');

const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf-8'));

// ─── Data Fetching ──────────────────────────────────────────────────────────

async function fetchHistorical(ticker, startDate) {
  console.log(`Fetching ${ticker} from ${startDate}...`);
  const result = await yahooFinance.chart(ticker, {
    period1: startDate,
    interval: '1d',
  });
  const quotes = result.quotes || [];
  console.log(`  → ${quotes.length} data points`);
  return quotes;
}

// ─── Analysis ───────────────────────────────────────────────────────────────

function buildAnalysis(ibitQuotes, btcQuotes, btcPerShareRatio) {
  const btcByDate = new Map();
  for (const q of btcQuotes) {
    if (!q.date || q.close == null) continue;
    const key = q.date.toISOString().slice(0, 10);
    btcByDate.set(key, q.close);
  }

  const rows = [];
  for (const q of ibitQuotes) {
    if (!q.date || q.close == null || q.volume == null) continue;
    const dateKey = q.date.toISOString().slice(0, 10);
    const btcClose = btcByDate.get(dateKey);
    if (btcClose == null) continue;

    const navEstimate = btcClose * btcPerShareRatio;
    const premDiscBps = ((q.close - navEstimate) / navEstimate) * 10000;
    const costBps = totalCostBps(config, q.close);
    const triggerBps = costBps + config.signals.minSpreadAfterCostsBps;

    const isCreate = premDiscBps > triggerBps;
    const isRedeem = premDiscBps < -triggerBps;
    const spreadCapturedBps = (isCreate || isRedeem) ? Math.abs(premDiscBps) - costBps : 0;
    const pnlPerTrade = (spreadCapturedBps / 10000) * config.etf.creationUnitShares * q.close;

    rows.push({
      date: dateKey,
      ibitClose: q.close,
      ibitVolume: q.volume,
      btcClose,
      navEstimate,
      premDiscBps,
      costBps,
      triggerBps,
      isCreate,
      isRedeem,
      spreadCapturedBps,
      pnlPerTrade,
    });
  }
  return rows;
}

// ─── Summary Stats ──────────────────────────────────────────────────────────

function summarize(rows) {
  const prems = rows.map(r => r.premDiscBps);
  const mean   = prems.reduce((a, b) => a + b, 0) / prems.length;
  const median = [...prems].sort((a, b) => a - b)[Math.floor(prems.length / 2)];
  const std    = Math.sqrt(prems.map(x => (x - mean) ** 2).reduce((a, b) => a + b, 0) / prems.length);
  const min    = Math.min(...prems);
  const max    = Math.max(...prems);

  const creates = rows.filter(r => r.isCreate);
  const redeems = rows.filter(r => r.isRedeem);
  const trades  = [...creates, ...redeems];

  const totalPnl    = trades.reduce((s, r) => s + r.pnlPerTrade, 0);
  const avgSpread   = trades.length > 0
    ? trades.reduce((s, r) => s + r.spreadCapturedBps, 0) / trades.length : 0;
  const avgPnl      = trades.length > 0 ? totalPnl / trades.length : 0;
  const days        = rows.length;
  const years       = days / 252;
  const annualPnl   = years > 0 ? totalPnl / years : 0;

  const firstClose  = rows[0]?.ibitClose || 1;
  const cu          = config.etf.creationUnitShares;
  const capital     = cu * firstClose;
  const annualReturn = capital > 0 ? (annualPnl / capital) * 100 : 0;

  const sampleCostBps = rows[0] ? rows[0].costBps : 0;
  const costBreakdown = rows[0] ? (() => {
    const c = config.costs;
    const feeBps = (c.creationRedemptionFeeUsd / (cu * firstClose)) * 10000;
    const commBps = (c.etfCommissionPerShare / firstClose) * 10000;
    return { feeBps, commBps };
  })() : { feeBps: 0, commBps: 0 };

  return {
    days, years: years.toFixed(2),
    mean, median, std, min, max,
    creates: creates.length, redeems: redeems.length,
    totalPnl, avgSpread, avgPnl, annualPnl, annualReturn,
    capital, sampleCostBps, costBreakdown,
  };
}

// ─── Monthly Breakdown ──────────────────────────────────────────────────────

function monthlyBreakdown(rows) {
  const months = new Map();
  for (const r of rows) {
    const key = r.date.slice(0, 7);
    if (!months.has(key)) months.set(key, { days: 0, creates: 0, redeems: 0, pnl: 0, premSum: 0 });
    const m = months.get(key);
    m.days++;
    m.premSum += r.premDiscBps;
    if (r.isCreate) { m.creates++; m.pnl += r.pnlPerTrade; }
    if (r.isRedeem) { m.redeems++; m.pnl += r.pnlPerTrade; }
  }
  return months;
}

// ─── CSV Export ─────────────────────────────────────────────────────────────

async function exportCsv(rows) {
  const outPath = path.join(__dirname, 'analysis.csv');
  const writer = createObjectCsvWriter({
    path: outPath,
    header: [
      { id: 'date',              title: 'Date' },
      { id: 'ibitClose',        title: 'IBIT Close' },
      { id: 'ibitVolume',       title: 'IBIT Volume' },
      { id: 'btcClose',         title: 'BTC Close' },
      { id: 'navEstimate',      title: 'NAV Estimate' },
      { id: 'premDiscBps',      title: 'Premium/Discount (bps)' },
      { id: 'costBps',          title: 'Cost (bps)' },
      { id: 'isCreate',         title: 'Create Signal' },
      { id: 'isRedeem',         title: 'Redeem Signal' },
      { id: 'spreadCapturedBps',title: 'Spread Captured (bps)' },
      { id: 'pnlPerTrade',      title: 'P&L Per Trade (USD)' },
    ],
  });
  await writer.writeRecords(rows.map(r => ({
    ...r,
    ibitClose:         r.ibitClose.toFixed(4),
    navEstimate:       r.navEstimate.toFixed(4),
    premDiscBps:       r.premDiscBps.toFixed(2),
    costBps:           r.costBps.toFixed(2),
    spreadCapturedBps: r.spreadCapturedBps.toFixed(2),
    pnlPerTrade:       r.pnlPerTrade.toFixed(2),
  })));
  console.log(`\nExported analysis to ${outPath}`);
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function main() {
  console.log('╔══════════════════════════════════════════════════════════╗');
  console.log('║  IBIT Arbitrage — Phase 1: Historical Analysis          ║');
  console.log('║  Data: Yahoo Finance                                    ║');
  console.log('╚══════════════════════════════════════════════════════════╝\n');

  const [ibitQuotes, btcQuotes] = await Promise.all([
    fetchHistorical(config.etf.ticker, config.analysis.startDate),
    fetchHistorical('BTC-USD', config.analysis.startDate),
  ]);

  // Derive btcPerShare from most recent prices
  const lastIbit = ibitQuotes.filter(q => q.close != null).slice(-1)[0];
  const lastBtc  = btcQuotes.filter(q => q.close != null).slice(-1)[0];
  let btcPerShare;
  if (lastIbit && lastBtc && lastIbit.close > 0 && lastBtc.close > 0) {
    btcPerShare = lastIbit.close / lastBtc.close;
    console.log(`\nBTC per share (derived from latest prices): ${btcPerShare.toFixed(8)}`);
  } else {
    btcPerShare = config.etf.btcPerShare || 0.000569;
    console.log(`\nBTC per share (config default): ${btcPerShare.toFixed(8)}`);
  }

  const rows = buildAnalysis(ibitQuotes, btcQuotes, btcPerShare);
  const s    = summarize(rows);
  const c    = config.costs;
  const cu   = config.etf.creationUnitShares;

  console.log('\n' + '='.repeat(60));
  console.log(' IBIT ETF ARBITRAGE — HISTORICAL ANALYSIS');
  console.log(` BlackRock iShares Bitcoin Trust ETF | Sponsor: BlackRock`);
  console.log(` Custodian: Coinbase Custody | Creation Unit: ${cu.toLocaleString()} shares`);
  console.log('='.repeat(60));
  console.log(` Period          : ${rows[0]?.date} → ${rows[rows.length-1]?.date}`);
  console.log(` Trading days    : ${s.days}`);
  console.log(` Years           : ${s.years}`);
  console.log('');
  console.log(` Premium/Discount Stats (bps):`);
  console.log(`   Mean           : ${fmt(s.mean, 2).padStart(10)}`);
  console.log(`   Median         : ${fmt(s.median, 2).padStart(10)}`);
  console.log(`   Std Dev        : ${fmt(s.std, 2).padStart(10)}`);
  console.log(`   Min            : ${fmt(s.min, 2).padStart(10)}`);
  console.log(`   Max            : ${fmt(s.max, 2).padStart(10)}`);
  console.log('');
  console.log(` Cost Breakdown (bps):`);
  console.log(`   Create/Redeem fee : ${fmt(s.costBreakdown.feeBps, 2).padStart(8)}  ($${c.creationRedemptionFeeUsd} flat)`);
  console.log(`   ETF commission    : ${fmt(s.costBreakdown.commBps, 2).padStart(8)}  ($${c.etfCommissionPerShare}/share)`);
  console.log(`   BTC execution     : ${fmt(c.btcExecutionBps, 2).padStart(8)}`);
  console.log(`   Market impact (×2): ${fmt(c.marketImpactBps * 2, 2).padStart(8)}`);
  console.log(`   BTC spot spread   : ${fmt(c.btcSpotSpreadBps, 2).padStart(8)}`);
  console.log(`   TOTAL             : ${fmt(s.sampleCostBps, 2).padStart(8)}`);
  console.log('');
  console.log(` Actionable Opportunities:`);
  console.log(`   Create signals : ${String(s.creates).padStart(6)}  (${fmt(s.creates/s.days*100, 1)}% of days)`);
  console.log(`   Redeem signals : ${String(s.redeems).padStart(6)}  (${fmt(s.redeems/s.days*100, 1)}% of days)`);
  console.log(`   Total trades   : ${String(s.creates+s.redeems).padStart(6)}  (${fmt((s.creates+s.redeems)/s.days*100, 1)}% of days)`);
  console.log('');
  console.log(` P&L Analysis (creation unit = ${cu.toLocaleString()} shares):`);
  console.log(`   Capital required : ${fmtUsd(s.capital).padStart(20)}`);
  console.log(`   Avg spread capt. : ${fmt(s.avgSpread, 2).padStart(10)} bps`);
  console.log(`   Avg P&L / trade  : ${fmtUsd(s.avgPnl).padStart(20)}`);
  console.log(`   Total P&L        : ${fmtUsd(s.totalPnl).padStart(20)}`);
  console.log(`   Annualized P&L   : ${fmtUsd(s.annualPnl).padStart(20)}`);
  console.log(`   Annualized Return: ${fmt(s.annualReturn, 2).padStart(10)}%`);
  console.log('='.repeat(60));

  await exportCsv(rows);

  // Monthly breakdown
  const months = monthlyBreakdown(rows);
  console.log('\n Monthly Breakdown:');
  console.log(' ' + '─'.repeat(61));
  console.log(` Month       | Days | Creates | Redeems | Total P&L    | Avg Prem`);
  console.log(' ' + '─'.repeat(61));
  for (const [month, m] of months) {
    const avgPrem = m.days > 0 ? m.premSum / m.days : 0;
    console.log(
      ` ${month}    | ${String(m.days).padStart(4)} | ${String(m.creates).padStart(7)} | ${String(m.redeems).padStart(7)} | ${fmtUsd(m.pnl).padStart(12)} | ${fmt(avgPrem, 2).padStart(7)} bps`
    );
  }
  console.log(' ' + '─'.repeat(61));
}

main().catch(err => { console.error('Fatal:', err); process.exit(1); });
