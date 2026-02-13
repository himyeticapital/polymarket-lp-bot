"""Paginate Gamma API to find ALL reward markets."""
import asyncio, sys, os, ssl, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import aiohttp, certifi

async def main():
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    all_markets = []

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
        for offset in range(0, 500, 100):
            params = {"limit": "100", "offset": str(offset), "active": "true", "closed": "false"}
            async with s.get("https://gamma-api.polymarket.com/markets", params=params) as r:
                data = await r.json()
            if not data:
                break
            all_markets.extend(data)
            print(f"Fetched offset={offset}: {len(data)} markets")
            if len(data) < 100:
                break

    print(f"\nTotal markets fetched: {len(all_markets)}")

    # Check rewards
    rewarded = []
    for m in all_markets:
        daily_reward = 0.0
        for cr in m.get("clobRewards", []) or []:
            daily_reward += float(cr.get("rewardsDailyRate", 0))
        max_spread = float(m.get("rewardsMaxSpread", 0))
        if daily_reward > 0 or max_spread > 0:
            comp = float(m.get("competitive", 0))
            rewarded.append({
                "question": m.get("question", ""),
                "daily_reward": daily_reward,
                "max_spread": max_spread,
                "min_size": float(m.get("rewardsMinSize", 0)),
                "competitive": comp,
                "volume24hr": float(m.get("volume24hr", 0)),
                "liquidity": float(m.get("liquidity", 0)),
            })

    rewarded.sort(key=lambda x: x["daily_reward"], reverse=True)
    print(f"With rewards: {len(rewarded)}")
    print(f"\n{'Market':<55} {'$/day':>6} {'Sprd%':>6} {'MinSz':>6} {'Comp':>6} {'Vol24h':>10}")
    print("-" * 100)
    for m in rewarded[:40]:
        print(f"{m['question'][:53]:<55} ${m['daily_reward']:>5.0f} {m['max_spread']:>5.1f}% {m['min_size']:>5.0f} {m['competitive']:>6.2f} ${m['volume24hr']:>9,.0f}")

asyncio.run(main())
