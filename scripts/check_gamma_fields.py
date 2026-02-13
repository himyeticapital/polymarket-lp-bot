"""Quick check: what fields does Gamma API return per market?"""
import asyncio, sys, os, json, ssl
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import aiohttp, certifi

async def main():
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as s:
        async with s.get("https://gamma-api.polymarket.com/markets", params={"limit": "3", "active": "true", "closed": "false"}) as r:
            data = await r.json()
    if data:
        m = data[0]
        print("=== Gamma market fields ===")
        for k, v in sorted(m.items()):
            val_str = str(v)[:80] if v else str(v)
            print(f"  {k}: {val_str}")

asyncio.run(main())
