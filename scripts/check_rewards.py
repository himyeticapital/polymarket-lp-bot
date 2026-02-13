"""Check which markets have LP rewards and what the daily rates are."""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import BotConfig
from bot.clients.gamma import GammaClient

async def main():
    config = BotConfig()
    gamma = GammaClient(config)
    await gamma.connect()

    markets = await gamma.get_markets()
    # Filter to markets with rewards
    rewarded = [m for m in markets if m.daily_reward_usd > 0 or m.max_incentive_spread > 0]
    rewarded.sort(key=lambda m: m.daily_reward_usd, reverse=True)

    print(f"\nTotal markets: {len(markets)}")
    print(f"With rewards: {len(rewarded)}")
    print(f"\n{'Market':<55} {'$/day':>6} {'MaxSprd':>8} {'MinSz':>6} {'Comp':>6} {'Vol24h':>10} {'Liq':>10}")
    print("-" * 110)
    for m in rewarded[:30]:
        print(f"{m.question[:53]:<55} ${m.daily_reward_usd:>5.0f} {m.max_incentive_spread:>7.3f} {m.min_incentive_size:>5.0f} {m.competition_level:>6} ${m.volume_24h:>9,.0f} ${m.liquidity:>9,.0f}")

    await gamma.close()

asyncio.run(main())
