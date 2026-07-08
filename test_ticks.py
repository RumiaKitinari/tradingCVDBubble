import asyncio
import logging
from ib_async import IB, Stock

async def test_ticks():
    ib = IB()
    try:
        await ib.connectAsync('127.0.0.1', 7497, clientId=99)
        print("Connected.")
        
        contract = Stock('NVDA', 'SMART', 'USD')
        await ib.qualifyContractsAsync(contract)
        print(f"Qualified: {contract}")
        
        # Using MktData to force explicit permission errors
        ticker = ib.reqMktData(contract, "", False, False)
        
        print("Waiting 10 seconds for data or error...")
        for _ in range(10):
            if ticker.last == ticker.last: # Check for NaN
                print(f"Got price! Bid: {ticker.bid} Ask: {ticker.ask} Last: {ticker.last}")
            await asyncio.sleep(1)
            
    except Exception as e:
        print(f"Exception: {e}")
    finally:
        ib.disconnect()
        print("Disconnected.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_ticks())
