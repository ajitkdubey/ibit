/**
 * ARKB ETF Arbitrage — Shared utilities
 */
const https = require('https');
const http = require('http');

/**
 * Fetch JSON from a URL (built-in, no deps)
 */
function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    const zlib = require('zlib');
    mod.get(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0',
        'Accept-Encoding': 'identity',
        'Accept': 'application/json',
      }
    }, (res) => {
      let stream = res;
      if (res.headers['content-encoding'] === 'gzip') {
        stream = res.pipe(zlib.createGunzip());
      } else if (res.headers['content-encoding'] === 'deflate') {
        stream = res.pipe(zlib.createInflate());
      }
      let data = '';
      stream.on('data', (chunk) => (data += chunk));
      stream.on('end', () => {
        try {
          if (data.charCodeAt(0) === 0xFEFF) data = data.slice(1);
          resolve(JSON.parse(data));
        } catch (e) {
          reject(new Error(`Failed to parse JSON: ${e.message}\nBody: ${data.slice(0, 200)}`));
        }
      });
      stream.on('error', reject);
    }).on('error', reject);
  });
}

/**
 * Fetch ARKB holdings CSV from ARK/21Shares
 * Returns { btcQuantity, sharesOutstanding, btcPerShare, marketValue }
 *
 * ARK publishes a CSV at:
 *   https://ark-funds.com/wp-content/uploads/funds-etf-csv/ARK_21SHARES_BITCOIN_ETF_ARKB_HOLDINGS.csv
 *
 * CSV format (approximate):
 *   fund,date,company,ticker,cusip,shares,market_value,...
 */
async function fetchArkHoldings(config) {
  const url = config.ark21shares.holdingsUrl;
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    mod.get(url, {
      headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'text/csv,*/*' },
      timeout: 10000,
    }, (res) => {
      // Follow redirect
      if (res.statusCode === 301 || res.statusCode === 302) {
        return fetchArkHoldings({ ark21shares: { holdingsUrl: res.headers.location } })
          .then(resolve).catch(reject);
      }
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        try {
          const lines = data.split('\n').filter(l => l.trim());
          // Find the BTC row — look for a line containing 'BITCOIN' or 'BTC'
          const btcLine = lines.find(l =>
            l.toUpperCase().includes('BITCOIN') || l.toUpperCase().includes(',BTC,')
          );
          if (!btcLine) throw new Error('BTC row not found in ARK holdings CSV');

          const cols = btcLine.split(',').map(s => s.replace(/"/g, '').trim());
          // Col indices vary — find the shares/market_value columns by header
          const header = lines[0].toLowerCase().split(',').map(s => s.trim());
          const sharesIdx = header.findIndex(h => h.includes('shares') || h.includes('quantity'));
          const mvIdx = header.findIndex(h => h.includes('market') && h.includes('value'));

          const btcQuantity = sharesIdx >= 0 ? parseFloat(cols[sharesIdx].replace(/,/g, '')) : 0;
          const marketValue = mvIdx >= 0 ? parseFloat(cols[mvIdx].replace(/[$,]/g, '')) : 0;

          resolve({ btcQuantity, marketValue });
        } catch (e) {
          reject(e);
        }
      });
      res.on('error', reject);
    }).on('error', reject).on('timeout', () => reject(new Error('ARK holdings fetch timed out')));
  });
}

/**
 * Calculate total round-trip cost in bps for one creation unit
 */
function totalCostBps(config, arkbPrice) {
  const c = config.costs;
  const cu = config.etf.creationUnitShares;
  const feeBps = (c.creationRedemptionFeeUsd / (cu * arkbPrice)) * 10000;
  const commBps = (c.etfCommissionPerShare / arkbPrice) * 10000;
  return feeBps + commBps + c.btcExecutionBps + c.marketImpactBps * 2 + c.btcSpotSpreadBps;
}

function fmt(n, decimals = 2) {
  return n.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtUsd(n) {
  return '$' + fmt(n);
}

module.exports = { fetchJson, fetchArkHoldings, totalCostBps, fmt, fmtUsd };
