"""Test: how many markets pass the new LP liquidity filters?"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import BotConfig
from bot.clients.gamma import GammaClient

async def main():
    config = BotConfig()
    gamma = GammaClient(config)
    await gamma.connect()

    markets = await gamma.get_markets()
    print(f"\nTotal markets from Gamma: {len(markets)}")

    # Apply same filters as LP strategy
    has_rewards = [m for m in markets if m.active and m.max_incentive_spread > 0]
    print(f"With reward pools: {len(has_rewards)}")

    pass_volume = [m for m in has_rewards if m.volume_24h >= config.lp_min_volume_24h]
    print(f"Pass volume >= ${config.lp_min_volume_24h:.0f}: {len(pass_volume)}")

    pass_liq = [m for m in pass_volume if m.liquidity >= config.lp_min_liquidity]
    print(f"Pass liquidity >= ${config.lp_min_liquidity:.0f}: {len(pass_liq)}")

    pass_spread = [m for m in pass_liq if m.spread <= config.lp_max_spread]
    print(f"Pass spread <= {config.lp_max_spread:.0%}: {len(pass_spread)}")

    pass_bid = [m for m in pass_spread if m.best_bid >= config.lp_min_best_bid]
    print(f"Pass best_bid >= ${config.lp_min_best_bid}: {len(pass_bid)}")

    print(f"\n{'='*70}")
    print(f"  ELIGIBLE MARKETS: {len(pass_bid)} (will pick top {config.lp_max_markets})")
    print(f"{'='*70}")

    # Sort by volume * liquidity * spread
    pass_bid.sort(key=lambda m: m.volume_24h * m.liquidity * m.max_incentive_spread, reverse=True)
    for i, m in enumerate(pass_bid[:10]):
        print(f"  {i+1}. {m.question[:50]:<52} vol=${m.volume_24h:>10,.0f}  liq=${m.liquidity:>8,.0f}  spread={m.spread:.3f}  bid={m.best_bid:.3f}")

    await gamma.close()

asyncio.run(main())
