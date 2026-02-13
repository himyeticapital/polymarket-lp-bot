"""Estimate cost of closing ALL positions at current market prices."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import BotConfig
from bot.clients.clob import AsyncClobClient
from bot.clients.data_api import DataApiClient


POLY_FEE_RATE = 0.002  # 0.2% taker fee on Polymarket


async def main():
    config = BotConfig()

    # Connect clients
    clob = AsyncClobClient(config)
    data_api = DataApiClient(config)
    await clob.connect()
    await data_api.connect()

    # Fetch current balance
    balance = await clob.get_balance()
    print(f"\n{'='*65}")
    print(f"  LIQUIDATION ESTIMATE — Close All Positions")
    print(f"{'='*65}")
    print(f"\n  USDC Cash:  ${balance:.2f}")

    # Fetch positions
    proxy_addr = config.proxy_address or config.wallet_address
    positions = await data_api.get_positions(proxy_addr)

    if not positions:
        print("  No open positions found.")
        await clob.close()
        await data_api.close()
        return

    # Fetch open orders (to cancel)
    open_orders = await clob.get_open_orders()
    print(f"  Open Orders: {len(open_orders)} (will be cancelled)")

    print(f"\n  {'Token':<12} {'Outcome':<6} {'Shares':>8} {'Entry':>7} {'Bid':>7} {'Value':>9} {'Proceeds':>9} {'P&L':>8}")
    print(f"  {'-'*12} {'-'*6} {'-'*8} {'-'*7} {'-'*7} {'-'*9} {'-'*9} {'-'*8}")

    total_entry_value = 0.0
    total_proceeds = 0.0
    total_fees = 0.0

    for p in positions:
        token_id = p.get("asset", "")
        size = float(p.get("size", 0))
        if size <= 0 or not token_id:
            continue

        avg_price = float(p.get("avgPrice", 0))
        cur_price = float(p.get("curPrice", 0))
        outcome = p.get("outcome", "?")

        # Get actual best bid (what we'd sell at)
        try:
            book = await clob.get_order_book(token_id)
            best_bid = book.best_bid or cur_price
        except Exception:
            best_bid = cur_price

        entry_value = size * avg_price
        gross_proceeds = size * best_bid
        fee = gross_proceeds * POLY_FEE_RATE
        net_proceeds = gross_proceeds - fee
        pnl = net_proceeds - entry_value

        total_entry_value += entry_value
        total_proceeds += net_proceeds
        total_fees += fee

        short_id = token_id[:10] + ".."
        print(f"  {short_id:<12} {outcome:<6} {size:>8.1f} ${avg_price:>5.3f} ${best_bid:>5.3f} ${entry_value:>7.2f} ${net_proceeds:>7.2f} {pnl:>+7.2f}")

    total_pnl = total_proceeds - total_entry_value
    portfolio_after = balance + total_proceeds
    starting = config.starting_balance_usd
    net_vs_deposit = portfolio_after - starting

    print(f"\n  {'─'*63}")
    print(f"  Entry Value (what you paid):     ${total_entry_value:>9.2f}")
    print(f"  Sale Proceeds (after fees):      ${total_proceeds:>9.2f}")
    print(f"  Trading Fees:                    ${total_fees:>9.2f}")
    print(f"  Realized P&L from positions:     ${total_pnl:>+9.2f}")
    print(f"  {'─'*63}")
    print(f"  Cash after liquidation:          ${portfolio_after:>9.2f}")
    print(f"  Original deposit:                ${starting:>9.2f}")
    print(f"  NET vs deposit:                  ${net_vs_deposit:>+9.2f}")
    print(f"{'='*65}\n")

    await clob.close()
    await data_api.close()


if __name__ == "__main__":
    asyncio.run(main())
