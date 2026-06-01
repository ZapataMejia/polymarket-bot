"""Test 0.4: Database initialization and table creation."""
import pytest

from src.core.database import Database, Trade, Signal, PortfolioSnapshot


@pytest.fixture
async def db(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    database = Database(url)
    await database.init_tables()
    yield database
    await database.close()


class TestDatabase:
    @pytest.mark.asyncio
    async def test_tables_created(self, db):
        async with db.engine.connect() as conn:
            result = await conn.run_sync(
                lambda sync_conn: sync_conn.execute(
                    __import__("sqlalchemy").text(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                ).fetchall()
            )
            table_names = {row[0] for row in result}
            assert "trades" in table_names
            assert "signals" in table_names
            assert "portfolio" in table_names

    @pytest.mark.asyncio
    async def test_insert_trade(self, db):
        async with db.session() as session:
            trade = Trade(
                symbol="BTC/USDT",
                side="BUY",
                price=50000.0,
                quantity=0.1,
                fee=5.0,
                strategy="test",
            )
            session.add(trade)
            await session.commit()
            assert trade.id is not None

    @pytest.mark.asyncio
    async def test_insert_signal(self, db):
        async with db.session() as session:
            signal = Signal(
                symbol="ETH/USDT",
                strategy="ema_crossover",
                direction="LONG",
                strength=0.85,
            )
            session.add(signal)
            await session.commit()
            assert signal.id is not None

    @pytest.mark.asyncio
    async def test_insert_portfolio(self, db):
        async with db.session() as session:
            snap = PortfolioSnapshot(
                total_value=10500.0,
                cash=5000.0,
                unrealized_pnl=300.0,
                realized_pnl=200.0,
                open_positions=2,
            )
            session.add(snap)
            await session.commit()
            assert snap.id is not None
