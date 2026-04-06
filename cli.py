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

    wb_parser = subparsers.add_parser(
        "weekly-build",
        help="Run the full weekly coverage workflow.",
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
    elif args.command == "weekly-build":
        import weekly_build

        weekly_build.main(
            skip_discovery=args.skip_discovery,
            skip_performance=args.skip_performance,
            skip_email=args.skip_email,
            dry_run=args.dry_run,
            force=args.force,
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
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
