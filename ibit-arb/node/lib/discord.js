/**
 * lib/discord.js
 * Discord notification module for ARKB Arb — sends alerts to a channel or DM.
 */

require('dotenv').config({ path: require('path').join(__dirname, '..', '.env') });
const { Client, GatewayIntentBits, EmbedBuilder } = require('discord.js');

const TOKEN   = process.env.DISCORD_TOKEN;
const USER_ID = process.env.DISCORD_USER_ID;
const GUILD_ID = process.env.DISCORD_GUILD_ID;

let client = null;
let ready  = false;
let alertChannelId = null;

async function init() {
  if (!TOKEN) {
    console.warn('[Discord] No DISCORD_TOKEN set — alerts disabled');
    return;
  }
  if (client) return;

  client = new Client({
    intents: [
      GatewayIntentBits.Guilds,
      GatewayIntentBits.GuildMessages,
      GatewayIntentBits.DirectMessages,
    ]
  });

  client.once('ready', async () => {
    console.log(`[Discord] Logged in as ${client.user.tag}`);
    ready = true;

    try {
      const guild = await client.guilds.fetch(GUILD_ID);
      const channels = await guild.channels.fetch();
      let channel = channels.find(c => c.name === 'ibit-arb-alerts' && c.isTextBased());
      if (!channel) {
        channel = await guild.channels.create({
          name: 'ibit-arb-alerts',
          topic: 'IBIT ETF arbitrage signals — creation/redemption arb vs BTC NAV',
        });
        console.log('[Discord] Created #ibit-arb-alerts channel');
      }
      alertChannelId = channel.id;
      console.log(`[Discord] Alert channel: #${channel.name} (${channel.id})`);

      await channel.send({
        embeds: [new EmbedBuilder()
          .setColor(0xf7931a)
          .setTitle('🟠 IBIT Arb Bot Online')
          .setDescription('Dashboard is live. Monitoring IBIT premium/discount arb signals.')
          .setTimestamp()
        ]
      });
    } catch (e) {
      console.error(`[Discord] Channel setup error: ${e.message}`);
    }
  });

  client.on('error', e => console.error(`[Discord] Error: ${e.message}`));
  await client.login(TOKEN);
}

async function getChannel() {
  if (!ready || !alertChannelId) return null;
  try { return await client.channels.fetch(alertChannelId); }
  catch { return null; }
}

async function sendTradeAlert(trade) {
  const channel = await getChannel();
  if (!channel) return;

  const isCreate = trade.signal === 'CREATE';
  const color    = isCreate ? 0x3fb950 : 0xf85149;
  const emoji    = isCreate ? '🟢' : '🔴';

  const embed = new EmbedBuilder()
    .setColor(color)
    .setTitle(`${emoji} IBIT ARB SIGNAL — ${trade.signal}`)
    .setDescription(isCreate
      ? 'ETF trading at **premium** → Buy BTC → Deliver to Coinbase Custody → Receive IBIT → Sell'
      : 'ETF trading at **discount** → Buy IBIT → Redeem for BTC → Sell BTC'
    )
    .addFields(
      { name: 'IBIT Price',   value: `$${trade.etfPrice.toFixed(4)}`,    inline: true },
      { name: 'BTC Price',    value: `$${trade.btcPrice.toFixed(2)}`,     inline: true },
      { name: 'NAV Estimate', value: `$${trade.navEstimate.toFixed(4)}`,  inline: true },
      { name: 'Spread (bps)', value: `${trade.spreadBps.toFixed(2)} bps`, inline: true },
      { name: 'Est. P&L',     value: `$${trade.pnl.toFixed(2)}`,          inline: true },
      { name: 'Unit Size',    value: `40,000 shares`,                     inline: true },
    )
    .setTimestamp(new Date(trade.timestamp))
    .setFooter({ text: 'IBIT Arb Dashboard — BlackRock iShares Bitcoin Trust' })

  await channel.send({ embeds: [embed] });
}

async function sendDM(message) {
  if (!ready) return;
  try {
    const user = await client.users.fetch(USER_ID);
    await user.send(message);
  } catch (e) {
    console.error(`[Discord] DM error: ${e.message}`);
  }
}

module.exports = { init, sendTradeAlert, sendDM };
