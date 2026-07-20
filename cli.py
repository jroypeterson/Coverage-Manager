import argparse

from logging_utils import configure_logging


def build_parser():
    parser = argparse.ArgumentParser(
        description="Coverage Manager command line interface."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("add-exchanges", help="Populate or normalize Exchange values.")
    subparsers.add_parser("cleanup", help="Clean and deduplicate the coverage CSV.")
    subparsers.add_parser("enrich", help="Enrich the coverage CSV with identifiers.")
    subparsers.add_parser("validate", help="Validate the coverage CSV for errors and warnings.")
    subparsers.add_parser(
        "baskets",
        help="Build the thematic-basket returns table (AI trade, GLP-1, obesity, "
             "Alzheimer's, MRD, oncology; cap- & equal-weighted, WTD/QTD/YTD/2025) "
             "into reports/ from the latest performance snapshot.",
    )

    dc_parser = subparsers.add_parser(
        "check-delisted",
        help=(
            "Probe yfinance for each universe ticker and flag those that look "
            "delisted, acquired, or recycled to a non-equity instrument."
        ),
    )
    dc_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the identity cache and refetch from yfinance.",
    )

    tc_parser = subparsers.add_parser(
        "check-ticker-changes",
        help=(
            "Use SEC EDGAR's stable CIK->ticker map to discover universe rows "
            "whose ticker has CHANGED (rename) or whose CIK is no longer listed "
            "(possible deregistration). Surfaces the NEW symbol so a renamed "
            "row can be remapped instead of removed."
        ),
    )
    tc_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the SEC ticker-map cache and refetch from sec.gov.",
    )

    lei_parser = subparsers.add_parser(
        "backfill-lei",
        help=(
            "Fill the LEI (Legal Entity Identifier) column from GLEIF, keyed by "
            "ISIN — the official cross-provider entity ID. Only looks up rows "
            "with an ISIN and no LEI; results cached 90 days."
        ),
    )
    lei_parser.add_argument(
        "--no-cache", action="store_true",
        help="Bypass the LEI cache and refetch from GLEIF.",
    )
    lei_parser.add_argument(
        "--limit", type=int, default=None,
        help="Only look up the first N missing rows (for a test pass).",
    )

    hist_parser = subparsers.add_parser(
        "history-backfill",
        help=(
            "Populate the FMP 5Y/10Y valuation-history cache for the full coverage "
            "universe. Resumable — already-cached names are skipped, so a run that "
            "dies partway costs nothing to resume. ~3 FMP calls per uncached ticker."
        ),
    )
    hist_parser.add_argument(
        "--limit", type=int, default=None,
        help="Only fetch the first N pending tickers (bounds a test/partial run).",
    )
    hist_parser.add_argument(
        "--tickers", type=str, default=None,
        help="Comma-separated ticker list to fetch instead of the full universe.",
    )
    hist_parser.add_argument(
        "--refresh", action="store_true",
        help="Bypass the cache and refetch everything in scope (expensive).",
    )
    hist_parser.add_argument(
        "--max-workers", type=int, default=10,
        help="Parallel fetch width (FMP is globally rate-limited at 300/min regardless).",
    )

    ipo_parser = subparsers.add_parser(
        "ipo-backfill",
        help=(
            "Fill the IPO Date + estimated 90/180-day lockup columns from "
            "Renaissance Capital, keyed by CIK/ticker. A metered verifier — FREE "
            "tier is 120 calls/MONTH, so it only looks up rows with a blank IPO "
            "Date, caches forever, and stops when the monthly budget is reached."
        ),
    )
    ipo_parser.add_argument(
        "--no-cache", action="store_true",
        help="Bypass the IPO cache and refetch from Renaissance.",
    )
    ipo_parser.add_argument(
        "--limit", type=int, default=None,
        help="Only look up the first N eligible rows (recommended — conserves the monthly quota). Recent IPOs go first.",
    )
    ipo_parser.add_argument(
        "--min-year", type=int, default=None,
        help="Skip rows listed before this year (e.g. 2024 for the last ~2 years of IPOs).",
    )
    ipo_parser.add_argument(
        "--include-foreign", action="store_true",
        help="Also attempt rows without a CIK. Off by default — Renaissance is US-IPO-only, so these always 404 and waste quota.",
    )

    perf_parser = subparsers.add_parser(
        "performance",
        help="Generate the Excel and HTML performance reports.",
    )
    perf_parser.add_argument(
        "--sample",
        action="store_true",
        help="Generate a reduced sample preview instead of the full report.",
    )
    perf_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass cache and fetch fresh data from all sources.",
    )

    movers_parser = subparsers.add_parser(
        "movers",
        help=(
            "Generate the weekly movers report — flag tickers with extreme "
            "1W returns and pull a Finnhub-news + Claude-summary 'why' for "
            "each. Reads the perf snapshot written by the most recent "
            "`performance` run."
        ),
    )
    movers_parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Snapshot date to load (YYYY-MM-DD). Defaults to today.",
    )
    movers_parser.add_argument(
        "--no-news",
        action="store_true",
        help="Skip Finnhub news + Anthropic summary; flag-only output.",
    )
    movers_parser.add_argument(
        "--no-slack",
        action="store_true",
        help="Skip the Slack post (still writes HTML/MD files).",
    )

    crosscheck_parser = subparsers.add_parser(
        "cross-check",
        help="Compare overlapping fields across providers and flag large discrepancies.",
    )
    crosscheck_parser.add_argument(
        "--sample",
        action="store_true",
        help="Run the comparison on the sample ticker set only.",
    )
    crosscheck_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass cache and fetch fresh data from all sources.",
    )

    wb_parser = subparsers.add_parser(
        "weekly-build",
        help="Run the full weekly coverage workflow (universe + reporting).",
    )
    wb_parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Skip the discovery step.",
    )
    wb_parser.add_argument(
        "--skip-performance",
        action="store_true",
        help="Skip performance report generation.",
    )
    wb_parser.add_argument(
        "--skip-email",
        action="store_true",
        help="Skip sending email.",
    )
    wb_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report only, no mutations.",
    )
    wb_parser.add_argument(
        "--force",
        action="store_true",
        help="Continue past validation errors instead of halting.",
    )

    wu_parser = subparsers.add_parser(
        "weekly-universe",
        help="Run only the universe-side weekly pipeline (validate, discovery, exports, sigma-export).",
    )
    wu_parser.add_argument("--skip-discovery", action="store_true", help="Skip the discovery step.")
    wu_parser.add_argument("--dry-run", action="store_true", help="Validate and report only, no mutations.")
    wu_parser.add_argument(
        "--force",
        action="store_true",
        help="Informational; the universe pipeline does not gate on validation, but the flag is accepted for symmetry with weekly-build.",
    )

    wr_parser = subparsers.add_parser(
        "weekly-report",
        help="Run only the reporting-side weekly pipeline (performance, email).",
    )
    wr_parser.add_argument("--skip-email", action="store_true", help="Skip sending email.")
    wr_parser.add_argument("--dry-run", action="store_true", help="Validate and report only, no mutations.")

    pos_parser = subparsers.add_parser(
        "positions",
        help=(
            "Manage the positions and researching list (data/positions_and_researching.csv) — "
            "names the user owns (Portfolio), is actively researching (Researching), "
            "passively follows (Following for Interest), or is trigger-ready on "
            "either side (Ready to Buy / Ready to Short). "
            "Replaces the older `watchlist` subcommand."
        ),
    )
    pos_sub = pos_parser.add_subparsers(dest="pos_command", required=True)

    pos_add = pos_sub.add_parser("add", help="Add or update a ticker.")
    pos_add.add_argument("ticker")
    pos_add.add_argument(
        "--position",
        choices=[
            "Portfolio", "Researching", "Following for Interest",
            "Ready to Buy", "Ready to Short",
        ],
        required=True,
        help=(
            "Position state — Portfolio (held), Researching (thesis-building), "
            "Following for Interest (passive earnings/signal tracking, no intent "
            "to trade), Ready to Buy (long thesis done, waiting for entry "
            "trigger), or Ready to Short (short thesis done, waiting for entry "
            "trigger)."
        ),
    )
    pos_add.add_argument("--buy", type=float, default=None, help="Buy price target (entry).")
    pos_add.add_argument("--sell", type=float, default=None, help="Sell price target (exit).")
    pos_add.add_argument("--first-buy-date", type=str, default="", help="First buy date (ISO).")
    pos_add.add_argument("--average-cost", type=float, default=None, help="Average cost basis.")
    pos_add.add_argument("--shares", type=int, default=None, help="Shares held.")
    pos_add.add_argument("--notes", type=str, default="", help="Free-form notes.")
    pos_add.add_argument(
        "--sector", type=str, default=None,
        help=(
            "Sector (JP) — required when the ticker isn't already in the coverage "
            "universe. Same auto-enrichment path as `watchlist add`."
        ),
    )
    pos_add.add_argument(
        "--exchange", type=str, default=None,
        help="Optional exchange hint for new universe rows.",
    )
    pos_add.add_argument(
        "--dry-run", action="store_true",
        help="Preview without writing.",
    )

    pos_rm = pos_sub.add_parser("remove", help="Remove a ticker.")
    pos_rm.add_argument("ticker")

    pos_sub.add_parser("list", help="Print all positions.")
    pos_sub.add_parser("validate", help="Validate (subset + Position enum + universe metadata).")

    wlr_parser = subparsers.add_parser(
        "watchlist-report",
        help="Generate the weekly watchlist performance report (Monday).",
    )
    wlr_parser.add_argument("--skip-email", action="store_true", help="Skip sending email.")
    wlr_parser.add_argument("--skip-slack", action="store_true", help="Skip Slack post.")
    wlr_parser.add_argument("--dry-run", action="store_true", help="Build report but do not email/post.")

    sx_parser = subparsers.add_parser(
        "sigma-export",
        help=(
            "Push ticker_metadata.json, portfolio.json, researching.json, and "
            "core_watchlist.json from the current Coverage Manager universe "
            "into the sibling sigma-alert clone (commits + pushes if changed). "
            "Useful to refresh sigma-alert immediately after a taxonomy or "
            "data change without waiting for the Friday weekly-universe cron."
        ),
    )
    sx_parser.add_argument(
        "--no-push",
        action="store_true",
        help="Write + commit locally in the sigma-alert clone but skip the push to origin.",
    )

    cache_parser = subparsers.add_parser(
        "cache-clear",
        help="Clear cached external data.",
    )
    cache_parser.add_argument(
        "--namespace",
        type=str,
        default=None,
        help="Clear only a specific namespace (e.g., fundamentals, prices, constituents).",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(verbose=args.verbose)

    if args.command == "add-exchanges":
        from universe import add_exchanges

        add_exchanges.main()
    elif args.command == "cleanup":
        from universe import cleanup

        cleanup.main()
    elif args.command == "enrich":
        from universe import enrich

        enrich.main()
    elif args.command == "validate":
        from universe import validation

        exit_code = validation.main()
        raise SystemExit(exit_code)
    elif args.command == "baskets":
        from reporting import thematic_baskets

        path = thematic_baskets.build()
        print(f"Wrote thematic-basket returns: {path}")
    elif args.command == "check-delisted":
        from universe import delisted_check

        result = delisted_check.main(use_cache=not args.no_cache)
        raise SystemExit(0 if not result["flagged"] else 2)
    elif args.command == "check-ticker-changes":
        from universe import ticker_change_check

        result = ticker_change_check.main(use_cache=not args.no_cache)
        flagged = len(result["changes"]) + len(result["deregistered"])
        raise SystemExit(0 if (result["sec_fetched_ok"] and not flagged) else 2)
    elif args.command == "backfill-lei":
        from universe import lei_backfill

        lei_backfill.main(use_cache=not args.no_cache, limit=args.limit)
    elif args.command == "history-backfill":
        from universe import history_backfill

        history_backfill.main(
            limit=args.limit,
            tickers=args.tickers,
            use_cache=not args.refresh,
            max_workers=args.max_workers,
        )
    elif args.command == "ipo-backfill":
        from universe import ipo_backfill

        ipo_backfill.main(
            use_cache=not args.no_cache,
            limit=args.limit,
            us_only=not args.include_foreign,
            min_year=args.min_year,
        )
    elif args.command == "weekly-build":
        import weekly_build

        weekly_build.main(
            skip_discovery=args.skip_discovery,
            skip_performance=args.skip_performance,
            skip_email=args.skip_email,
            dry_run=args.dry_run,
            force=args.force,
        )
    elif args.command == "weekly-universe":
        import weekly_universe

        weekly_universe.main(
            skip_discovery=args.skip_discovery,
            dry_run=args.dry_run,
            force=args.force,
        )
    elif args.command == "weekly-report":
        import weekly_report

        weekly_report.main(
            skip_email=args.skip_email,
            dry_run=args.dry_run,
        )
    elif args.command == "positions":
        from universe import positions

        if args.pos_command == "add":
            try:
                result = positions.add(
                    args.ticker,
                    position=args.position,
                    buy_price=args.buy,
                    sell_price=args.sell,
                    first_buy_date=args.first_buy_date,
                    average_cost=args.average_cost,
                    shares=args.shares,
                    notes=args.notes,
                    create_if_missing=bool(args.sector),
                    sector_jp=args.sector,
                    exchange_hint=args.exchange,
                    dry_run=args.dry_run,
                )
            except positions.PositionsError as e:
                print(f"Error: {e}")
                raise SystemExit(1)
            if args.dry_run:
                print("[dry-run] no files written")
                if result.get("would_create_universe_row"):
                    print("Would append new universe row:")
                    for k, v in result["universe_row"].items():
                        if v:
                            print(f"  {k}: {v}")
                    print()
                print(f"Would add positions entry: {result['positions_entry']}")
            else:
                print(f"Added/updated: {result}")
        elif args.pos_command == "remove":
            removed = positions.remove(args.ticker)
            if removed:
                print(f"Removed {args.ticker}")
            else:
                print(f"{args.ticker} was not in the positions file")
                raise SystemExit(1)
        elif args.pos_command == "list":
            entries = positions.load()
            if not entries:
                print("(positions file is empty)")
            else:
                print(f"{'Ticker':<10}{'Position':<16}{'Buy':>10}{'Sell':>10}  {'Date':<12} Notes")
                for e in entries:
                    buy = "" if e["Buy Price"] is None else f"{e['Buy Price']:g}"
                    sell = "" if e["Sell Price"] is None else f"{e['Sell Price']:g}"
                    print(f"{e['Ticker']:<10}{e['Position']:<16}{buy:>10}{sell:>10}  {e['Position Date']:<12} {e['Notes']}")
                counts = {
                    name: sum(1 for e in entries if e["Position"] == name)
                    for name in positions.POSITION_VALUES_ORDERED
                }
                summary = ", ".join(f"{n} {name}" for name, n in counts.items())
                print(f"\nTotal: {len(entries)} ({summary})")
        elif args.pos_command == "validate":
            entries = positions.load()
            errors, warnings = positions.validate(entries)
            for w in warnings:
                print(f"WARN: {w}")
            for err in errors:
                print(f"ERROR: {err}")
            print(f"{len(entries)} entries, {len(errors)} errors, {len(warnings)} warnings")
            raise SystemExit(0 if not errors else 1)
    elif args.command == "watchlist-report":
        from reporting import watchlist_report

        watchlist_report.main(
            skip_email=args.skip_email,
            skip_slack=args.skip_slack,
            dry_run=args.dry_run,
        )
    elif args.command == "sigma-export":
        from config import CSV_PATH
        from reporting.sigma_export import export_and_push

        result = export_and_push(CSV_PATH, push=not args.no_push)
        status = result.get("status", "unknown")
        print(f"sigma-export: {status}")
        for k, v in result.items():
            if k == "status":
                continue
            print(f"  {k}: {v}")
        # Exit non-zero on failure so it's a useful command for scripts
        if status.startswith("failed") or status == "committed_not_pushed":
            raise SystemExit(2)
    elif args.command == "cache-clear":
        from cache import cache_clear, cache_stats

        before = cache_stats()
        count = cache_clear(namespace=args.namespace)
        print(f"Cleared {count} cache entries")
        if before:
            print(f"Namespaces before clear: {before}")
    elif args.command == "performance":
        from reporting import generate

        generate.main(sample_mode=args.sample, refresh=args.refresh)
    elif args.command == "cross-check":
        import source_validation

        source_validation.main(sample_mode=args.sample, refresh=args.refresh)
    elif args.command == "movers":
        from movers_runner import run_movers_cli

        exit_code = run_movers_cli(
            snapshot_date=args.date,
            skip_news=args.no_news,
            skip_slack=args.no_slack,
        )
        raise SystemExit(exit_code)
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
