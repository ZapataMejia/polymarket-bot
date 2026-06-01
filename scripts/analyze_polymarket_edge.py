"""Empirical measurement of Polymarket 'Up or Down' edge vs Binance.

Run this to answer: "Was the 2.7-second latency arb edge described in the article
really there in the last N days, after spreads and fees?"

Usage:
    python scripts/analyze_polymarket_edge.py --days 3
    python scripts/analyze_polymarket_edge.py --start 2026-05-20 --end 2026-05-25 \
        --assets bitcoin ethereum solana --threshold 0.05 --csv data/poly_edge.csv

Defaults are conservative: 1.5¢ half-spread + 0.5¢ fee + 30s guardrail before resolution.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as a plain script: add repo root to sys.path before importing src.*
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import ssl  # noqa: E402

import aiohttp  # noqa: E402
import certifi  # noqa: E402
import pandas as pd  # noqa: E402

from src.core import Config, setup_logger  # noqa: E402
from src.data.exchange import ExchangeClient  # noqa: E402
from src.polymarket.binance_klines import BinanceKlineCache  # noqa: E402
from src.polymarket.clob import ClobClient  # noqa: E402
from src.polymarket.edge import EdgeAnalyzer, EdgeConfig, summarize  # noqa: E402
from src.polymarket.gamma import GammaClient  # noqa: E402
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=3, help="Lookback window in days (ignored if --start/--end given)")
    p.add_argument("--start", type=str, default=None, help="YYYY-MM-DD UTC (inclusive)")
    p.add_argument("--end", type=str, default=None, help="YYYY-MM-DD UTC (exclusive)")
    p.add_argument(
        "--assets", nargs="+",
        default=["bitcoin", "ethereum", "solana"],
        help="Which assets to include",
    )
    p.add_argument("--threshold", type=float, default=0.05, help="Min |edge| to trigger entry (e.g. 0.05 = 5pp)")
    p.add_argument("--half-spread-cents", type=float, default=1.5)
    p.add_argument("--fee-cents", type=float, default=0.5)
    p.add_argument("--fee-rate-pct", type=float, default=2.0,
                   help="Polymarket taker fee as %% of fill price (defaults to documented 2%%)")
    p.add_argument("--series", nargs="+", default=None,
                   help="Restrict to specific series_slug list, e.g. btc-up-or-down-hourly. "
                        "When omitted, lists all crypto Up/Down via /markets/keyset (slower).")
    p.add_argument("--min-seconds-to-resolution", type=int, default=30,
                   help="Anti-look-ahead guardrail: skip entries with < N seconds left")
    p.add_argument("--min-volume", type=float, default=0.0,
                   help="Skip markets with total volume below this (USD)")
    p.add_argument("--max-markets", type=int, default=400, help="Hard cap on total # markets analyzed")
    p.add_argument("--max-markets-per-asset", type=int, default=0,
                   help="If >0, cap markets per asset (sampled by highest volume) before total cap")
    p.add_argument("--stratify-by-month", action="store_true",
                   help="Sample top-N per (asset, month) instead of global. Use for multi-month windows.")
    p.add_argument("--per-month-per-asset", type=int, default=50,
                   help="With --stratify-by-month: cap on markets per (asset, month) bucket")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--spread-sensitivity", action="store_true",
                   help="After main report, re-price PnL at half-spreads of 1/2/3/5/7¢")
    p.add_argument("--csv", type=str, default=None, help="Write per-market results to this CSV")
    p.add_argument("--config", type=str, default="config/default.yaml")
    p.add_argument("--cache-dir", type=str, default="data/raw/polymarket_binance_cache")
    return p.parse_args()


def _resolve_window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    if args.start and args.end:
        s = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
        e = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    else:
        e = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        s = e - timedelta(days=args.days)
    if e <= s:
        raise SystemExit(f"Invalid window: start={s} end={e}")
    return s, e


def _fmt_money(x: float) -> str:
    sign = "-" if x < 0 else " "
    return f"{sign}${abs(x):.4f}"


def _print_report(df: pd.DataFrame, cfg: EdgeConfig) -> None:
    total = len(df)
    if total == 0:
        print("No markets found in window. Try --days 7 or a different date range.")
        return

    print()
    print("=" * 78)
    print(" POLYMARKET 'UP OR DOWN' EDGE — EMPIRICAL TEST".center(78))
    print("=" * 78)
    print(f"  Markets analyzed       : {total}")
    print(f"  Entry threshold        : {cfg.entry_threshold*100:.1f}pp  (|p_fair - p_poly|)")
    print(f"  Half-spread charged    : {cfg.half_spread*100:.2f}¢ per fill")
    print(f"  Flat fee charged       : {cfg.fee*100:.2f}¢ per fill (gas/relayer)")
    print(f"  Proportional taker fee : {cfg.fee_rate_pct:.2f}% of fill price")
    print(f"  Min sec-to-resolution  : {cfg.min_seconds_to_resolution}s (look-ahead guardrail)")
    print()

    sig = df[df["signal"].isin(["UP", "DOWN"])].copy()
    no_sig = df[~df["signal"].isin(["UP", "DOWN"])]
    print(f"  Markets with signal    : {len(sig)}  ({len(sig)/total*100:.1f}%)")
    print(f"  Markets without signal : {len(no_sig)}")
    if not no_sig.empty:
        notes = no_sig["note"].value_counts()
        for note, n in notes.items():
            print(f"      reason {note!r:>28}: {n}")
    print()

    if sig.empty:
        print("  No signals at this threshold. Lower --threshold or extend --days.")
        return

    correct = int(sig["correct"].sum())
    win_rate = correct / len(sig)
    avg_edge = sig["signal_edge_up"].abs().mean()
    avg_fill_naive = sig.apply(
        lambda r: r["p_poly_at_signal"] if r["signal"] == "UP" else (1 - r["p_poly_at_signal"]),
        axis=1,
    ).mean()
    pnl_naive_total = sig["pnl_naive"].sum()
    pnl_real_total = sig["pnl_realistic"].sum()
    pnl_naive_mean = sig["pnl_naive"].mean()
    pnl_real_mean = sig["pnl_realistic"].mean()

    # Capital efficiency: assume we stake the *fill* on each (i.e. fill goes out of our
    # pocket); ROI = pnl / fill.
    sig["roi_naive"] = sig["pnl_naive"] / sig.apply(
        lambda r: r["p_poly_at_signal"] if r["signal"] == "UP" else (1 - r["p_poly_at_signal"]),
        axis=1,
    ).replace(0, 1e-9)
    sig["roi_realistic"] = sig["pnl_realistic"] / sig["fill_price"].replace(0, 1e-9)

    print("  Win rate               : {:.1%}  ({}/{})".format(win_rate, correct, len(sig)))
    print(f"  Avg signal |edge|      : {avg_edge*100:.2f}pp")
    print(f"  Avg naive fill price   : ${avg_fill_naive:.4f}")
    print()
    print("  --- PnL per signal ($1 stake = 1 contract paying $1 if right) ---")
    print(f"    Naive   total: {_fmt_money(pnl_naive_total)} | mean: {_fmt_money(pnl_naive_mean)} | per market: {_fmt_money(pnl_naive_total/total)}")
    print(f"    Realistic total: {_fmt_money(pnl_real_total)} | mean: {_fmt_money(pnl_real_mean)} | per market: {_fmt_money(pnl_real_total/total)}")
    print()
    print(f"  --- Capital ROI per signal (PnL / dollars at risk) ---")
    print(f"    Naive   median ROI: {sig['roi_naive'].median()*100:+.2f}% | mean: {sig['roi_naive'].mean()*100:+.2f}%")
    print(f"    Realistic median ROI: {sig['roi_realistic'].median()*100:+.2f}% | mean: {sig['roi_realistic'].mean()*100:+.2f}%")
    print()

    by_asset = sig.groupby("asset").agg(
        n=("signal", "size"),
        win_rate=("correct", "mean"),
        pnl_naive=("pnl_naive", "sum"),
        pnl_realistic=("pnl_realistic", "sum"),
        avg_edge=("signal_edge_up", lambda s: s.abs().mean()),
    )
    print("  By asset:")
    for asset, row in by_asset.iterrows():
        print(
            f"    {asset:<10} n={int(row['n']):>4}  win={row['win_rate']*100:5.1f}%  "
            f"|edge|={row['avg_edge']*100:4.2f}pp  "
            f"PnL naive={_fmt_money(row['pnl_naive'])}  realistic={_fmt_money(row['pnl_realistic'])}"
        )
    print()

    # Verdict.
    print("=" * 78)
    if pnl_real_mean > 0 and win_rate > 0.55:
        verdict = "PnL realistic POSITIVE; edge survives spread+fee in this sample."
    elif pnl_real_mean > 0:
        verdict = "PnL realistic positive but win-rate close to coin-flip; high variance."
    elif pnl_naive_mean > 0 and pnl_real_mean <= 0:
        verdict = "Naive edge exists, but spread+fee eat it. Matches the 'crowded out' thesis."
    else:
        verdict = "No edge in this sample after costs. Likely already arbed at this fidelity."
    print(f"  Verdict: {verdict}")
    print(f"  CAVEATS: 1-min data only — misses sub-second moves. Mid-price not bid/ask.")
    print(f"           No order-book depth modeled. Hold-to-resolution; no exit slippage.")
    print(f"           Sample may be small. Run with --days 14+ for stable stats.")
    print("=" * 78)


def _print_monthly_breakdown(df: pd.DataFrame, base_cfg: EdgeConfig) -> None:
    """Per-month aggregation — essential for understanding edge decay over time."""
    sig = df[df["signal"].isin(["UP", "DOWN"])].copy()
    if sig.empty:
        return
    sig["window_start"] = pd.to_datetime(sig["window_start"], utc=True, errors="coerce")
    sig = sig.dropna(subset=["window_start"])
    if sig.empty:
        return
    sig["month"] = sig["window_start"].dt.strftime("%Y-%m")

    print()
    print("=" * 78)
    print(" MONTHLY BREAKDOWN (is the edge decaying over time?)".center(78))
    print("=" * 78)
    print("  month     n     win%   |edge|   PnL_naive    PnL_real    ROI_real%")
    print("  " + "-" * 74)
    by_month = sig.groupby("month").agg(
        n=("signal", "size"),
        win=("correct", "mean"),
        edge=("signal_edge_up", lambda s: s.abs().mean()),
        pnl_naive=("pnl_naive", "sum"),
        pnl_real=("pnl_realistic", "sum"),
        capital=("fill_price", "sum"),
    )
    for month, row in by_month.iterrows():
        roi = row["pnl_real"] / row["capital"] * 100 if row["capital"] > 0 else 0.0
        print(
            f"  {month}  {int(row['n']):>4}  {row['win']*100:5.1f}%  "
            f"{row['edge']*100:5.2f}pp  ${row['pnl_naive']:>8.2f}   "
            f"${row['pnl_real']:>8.2f}   {roi:+6.2f}%"
        )
    print("=" * 78)


def _print_calibration(df: pd.DataFrame) -> None:
    """Compare model-predicted probability against realized win rate per bucket.

    Good calibration => predicted ≈ actual ± noise. Systematic mismatch reveals
    over- or under-confidence in the log-normal fair-value pricer.
    """
    sig = df[df["signal"].isin(["UP", "DOWN"])].copy()
    if sig.empty:
        return
    sig["fair_for_side"] = sig.apply(
        lambda r: r["p_fair_at_signal"] if r["signal"] == "UP" else 1 - r["p_fair_at_signal"],
        axis=1,
    )
    sig["edge_abs"] = sig["signal_edge_up"].abs()

    print()
    print("=" * 78)
    print(" MODEL CALIBRATION (predicted prob vs realized win rate)".center(78))
    print("=" * 78)
    buckets = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70),
               (0.70, 0.80), (0.80, 1.001)]
    for lo, hi in buckets:
        mask = (sig["fair_for_side"] >= lo) & (sig["fair_for_side"] < hi)
        grp = sig[mask]
        if grp.empty:
            continue
        pred = grp["fair_for_side"].mean()
        actual = grp["correct"].mean()
        diff = (actual - pred) * 100
        print(
            f"  fair in [{lo:.2f},{hi:.2f})  n={len(grp):>4}  "
            f"predicted={pred*100:5.1f}%  actual={actual*100:5.1f}%  diff={diff:+5.1f}pp"
        )

    print()
    print(" PnL by signal |edge| bucket (realistic costs):")
    edge_buckets = [(0.05, 0.075), (0.075, 0.10), (0.10, 0.15),
                    (0.15, 0.25), (0.25, 0.50), (0.50, 1.0)]
    for lo, hi in edge_buckets:
        mask = (sig["edge_abs"] >= lo) & (sig["edge_abs"] < hi)
        grp = sig[mask]
        if grp.empty:
            continue
        print(
            f"  |edge| in [{lo*100:>4.1f},{hi*100:>4.1f})pp  n={len(grp):>4}  "
            f"win={grp['correct'].mean()*100:5.1f}%  "
            f"mean PnL=${grp['pnl_realistic'].mean():+.4f}  "
            f"total=${grp['pnl_realistic'].sum():+.2f}"
        )
    print("=" * 78)


def _print_spread_sensitivity(df: pd.DataFrame, base_cfg: EdgeConfig) -> None:
    """Recompute PnL at different assumed half-spreads to show where edge dies."""
    sig = df[df["signal"].isin(["UP", "DOWN"])].copy()
    if sig.empty:
        return

    def _naive_fill(row):
        return row["p_poly_at_signal"] if row["signal"] == "UP" else (1 - row["p_poly_at_signal"])

    sig["naive_fill"] = sig.apply(_naive_fill, axis=1)
    sig["payoff"] = sig["correct"].astype(float)

    print()
    print("=" * 78)
    print(" SPREAD SENSITIVITY".center(78))
    print("=" * 78)
    print(" half-spread + fee  →  mean PnL per signal  | mean ROI on stake  | total PnL")
    print(" " + "-" * 76)
    fee = base_cfg.fee
    rate = base_cfg.fee_rate
    for half_cents in (0.0, 1.0, 1.5, 2.0, 3.0, 5.0, 7.0, 10.0):
        half = half_cents / 100.0
        fills = (sig["naive_fill"] + half).clip(upper=1.0)
        prop_fee = fills * rate
        pnl = sig["payoff"] - fills - fee - prop_fee
        roi = pnl / fills.replace(0, 1e-9)
        print(
            f"  {half_cents:4.1f}¢ + {base_cfg.fee_cents:.1f}¢ + {base_cfg.fee_rate_pct:.1f}%·fill →  "
            f"${pnl.mean():>7.4f} per signal | {roi.mean()*100:+6.2f}% ROI | "
            f"total ${pnl.sum():>8.2f} over {len(sig)} signals"
        )
    print("=" * 78)
    print(" Interpretation: this is the most important table in the report.")
    print(" 1.5¢ is what active LPs quote on liquid Up/Down markets.")
    print(" 3-5¢ is more realistic for thin/late-window markets.")
    print(" Polymarket's documented 2% taker fee already takes a big bite at avg fill ~$0.50.")
    print(" Wherever mean PnL flips negative, the 'edge' is just a model artifact.")
    print("=" * 78)


async def run() -> None:
    args = parse_args()
    config = Config.from_yaml(args.config)
    setup_logger("trading", config.log_level, config.log_file)

    start_utc, end_utc = _resolve_window(args)
    print(f"Window: {start_utc.isoformat()}  ->  {end_utc.isoformat()}")
    print(f"Assets: {args.assets}")

    edge_cfg = EdgeConfig(
        entry_threshold=args.threshold,
        half_spread_cents=args.half_spread_cents,
        fee_cents=args.fee_cents,
        fee_rate_pct=args.fee_rate_pct,
        min_seconds_to_resolution=args.min_seconds_to_resolution,
    )

    binance_client = ExchangeClient(config.exchange)
    kline_cache = BinanceKlineCache(binance_client, cache_dir=Path(args.cache_dir))

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=connector) as session:
        gamma = GammaClient(session=session)
        clob = ClobClient(session=session)
        analyzer = EdgeAnalyzer(
            gamma=gamma,
            clob=clob,
            binance_fetch_minutes=kline_cache.fetch_klines,
            config=edge_cfg,
        )

        try:
            if args.series:
                markets = []
                for slug in args.series:
                    chunk = await gamma.list_events_by_series(
                        series_slug=slug,
                        start_utc=start_utc,
                        end_utc=end_utc,
                        cache_dir=Path(args.cache_dir) / "gamma",
                    )
                    markets.extend(chunk)
                # Dedup just in case.
                seen: set[str] = set()
                deduped: list = []
                for m in markets:
                    if m.market_id not in seen:
                        seen.add(m.market_id)
                        deduped.append(m)
                markets = deduped
            else:
                markets = await gamma.list_up_down_markets(
                    start_utc=start_utc,
                    end_utc=end_utc,
                    assets=tuple(args.assets),
                    cache_dir=Path(args.cache_dir) / "gamma",
                )

            markets = [m for m in markets if m.volume_usd >= args.min_volume]

            if args.stratify_by_month:
                grouped: dict[tuple[str, str], list] = {}
                for m in markets:
                    bucket = (m.asset, m.window_end_utc.strftime("%Y-%m"))
                    grouped.setdefault(bucket, []).append(m)
                kept: list = []
                for bucket, lst in grouped.items():
                    lst.sort(key=lambda m: m.volume_usd, reverse=True)
                    kept.extend(lst[: args.per_month_per_asset])
                markets = kept
            elif args.max_markets_per_asset:
                grouped_a: dict[str, list] = {}
                for m in markets:
                    grouped_a.setdefault(m.asset, []).append(m)
                kept = []
                for asset, lst in grouped_a.items():
                    lst.sort(key=lambda m: m.volume_usd, reverse=True)
                    kept.extend(lst[: args.max_markets_per_asset])
                markets = kept

            if args.max_markets and len(markets) > args.max_markets:
                # Keep highest-volume markets — those are the ones where you actually
                # could have traded a meaningful size.
                markets.sort(key=lambda m: m.volume_usd, reverse=True)
                markets = markets[: args.max_markets]
            markets.sort(key=lambda m: m.window_end_utc)

            print(f"Up/Down markets matched: {len(markets)}")
            if not markets:
                print("Nothing to analyze. Try a wider date range.")
                return

            results = await analyzer.analyze_many(markets, concurrency=args.concurrency)
        finally:
            await binance_client.close()

    df = summarize(results)
    _print_report(df, edge_cfg)
    _print_calibration(df)
    _print_monthly_breakdown(df, edge_cfg)

    if args.spread_sensitivity:
        _print_spread_sensitivity(df, edge_cfg)

    if args.csv:
        out = Path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"\nWrote per-market results to {out}")


def main() -> int:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
