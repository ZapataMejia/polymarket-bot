"""Test 1.1-1.5, 1.8-1.10: Exchange connectivity (requires network)."""
import pytest

from src.core.config import ExchangeConfig
from src.data.exchange import ExchangeClient


@pytest.fixture
async def client():
    config = ExchangeConfig(name="binance", sandbox=False, rate_limit=1200, timeout=30000)
    client = ExchangeClient(config)
    yield client
    await client.close()


@pytest.mark.integration
class TestExchangeConnection:
    @pytest.mark.asyncio
    async def test_fetch_ohlcv(self, client):
        df = await client.fetch_ohlcv("BTC/USDT", "1h", limit=100)
        assert not df.empty
        assert len(df) >= 50
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert df.index.name == "timestamp"

    @pytest.mark.asyncio
    async def test_fetch_multi_timeframe(self, client):
        for tf in ["1m", "5m", "15m", "1h", "4h"]:
            df = await client.fetch_ohlcv("BTC/USDT", tf, limit=10)
            assert not df.empty, f"Failed for timeframe {tf}"

    @pytest.mark.asyncio
    async def test_fetch_orderbook(self, client):
        ob = await client.fetch_orderbook("BTC/USDT", limit=20)
        assert len(ob["bids"]) >= 20
        assert len(ob["asks"]) >= 20
        assert ob["bid_depth"] > 0
        assert ob["ask_depth"] > 0

    @pytest.mark.asyncio
    async def test_fetch_ticker(self, client):
        ticker = await client.fetch_ticker("BTC/USDT")
        assert "last" in ticker
        assert ticker["last"] > 0

    @pytest.mark.asyncio
    async def test_fetch_recent_trades(self, client):
        df = await client.fetch_recent_trades("BTC/USDT", limit=50)
        assert not df.empty
        assert "price" in df.columns
        assert "amount" in df.columns

    @pytest.mark.asyncio
    async def test_multi_symbol(self, client):
        for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
            df = await client.fetch_ohlcv(symbol, "1h", limit=10)
            assert not df.empty, f"Failed for {symbol}"
