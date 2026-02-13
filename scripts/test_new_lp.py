"""Test the new one-sided LP strategy filters and market selection."""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import BotConfig
from bot.clients.gamma import GammaClient

async def main():
    config = BotConfig()
    gamma = GammaClient(config)
    await gamma.connect()

    print(f"\n{'='*80}")
    print(f"  NEW LP STRATEGY — Market Selection Test")
    print(f"{'='*80}")
    print(f"\n  Filters: vol>=${config.lp_min_volume_24h:.0f} liq>=${config.lp_min_liquidity:.0f} "
          f"spread<={config.lp_max_spread:.0%} bid>=${config.lp_min_best_bid} "
          f"reward>=${config.lp_min_daily_reward} days<={config.lp_max_days_to_resolve}")
    print(f"  Max per market: ${config.max_per_market_usd} | Order size: ${config.lp_order_size_usd}")

    markets = await gamma.get_markets()
    print(f"\n  Total markets fetched (paginated): {len(markets)}")

    # Apply filters
    from datetime import datetime, timezone
    has_rewards = [m for m in markets if m.active and m.max_incentive_spread > 0]
    print(f"  With reward pools: {len(has_rewards)}")

    eligible = []
    for m in has_rewards:
        if m.volume_24h < config.lp_min_volume_24h:
            continue
        if m.liquidity < config.lp_min_liquidity:
            continue
        if m.spread > config.lp_max_spread:
            continue
        if m.best_bid < config.lp_min_best_bid:
            continue
        if m.daily_reward_usd < config.lp_min_daily_reward:
            continue
        if m.end_date:
            try:
                end = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                days = (end - datetime.now(timezone.utc)).days
                if days > config.lp_max_days_to_resolve:
                    continue
            except:
                pass
        eligible.append(m)

    # Score and rank
    def score(m):
        comp = m.competitive_raw
        comp_score = max(0.1, 1.0 - abs(comp - 0.5) * 2)
        liq_factor = min(m.liquidity / 10_000, 3.0)
        return m.daily_reward_usd * comp_score * max(liq_factor, 0.1)

    eligible.sort(key=score, reverse=True)

    print(f"  Pass all filters: {len(eligible)}")
    print(f"  Will select top {config.lp_max_markets}")

    print(f"\n  {'#':<3} {'Market':<48} {'$/day':>6} {'Comp':>5} {'Score':>7} {'Vol24h':>10} {'Liq':>10} {'Spread':>7} {'Days':>5}")
    print(f"  {'-'*3} {'-'*48} {'-'*6} {'-'*5} {'-'*7} {'-'*10} {'-'*10} {'-'*7} {'-'*5}")

    for i, m in enumerate(eligible[:15]):
        s = score(m)
        days = "?"
        if m.end_date:
            try:
                end = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                days = str((end - datetime.now(timezone.utc)).days)
            except:
                pass
        marker = " <--" if i < config.lp_max_markets else ""
        print(f"  {i+1:<3} {m.question[:46]:<48} ${m.daily_reward_usd:>4.0f} {m.competitive_raw:>5.2f} {s:>7.1f} ${m.volume_24h:>9,.0f} ${m.liquidity:>9,.0f} {m.spread:>6.3f} {days:>5}{marker}")

    await gamma.close()

asyncio.run(main())
