"""One-off script to sell a position on Polymarket CLOB."""

import asyncio
import sys


async def main():
    if len(sys.argv) < 4:
        print("Usage: python scripts/sell_position.py <token_id> <shares> <price>")
        print("  price: minimum price to accept (set low for immediate fill)")
        sys.exit(1)

    token_id = sys.argv[1]
    shares = float(sys.argv[2])
    price = float(sys.argv[3])

    from bot.config import BotConfig
    from bot.clients.clob import AsyncClobClient
    from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

    config = BotConfig()
    client = AsyncClobClient(config)
    await client.connect()

    # Approve conditional token for selling
    sig_type = 2 if config.proxy_address else 0
    params = BalanceAllowanceParams(
        asset_type=AssetType.CONDITIONAL,
        token_id=token_id,
        signature_type=sig_type,
    )
    print(f"Approving conditional token for selling...")
    approve_result = await asyncio.to_thread(
        client.client.update_balance_allowance, params
    )
    print(f"Approval: {approve_result}")

    print(f"Selling {shares} shares of {token_id[:20]}... at ${price}")
    result = await client.create_and_post_limit_order(
        token_id=token_id,
        price=price,
        size=shares,
        side="SELL",
        order_type="GTC",
    )
    print(f"Result: {result}")
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
