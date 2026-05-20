import asyncio
from core.config import load_settings
from core.logger import setup_logging
from kiwoom.client import KiwoomClient


async def main():
    setup_logging()
    settings = load_settings()
    kiwoom = KiwoomClient(settings)

    balance = await kiwoom.get_balance()
    print(f"총 평가금액: {balance.total_eval:,.0f}원")
    print(f"예수금: {balance.cash:,.0f}원")
    print(f"수익률: {balance.return_rate:.2f}%")
    print(f"보유 종목 수: {len(balance.holdings)}")
    for h in balance.holdings:
        print(f"  - {h.name} ({h.ticker}): {h.quantity}주, {h.return_rate:+.2f}%")


asyncio.run(main())
