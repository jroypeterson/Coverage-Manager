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

    wl_parser = subparsers.add_parser(
        "watchlist",
        help="Manage the personal watchlist (subset of coverage universe).",
    )
    wl_sub = wl_parser.add_subparsers(dest="wl_command", required=True)

    wl_add = wl_sub.add_parser("add", help="Add or update a ticker on the watchlist.")
    wl_add.add_argument("ticker")
    wl_add.add_argument("--buy", type=float, default=None, help="Buy price (local currency).")
    wl_add.add_argument("--target", type=float, default=None, help="Target price (local currency).")
    wl_add.add_argument("--notes", type=str, default="", help="Free-form notes.")
    wl_add.add_argument(
        "--sector",
        type=str,
        default=None,
        help=(
            "Sector (JP) — required when the ticker isn't already in the "
            "coverage universe. Passing this opts into auto-enriching a new "
            "universe row via FMP/yfinance/OpenFIGI before adding to the "
            "watchlist. Must match the user-curated taxonomy "
            "(Tech, SaaS, Fintech, Biopharma, MedTech, Life Science Tools, "
            "Healthcare Services, Other)."
        ),
    )
    wl_add.add_argument(
        "--exchange",
        type=str,
        default=None,
        help=(
            "Optional exchange hint (e.g. NASDAQ, NYSE, LSE, TSE) for when "
            "the data sources can't resolve it on their own. Only used when "
            "--sector is passed and the ticker is being newly created."
        ),
    )
    wl_add.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Preview the watchlist entry and any new universe row that would "
            "be written, without touching disk."
        ),
    )

    wl_rm = wl_sub.add_parser("remove", help="Remove a ticker from the watchlist.")
    wl_rm.add_argument("ticker")

    wl_sub.add_parser("list", help="Print the current watchlist.")
    wl_sub.add_parser("validate", help="Validate the watchlist (subset + price sanity).")

    wlr_parser = subparsers.add_parser(
        "watchlist-report",
        help="Generate the weekly watchlist performance report (Monday).",
    )
    wlr_parser.add_argument("--skip-email", action="store_true", help="Skip sending email.")
    wlr_parser.add_argument("--skip-slack", action="store_true", help="Skip Slack post.")
    wlr_parser.add_argument("--dry-run", action="store_true", help="Build report but do not email/post.")

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
    elif args.command == "check-delisted":
        from universe import delisted_check

        result = delisted_check.main(use_cache=not args.no_cache)
        raise SystemExit(0 if not result["flagged"] else 2)
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
    elif args.command == "watchlist":
        from universe import watchlist

        if args.wl_command == "add":
            try:
                result = watchlist.add(
                    args.ticker,
                    buy_price=args.buy,
                    target_price=args.target,
                    notes=args.notes,
                    create_if_missing=bool(args.sector),
                    sector_jp=args.sector,
                    exchange_hint=args.exchange,
                    dry_run=args.dry_run,
                )
            except watchlist.WatchlistError as e:
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
                print(f"Would add watchlist entry: {result['watchlist_entry']}")
            else:
                print(f"Added/updated: {result}")
        elif args.wl_command == "remove":
            removed = watchlist.remove(args.ticker)
            if removed:
                print(f"Removed {args.ticker}")
            else:
                print(f"{args.ticker} was not on the watchlist")
                raise SystemExit(1)
        elif args.wl_command == "list":
            entries = watchlist.load()
            if not entries:
                print("(watchlist is empty)")
            else:
                print(f"{'Ticker':<12}{'Buy':>10}{'Target':>10}  {'Added':<12} Notes")
                for e in entries:
                    buy = "" if e["Buy Price"] is None else f"{e['Buy Price']:g}"
                    tgt = "" if e["Target Price"] is None else f"{e['Target Price']:g}"
                    print(f"{e['Ticker']:<12}{buy:>10}{tgt:>10}  {e['Date Added']:<12} {e['Notes']}")
        elif args.wl_command == "validate":
            entries = watchlist.load()
            errors, warnings = watchlist.validate(entries)
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
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
