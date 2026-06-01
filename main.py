"""Main entry point for the trading system."""
from __future__ import annotations

import argparse
import asyncio

from src.core import Config, Database, setup_logger


async def cmd_collect(config: Config) -> None:
    from src.data.collector import DataCollector

    log = setup_logger("trading", config.log_level, config.log_file)
    log.info("Starting data collection...")
    collector = DataCollector(config)
    try:
        results = await collector.collect_all_history()
        for key, count in results.items():
            log.info("  %s: %d candles", key, count)
    finally:
        await collector.close()


async def cmd_backtest(config: Config) -> None:
    from src.data.storage import ParquetStorage
    from src.features.pipeline import FeaturePipeline
    from src.strategies.backtest import BacktestEngine
    from src.strategies.breakout import VolumeBreakout
    from src.strategies.mean_reversion import BollingerMeanReversion
    from src.strategies.regime_adaptive import (
        RegimeAdaptiveConservative,
        RegimeAdaptiveUltraConservative,
    )
    from src.strategies.trend_following import EMACrossover

    log = setup_logger("trading", config.log_level, config.log_file)
    storage = ParquetStorage(config.data_storage_path)
    pipeline = FeaturePipeline()
    engine = BacktestEngine()
    strategies = [
        RegimeAdaptiveUltraConservative(allow_short=True),
        RegimeAdaptiveUltraConservative(allow_short=False),
        RegimeAdaptiveConservative(allow_short=True),
        RegimeAdaptiveConservative(allow_short=False),
        BollingerMeanReversion(),
        EMACrossover(),
        VolumeBreakout(),
    ]

    for symbol in config.symbols:
        raw = storage.load(symbol, "1h")
        if raw.empty:
            log.warning("No data for %s — run 'collect' first", symbol)
            continue
        features = pipeline.build(raw)
        log.info("Features for %s: %d rows, %d columns", symbol, *features.shape)
        for strat in strategies:
            r = engine.run(strat, features)
            log.info(
                "[%s] %s — Return: %.2f%% | Sharpe: %.2f | MaxDD: %.2f%% | "
                "WinRate: %.1f%% | Trades: %d | BuyHold: %.2f%%",
                symbol, r.strategy_name, r.total_return * 100, r.sharpe_ratio,
                r.max_drawdown * 100, r.win_rate * 100, r.total_trades, r.buy_hold_return * 100,
            )

        # --- TSMOM + vol targeting (velas diarias; literatura Moskowitz / vol parity) ---
        from src.strategies.tsmom import build_tsmom_exposure, ohlcv_to_daily

        daily = ohlcv_to_daily(raw)
        if len(daily) >= 25:
            tc = config.tsmom
            exp = build_tsmom_exposure(
                daily,
                min_votes=tc.min_votes,
                target_ann_vol=tc.target_ann_vol,
                vol_lookback=tc.vol_lookback,
                max_leverage=tc.max_leverage,
                rebalance_rule=tc.rebalance_rule,
            )
            r_ts = engine.run_exposure(
                daily,
                exp,
                strategy_name="tsmom_vol_weekly",
                periods_per_year=252,
            )
            log.info(
                "[%s] %s (daily) — Return: %.2f%% | Sharpe: %.2f | MaxDD: %.2f%% | "
                "Trades: %d | BuyHold: %.2f%%",
                symbol, r_ts.strategy_name, r_ts.total_return * 100, r_ts.sharpe_ratio,
                r_ts.max_drawdown * 100, r_ts.total_trades, r_ts.buy_hold_return * 100,
            )
        else:
            log.warning("[%s] TSMOM omitido: pocos días diarios (%d)", symbol, len(daily))


async def cmd_train_ml(config: Config) -> None:
    from src.data.storage import ParquetStorage
    from src.features.pipeline import FeaturePipeline
    from src.models.ml_models import GradientBoostModel, EnsembleModel, prepare_xy

    log = setup_logger("trading", config.log_level, config.log_file)
    storage = ParquetStorage(config.data_storage_path)
    pipeline = FeaturePipeline()

    for symbol in config.symbols:
        raw = storage.load(symbol, "1h")
        if raw.empty:
            log.warning("No data for %s", symbol)
            continue
        features = pipeline.build(raw)
        X, y = prepare_xy(features)
        log.info("[%s] Training data: %d samples, %d features", symbol, len(X), X.shape[1])

        xgb = GradientBoostModel("xgboost")
        lgb = GradientBoostModel("lightgbm")
        xgb_result = xgb.train_evaluate(X, y)
        lgb_result = lgb.train_evaluate(X, y)
        log.info("[%s] XGBoost accuracy: %.3f", symbol, xgb_result.accuracy)
        log.info("[%s] LightGBM accuracy: %.3f", symbol, lgb_result.accuracy)
        log.info("[%s] XGBoost top features: %s", symbol, list(xgb_result.feature_importance.keys())[:5])

        ensemble = EnsembleModel([xgb, lgb])
        ens_result = ensemble.train_evaluate(X, y)
        log.info("[%s] Ensemble accuracy: %.3f", symbol, ens_result.accuracy)

        xgb.save(f"data/models/{symbol.replace('/', '_')}_xgb.pkl")
        lgb.save(f"data/models/{symbol.replace('/', '_')}_lgb.pkl")
        log.info("[%s] Models saved", symbol)


async def cmd_train_rl(config: Config) -> None:
    from src.data.storage import ParquetStorage
    from src.features.pipeline import FeaturePipeline
    from src.models.rl_agent import SimpleRLAgent
    from src.models.rl_env import TradingEnv

    log = setup_logger("trading", config.log_level, config.log_file)
    storage = ParquetStorage(config.data_storage_path)
    pipeline = FeaturePipeline()

    for symbol in config.symbols[:1]:  # train on first symbol
        raw = storage.load(symbol, "1h")
        if raw.empty:
            log.warning("No data for %s", symbol)
            continue
        features = pipeline.build(raw)
        env = TradingEnv(features)
        agent = SimpleRLAgent(n_features=env.observation_space.shape[0])
        log.info("[%s] Training RL agent (100 episodes)...", symbol)
        returns = agent.train(env, episodes=100)
        log.info("[%s] Final avg return: %.3f", symbol, sum(returns[-10:]) / 10)
        agent.save(f"data/models/{symbol.replace('/', '_')}_rl.pkl")


async def cmd_paper_trade(config: Config) -> None:
    from src.execution.paper_trader import PaperTrader
    from src.strategies.regime_adaptive import RegimeAdaptiveUltraConservative

    log = setup_logger("trading", config.log_level, config.log_file)
    strategies = [RegimeAdaptiveUltraConservative(allow_short=False)]
    trader = PaperTrader(config, strategies)
    try:
        await trader.run(interval_seconds=60)
    finally:
        await trader.close()


async def cmd_poly_edge(config: Config, args: argparse.Namespace) -> None:
    """Empirical edge test for Polymarket 5/15-min Up/Down crypto markets.

    Delegates to scripts.analyze_polymarket_edge so the CLI stays a thin shim.
    """
    import sys

    saved_argv = sys.argv
    forwarded = ["analyze_polymarket_edge.py", f"--config={args.config}"]
    if args.days:
        forwarded += ["--days", str(args.days)]
    if args.start:
        forwarded += ["--start", args.start]
    if args.end:
        forwarded += ["--end", args.end]
    if args.threshold is not None:
        forwarded += ["--threshold", str(args.threshold)]
    if args.half_spread_cents is not None:
        forwarded += ["--half-spread-cents", str(args.half_spread_cents)]
    if args.csv:
        forwarded += ["--csv", args.csv]
    if args.spread_sensitivity:
        forwarded += ["--spread-sensitivity"]
    sys.argv = forwarded
    try:
        from scripts.analyze_polymarket_edge import run as _run
        await _run()
    finally:
        sys.argv = saved_argv


async def cmd_init_db(config: Config) -> None:
    log = setup_logger("trading", config.log_level, config.log_file)
    db = Database(config.database_url)
    await db.init_tables()
    log.info("Database tables created")
    await db.close()


async def cmd_live_collect(config: Config) -> None:
    from src.data.collector import DataCollector

    log = setup_logger("trading", config.log_level, config.log_file)
    collector = DataCollector(config)
    try:
        await collector.run_continuous(interval_seconds=60)
    finally:
        await collector.close()


async def cmd_paper_trade_poly(config: Config, args: argparse.Namespace) -> None:
    """Daemon de paper trading sobre Polymarket Up/Down con alertas a Telegram.

    Único bot activo en Telegram: detecta señales en vivo, abre posiciones virtuales,
    settlea cuando expiran y envía PnL al chat. Persiste estado en JSON.
    """
    import sys

    saved_argv = sys.argv
    forwarded = ["run_paper_trader.py", f"--config={args.config}"]
    if args.bankroll is not None:
        forwarded += ["--bankroll", str(args.bankroll)]
    if args.sizing_mode:
        forwarded += ["--sizing-mode", args.sizing_mode]
    if args.position_size is not None:
        forwarded += ["--position-size", str(args.position_size)]
    if args.kelly_fraction is not None:
        forwarded += ["--kelly-fraction", str(args.kelly_fraction)]
    if args.max_pct_per_trade is not None:
        forwarded += ["--max-pct-per-trade", str(args.max_pct_per_trade)]
    if args.max_concurrent is not None:
        forwarded += ["--max-concurrent", str(args.max_concurrent)]
    if args.bankroll_floor is not None:
        forwarded += ["--bankroll-floor", str(args.bankroll_floor)]
    if args.threshold is not None:
        forwarded += ["--threshold", str(args.threshold)]
    if args.half_spread_cents is not None:
        forwarded += ["--half-spread-cents", str(args.half_spread_cents)]
    if args.fee_rate_pct is not None:
        forwarded += ["--fee-rate-pct", str(args.fee_rate_pct)]
    if args.poll_sec is not None:
        forwarded += ["--poll-sec", str(args.poll_sec)]
    if args.series:
        forwarded += ["--series"] + list(args.series)
    if args.state_path:
        forwarded += ["--state-path", args.state_path]
    sys.argv = forwarded
    try:
        from scripts.run_paper_trader import amain as _amain
        await _amain()
    finally:
        sys.argv = saved_argv


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Trading System")
    parser.add_argument("command", choices=[
        "collect", "backtest", "train-ml", "train-rl",
        "paper-trade", "init-db", "live-collect",
        "poly-edge", "paper-trade-poly",
    ])
    parser.add_argument("--config", default="config/default.yaml")
    # Flags forwarded to `poly-edge` (no-op for other commands).
    parser.add_argument("--days", type=int, default=None,
                        help="poly-edge: ventana en días")
    parser.add_argument("--start", default=None,
                        help="poly-edge: fecha inicio UTC YYYY-MM-DD")
    parser.add_argument("--end", default=None,
                        help="poly-edge: fecha fin UTC YYYY-MM-DD")
    parser.add_argument("--threshold", type=float, default=None,
                        help="poly-edge / paper-trade-poly: umbral |edge| (0.05 = 5pp)")
    parser.add_argument("--half-spread-cents", type=float, default=None,
                        help="poly-edge / paper-trade-poly: medio spread asumido en centavos")
    parser.add_argument("--csv", default=None,
                        help="poly-edge: exportar CSV por mercado")
    parser.add_argument("--spread-sensitivity", action="store_true",
                        help="poly-edge: tabla de sensibilidad al spread")
    # Flags forwarded to `paper-trade-poly`.
    parser.add_argument("--bankroll", type=float, default=None,
                        help="paper-trade-poly: capital inicial virtual en USD")
    parser.add_argument("--sizing-mode", choices=("fixed", "kelly"), default=None,
                        help="paper-trade-poly: 'fixed' o 'kelly' para tamaño por trade")
    parser.add_argument("--position-size", type=float, default=None,
                        help="paper-trade-poly: tamaño fijo por trade (solo modo fixed)")
    parser.add_argument("--kelly-fraction", type=float, default=None,
                        help="paper-trade-poly: fracción de Kelly (0.25 = quarter)")
    parser.add_argument("--max-pct-per-trade", type=float, default=None,
                        help="paper-trade-poly: cap por trade en %% del bankroll")
    parser.add_argument("--max-concurrent", type=int, default=None,
                        help="paper-trade-poly: máx posiciones abiertas simultáneas")
    parser.add_argument("--bankroll-floor", type=float, default=None,
                        help="paper-trade-poly: pausa si el bankroll cae bajo este nivel")
    parser.add_argument("--series", nargs="+", default=None,
                        help="paper-trade-poly: lista de series de Polymarket a monitorear")
    parser.add_argument("--fee-rate-pct", type=float, default=None,
                        help="paper-trade-poly: fee proporcional de Polymarket")
    parser.add_argument("--poll-sec", type=int, default=None,
                        help="paper-trade-poly: intervalo de polling en segundos")
    parser.add_argument("--state-path", default=None,
                        help="paper-trade-poly: ruta al JSON de estado")
    args = parser.parse_args()

    config = Config.from_yaml(args.config)
    commands = {
        "collect": cmd_collect,
        "backtest": cmd_backtest,
        "train-ml": cmd_train_ml,
        "train-rl": cmd_train_rl,
        "paper-trade": cmd_paper_trade,
        "init-db": cmd_init_db,
        "live-collect": cmd_live_collect,
        "poly-edge": lambda c: cmd_poly_edge(c, args),
        "paper-trade-poly": lambda c: cmd_paper_trade_poly(c, args),
    }
    asyncio.run(commands[args.command](config))


if __name__ == "__main__":
    main()
