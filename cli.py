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

    cik_parser = subparsers.add_parser(
        "backfill-cik",
        help=(
            "Fill blank CIKs from SEC EDGAR's free bulk ticker map. A CIK is a "
            "fact about whether a company has REGISTERED YET, so blanks must be "
            "re-probed: a name that registers after its row was enriched keeps a "
            "blank CIK forever and every CIK-keyed lane silently skips it "
            "(SpaceX, 2026-07-25). One HTTP GET; never overwrites an existing CIK."
        ),
    )
    cik_parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be filled without writing the CSV.",
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

    fid_parser = subparsers.add_parser(
        "backfill-foreign-ids",
        help=(
            "Recover ISIN + LEI for foreign-listed rows by joining broad international "
            "ETF holdings files (local ticker + country) to the same funds' SEC N-PORT "
            "filings (ISIN + LEI). Fills the 200 foreign rows yfinance and FMP cannot "
            "resolve. Never overwrites an existing value."
        ),
    )
    fid_parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be filled without writing the universe CSV.",
    )
    fid_parser.add_argument(
        "--no-cache", action="store_true",
        help="Bypass the 7-day source cache and refetch the holdings + N-PORT files.",
    )
    fid_parser.add_argument(
        "--limit", type=int, default=None,
        help="Apply only the first N proposals (bounds a first run).",
    )

    fcc_parser = subparsers.add_parser(
        "crosscheck-foreign",
        help=(
            "Audit foreign-row metadata (ISIN, LEI, currency, name) against the same "
            "SEC N-PORT + fund-holdings sources backfill-foreign-ids uses. Read-only. "
            "Reports incorporation-vs-HQ separately - those are not errors."
        ),
    )
    fcc_parser.add_argument(
        "--no-cache", action="store_true",
        help="Bypass the 7-day source cache and refetch.",
    )

    vii_parser = subparsers.add_parser(
        "verify-isin-issuers",
        help=(
            "Identity-check every stored ISIN against the issuer name OpenFIGI "
            "maps it to (the prefix guard is a country check, not an identity "
            "check). Read-only; conflicts are evidence for a human call. "
            "Verdicts are ok / conflict / inconclusive - an unreachable API is "
            "inconclusive, never clean."
        ),
    )
    vii_parser.add_argument(
        "--no-cache", action="store_true",
        help="Bypass the ISIN->name cache and refetch from OpenFIGI.",
    )
    vii_parser.add_argument(
        "--sample", type=int, default=None,
        help="Check only the first N ISIN-bearing rows (bounds a first run).",
    )
    vii_parser.add_argument(
        "--tickers", nargs="*", default=None,
        help="Restrict the check to these tickers.",
    )

    rcn_parser = subparsers.add_parser(
        "resolve-cik-by-name",
        help=(
            "Find blank-CIK rows whose COMPANY NAME matches an SEC registrant "
            "filing under a DIFFERENT ticker. Closes the circular blind spot "
            "where backfill-cik (keyed on the current ticker) and "
            "check-ticker-changes (keyed on the CIK) both miss a renamed row. "
            "REPORT-ONLY - it never writes."
        ),
    )
    rcn_parser.add_argument(
        "--limit", type=int, default=None,
        help="Check only the first N blank-CIK rows.")
    rcn_parser.add_argument(
        "--tickers", nargs="*", default=None,
        help="Restrict the sweep to these tickers.")

    crsp_parser = subparsers.add_parser(
        "crsp-snapshot",
        help=(
            "Archive the current CRSP/Morningstar US Total Market constituent list "
            "(~3,500 names with weights) and diff it against the prior quarter. CRSP "
            "OVERWRITES this file each quarter with no archive, so a missed quarter "
            "is unrecoverable. Also refreshes the daily PR+TR index-level history."
        ),
    )
    crsp_parser.add_argument(
        "--force", action="store_true",
        help="Re-archive even if this quarter's TradeDate is already captured.",
    )
    crsp_parser.add_argument(
        "--skip-levels", action="store_true",
        help="Skip the 12 MB daily index-levels refresh (constituents only).",
    )
    crsp_parser.add_argument(
        "--archive-levels", action="store_true",
        help=("Force a dated gzip copy of the index-level history into "
              "data/crsp/archive/ (normally written only when a new quarter lands)."),
    )
    crsp_parser.add_argument(
        "--dry-run", action="store_true",
        help="Download and verify, report the delta, write nothing.",
    )
    crsp_parser.add_argument(
        "--no-reconcile", action="store_true",
        help="Skip the coverage-universe reconciliation section of the report.",
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
        # A degraded run also exits 2: it did not learn what it was asked to
        # learn, and exiting 0 would report that silence as a clean universe.
        raise SystemExit(2 if (result["flagged"] or result.get("degraded")) else 0)
    elif args.command == "check-ticker-changes":
        from universe import ticker_change_check

        result = ticker_change_check.main(use_cache=not args.no_cache)
        flagged = len(result["changes"]) + len(result["deregistered"])
        raise SystemExit(0 if (result["sec_fetched_ok"] and not flagged) else 2)
    elif args.command == "backfill-cik":
        from universe import cik_backfill

        result = cik_backfill.main(dry_run=args.dry_run)
        # Exit 2 when the SEC fetch failed: a silent no-op here would let the
        # very gap this step closes reopen unnoticed.
        raise SystemExit(0 if result["fetched_ok"] else 2)
    elif args.command == "backfill-lei":
        from universe import lei_backfill

        lei_backfill.main(use_cache=not args.no_cache, limit=args.limit)
    elif args.command == "backfill-foreign-ids":
        from universe import foreign_identifiers

        result = foreign_identifiers.main(
            dry_run=args.dry_run, use_cache=not args.no_cache, limit=args.limit,
        )
        report = foreign_identifiers.write_report(result)
        print(f"status: {result.status}")
        print(f"map keys: {result.map_size:,}  eligible rows: {result.candidates:,}  "
              f"proposals: {len(result.proposals):,}")
        print(f"ISIN written: {result.isin_written}  LEI written: {result.lei_written}")
        for f in result.funds_failed:
            print(f"WARNING: source failed - {f}")
        for e in result.errors:
            print(f"ERROR: {e}")
        print(f"report: {report}")
        # Exit 2 when no source answered: a run that learned nothing must not
        # report a clean universe (mirrors backfill-cik / check-delisted).
        raise SystemExit(0 if result.status != "failed" else 2)
    elif args.command == "crosscheck-foreign":
        from universe import foreign_crosscheck

        result = foreign_crosscheck.main(use_cache=not args.no_cache)
        report = foreign_crosscheck.write_report(result)
        print(f"status: {result.status}")
        print(f"checked: {result.checked:,}  matched: {result.matched:,}  "
              f"unmatched: {result.unmatched:,}")
        print(f"conflicts: {len(result.conflicts)}  "
              f"incorporation notes: {len(result.incorporation_notes)}")
        for f in result.funds_failed:
            print(f"WARNING: source failed - {f}")
        for e in result.errors:
            print(f"ERROR: {e}")
        print(f"report: {report}")
        # Exit 2 on a real conflict OR on a run that learned nothing, so a
        # scheduled invocation cannot report silence as agreement.
        raise SystemExit(2 if (result.conflicts or not result.ok) else 0)
    elif args.command == "resolve-cik-by-name":
        from universe import cik_name_resolver as rcn
        result, report = rcn.main(tickers=args.tickers, limit=args.limit)
        print(f"checked {result.checked} blank-CIK row(s)")
        for verdict in (rcn.STALE_US_LISTING, rcn.AMBIGUOUS_NAME,
                        rcn.LEDGER_CONFLICT, rcn.SEC_REGISTERED_OTHER_LINE,
                        rcn.SHORT_NAME_SUPPRESSED, rcn.NO_MATCH):
            print(f"  {verdict:28} {len(result.by_verdict(verdict))}")
        print(f"report: {report}")
        if not result.fetched_ok:
            print("SEC map unavailable - this run learned NOTHING, not 'clean'")
            return 2
        return 2 if result.needs_review else 0

    elif args.command == "verify-isin-issuers":
        from universe import isin_identity

        result = isin_identity.main(
            tickers=args.tickers, sample=args.sample,
            use_cache=not args.no_cache,
        )

        def _a(text):
            # The universe is global and this console is cp1252 — sanitize
            # the DATA, not just the format string.
            return str(text).encode("ascii", "backslashreplace").decode("ascii")

        print(f"checked: {result['checked']:,} ISIN-bearing rows "
              f"({result['no_isin']:,} rows carry no ISIN)")
        print(f"ok: {result['ok']:,}  conflicts: {len(result['conflicts'])}  "
              f"inconclusive: {len(result['inconclusive'])}")
        for r in result["conflicts"]:
            print(_a(f"CONFLICT {r['ticker']}: '{r['company']}' vs "
                     f"{r['isin']} -> {'; '.join(r['openfigi_names'])}"))
        for r in result["inconclusive"]:
            print(_a(f"inconclusive {r['ticker']}: {r['isin']} ({r['reason']})"))
        print(f"report: {result['report_path']}")
        # Exit 2 on any conflict OR when nothing was learned at all — an
        # unreachable API must never exit 0 and read as a clean universe.
        learned_nothing = result["checked"] > 0 and result["ok"] == 0 and not result["conflicts"]
        raise SystemExit(2 if (result["conflicts"] or learned_nothing) else 0)
    elif args.command == "crsp-snapshot":
        import csv as _csv

        import config
        from universe import crsp_snapshot

        result = crsp_snapshot.snapshot(
            force=args.force,
            skip_levels=args.skip_levels,
            archive_levels=args.archive_levels,
            dry_run=args.dry_run,
        )

        recon = None
        if result.ok and not args.no_reconcile and result.path and result.path.exists():
            tm_rows = [
                r for r in crsp_snapshot.parse_constituents(result.path)
                if r["Index Ticker"].strip() == crsp_snapshot.TOTAL_MARKET_KEY
            ]
            with config.CSV_PATH.open(newline="", encoding="utf-8-sig") as fh:
                recon = crsp_snapshot.reconcile_universe(tm_rows, list(_csv.DictReader(fh)))

        report = crsp_snapshot.write_report(result, recon)
        print(f"status: {result.status}")
        if result.failure_kind:
            # Say WHICH kind of failure before the detail. A moved URL needs a
            # human today; a network blip needs nothing but next Monday. Same
            # exit code, opposite response — so the distinction has to be in the
            # message, not inferable only from the traceback.
            print(f"failure kind: {result.failure_kind}")
            print(crsp_snapshot.failure_guidance(result.failure_kind))
        if result.trade_date:
            print(f"trade date: {result.trade_date}  constituents: {result.constituent_count:,}")
        if result.prior_trade_date:
            print(
                f"delta vs {result.prior_trade_date}: "
                f"+{len(result.added)} added, -{len(result.dropped)} dropped"
            )
        if result.levels_archive:
            print(f"levels archived: {result.levels_archive.name}")
        for w in result.warnings:
            print(f"WARNING: {w}")
        for e in result.errors:
            print(f"ERROR: {e}")
        print(f"report: {report}")
        # Exit 2 on failure so a scheduled run that learned nothing cannot report
        # success — the whole point of the job is that a missed quarter is
        # unrecoverable, which makes a silent failure the expensive outcome.
        raise SystemExit(0 if result.ok else 2)
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
