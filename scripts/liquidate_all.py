"""Cancel ALL open orders and sell ALL positions at market price."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import BotConfig
from bot.clients.clob import AsyncClobClient
from bot.clients.data_api import DataApiClient


async def main():
    config = BotConfig()
    clob = AsyncClobClient(config)
    data_api = DataApiClient(config)
    await clob.connect()
    await data_api.connect()

    # Step 1: Cancel ALL open orders
    print("\n=== STEP 1: Cancel all open orders ===")
    open_orders = await clob.get_open_orders()
    print(f"  Found {len(open_orders)} open orders")
    if open_orders:
        result = await clob.cancel_all()
        print(f"  Cancel result: {result}")

    # Verify cancellation
    await asyncio.sleep(2)
    remaining = await clob.get_open_orders()
    print(f"  Orders remaining after cancel: {len(remaining)}")

    # Step 2: Fetch balance before
    balance_before = await clob.get_balance()
    print(f"\n  USDC balance before liquidation: ${balance_before:.2f}")

    # Step 3: Sell all positions
    print("\n=== STEP 2: Sell all positions ===")
    proxy_addr = config.proxy_address or config.wallet_address
    positions = await data_api.get_positions(proxy_addr)

    if not positions:
        print("  No positions to liquidate.")
        await clob.close()
        await data_api.close()
        return

    total_proceeds = 0.0
    for p in positions:
        token_id = p.get("asset", "")
        size = float(p.get("size", 0))
        outcome = p.get("outcome", "?")
        cur_price = float(p.get("curPrice", 0))

        if size <= 0 or not token_id:
            continue

        short_id = token_id[:12] + ".."
        print(f"\n  Selling {size:.1f} shares of {short_id} ({outcome}) @ ~${cur_price:.3f}")

        # Get best bid for the limit sell
        try:
            book = await clob.get_order_book(token_id)
            best_bid = book.best_bid
        except Exception:
            best_bid = None

        if best_bid is None or best_bid < 0.001:
            print(f"    SKIP — no bid-side liquidity (best bid: {best_bid})")
            continue

        # Sell as limit at best bid (GTC so it rests if needed)
        try:
            result = await clob.create_and_post_limit_order(
                token_id=token_id,
                price=best_bid,
                size=size,
                side="SELL",
                order_type="FOK",  # Fill or kill — immediate or nothing
            )
            proceeds = size * best_bid
            total_proceeds += proceeds
            print(f"    SOLD — proceeds ~${proceeds:.2f} | result: {result}")
        except Exception as e:
            print(f"    FAILED — {e}")

        await asyncio.sleep(0.5)  # Rate limit

    # Step 4: Final balance
    await asyncio.sleep(3)
    balance_after = await clob.get_balance()
    print(f"\n{'='*50}")
    print(f"  Balance before:  ${balance_before:.2f}")
    print(f"  Balance after:   ${balance_after:.2f}")
    print(f"  Change:          ${balance_after - balance_before:+.2f}")
    print(f"  Original deposit: ${config.starting_balance_usd:.2f}")
    print(f"  NET vs deposit:   ${balance_after - config.starting_balance_usd:+.2f}")
    print(f"{'='*50}\n")

    # Check if any positions remain
    remaining_pos = await data_api.get_positions(proxy_addr)
    remaining_active = [p for p in remaining_pos if float(p.get("size", 0)) > 0]
    if remaining_active:
        print(f"  WARNING: {len(remaining_active)} positions still open (no bid liquidity)")
        for p in remaining_active:
            print(f"    - {p.get('asset', '')[:12]}.. {p.get('outcome', '?')} {float(p.get('size', 0)):.1f} shares")
    else:
        print("  All positions closed!")

    await clob.close()
    await data_api.close()


if __name__ == "__main__":
    asyncio.run(main())
