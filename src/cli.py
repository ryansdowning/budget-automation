"""CLI entry point for transactions categorizer."""

import argparse
import csv
import json
import sys
from pathlib import Path

from loguru import logger

from src.logging_config import DebugArtifacts, configure_logging
from src.models import CategoriesConfig
from src.pipeline import Pipeline

# Default categories file path (relative to package)
DEFAULT_CATEGORIES_PATH = Path(__file__).parent.parent / "categories" / "default.json"


def load_categories(categories_path: Path | None) -> CategoriesConfig | None:
    """Load categories from JSON file or use defaults.

    Args:
        categories_path: Path to custom categories file, or None for defaults

    Returns:
        CategoriesConfig with loaded categories, or None if no file specified
        and default doesn't exist
    """
    if categories_path is None:
        categories_path = DEFAULT_CATEGORIES_PATH
        if not categories_path.exists():
            return None

    if not categories_path.exists():
        logger.error(f"Categories file not found: {categories_path}")
        sys.exit(1)

    try:
        with open(categories_path) as f:
            data = json.load(f)
        return CategoriesConfig.model_validate(data)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse categories file: {e}")
        sys.exit(1)


def generate_summary_from_csv(
    input_path: Path,
    output_path: Path,
    categories: CategoriesConfig | None = None,
) -> None:
    """Generate a summary CSV from an existing transactions CSV.

    Args:
        input_path: Path to input CSV with 'amount' and 'category' columns
        output_path: Path for output summary CSV
        categories: Optional categories config to include zero-amount categories
    """
    # Read input CSV and sum by category
    category_totals: dict[str, float] = {}

    with open(input_path, newline="") as f:
        reader = csv.DictReader(f)

        if "amount" not in (reader.fieldnames or []):
            logger.error("Input CSV must have an 'amount' column")
            sys.exit(1)
        if "category" not in (reader.fieldnames or []):
            logger.error("Input CSV must have a 'category' column")
            sys.exit(1)

        for row in reader:
            category = row["category"]
            try:
                amount = float(row["amount"].replace("$", "").replace(",", ""))
            except ValueError:
                logger.warning(f"Skipping invalid amount: {row['amount']}")
                continue

            category_totals[category] = category_totals.get(category, 0) + amount

    # If categories provided, ensure all categories are in output
    if categories:
        for name in categories.get_category_names():
            if name not in category_totals:
                category_totals[name] = 0.0

    # Write summary CSV
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["category", "total"])
        writer.writeheader()

        # Sort by category name if using categories config, otherwise by total descending
        if categories:
            sorted_categories = list(categories.get_category_names())
        else:
            sorted_categories = sorted(
                category_totals.keys(),
                key=lambda k: category_totals[k],
                reverse=True,
            )

        for category_name in sorted_categories:
            if category_name in category_totals:
                writer.writerow({
                    "category": category_name,
                    "total": f"{category_totals[category_name]:.2f}",
                })

    logger.info(f"Wrote summary to {output_path}")
    print(f"Summary written to: {output_path}")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Parse credit card statement PDFs and categorize transactions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Process command (default behavior for PDFs)
    process_parser = subparsers.add_parser(
        "process",
        help="Process PDF statements and categorize transactions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s statement.pdf -o output.csv
  %(prog)s statements/*.pdf -o all_transactions.csv
  %(prog)s statement.pdf -c my_categories.json -o output.csv
  %(prog)s statement.pdf --dry-run  # Parse only, no categorization
        """,
    )
    process_parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="PDF file(s) to process",
    )
    process_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output.csv"),
        help="Output CSV file path (default: output.csv)",
    )
    process_parser.add_argument(
        "-c",
        "--categories",
        type=Path,
        default=None,
        help="Categories JSON file (default: built-in categories)",
    )
    process_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    process_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output and save artifacts",
    )
    process_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse PDFs only, skip categorization",
    )
    process_parser.add_argument(
        "--ollama-model",
        default="mistral",
        help="Ollama model for parsing and categorization (default: mistral)",
    )
    process_parser.add_argument(
        "--ollama-host",
        default="localhost:11434",
        help="Ollama server address (default: localhost:11434)",
    )
    process_parser.add_argument(
        "--summary",
        action="store_true",
        help="Generate a summary CSV with category totals",
    )

    # Summary command (generate summary from existing CSV)
    summary_parser = subparsers.add_parser(
        "summary",
        help="Generate a summary CSV from an existing transactions CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s transactions.csv -o summary.csv
  %(prog)s transactions.csv -o summary.csv -c categories.json  # Include all categories
        """,
    )
    summary_parser.add_argument(
        "input",
        type=Path,
        help="Input CSV file with 'amount' and 'category' columns",
    )
    summary_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("summary.csv"),
        help="Output summary CSV file path (default: summary.csv)",
    )
    summary_parser.add_argument(
        "-c",
        "--categories",
        type=Path,
        default=None,
        help="Categories JSON file (optional, fills zeros for missing categories)",
    )
    summary_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    # Parse args - if no subcommand given, check if first arg looks like a PDF
    args = parser.parse_args()

    # Handle legacy usage: if no command specified but args look like PDFs, use 'process'
    if args.command is None:
        # Re-parse with 'process' as default command
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
            # Assume it's a file path, insert 'process' command
            sys.argv.insert(1, "process")
            args = parser.parse_args()
        else:
            parser.print_help()
            sys.exit(1)

    return args


def run_process(args: argparse.Namespace) -> int:
    """Run the process command to parse PDFs and categorize transactions."""
    # Validate inputs
    pdf_paths: list[Path] = []
    for input_path in args.inputs:
        if not input_path.exists():
            logger.error(f"File not found: {input_path}")
            return 1
        if not input_path.suffix.lower() == ".pdf":
            logger.warning(f"Skipping non-PDF file: {input_path}")
            continue
        pdf_paths.append(input_path)

    if not pdf_paths:
        logger.error("No valid PDF files provided")
        return 1

    # Load categories
    categories = load_categories(args.categories)
    if categories is None:
        logger.error("Categories file required for processing")
        return 1
    logger.info(f"Loaded {len(categories.categories)} categories")

    # Parse Ollama host
    ollama_parts = args.ollama_host.split(":")
    ollama_host = ollama_parts[0]
    ollama_port = int(ollama_parts[1]) if len(ollama_parts) > 1 else 11434

    # Set up debug artifacts
    debug_artifacts = None
    if args.debug:
        debug_dir = args.output.parent / "debug"
        debug_artifacts = DebugArtifacts(debug_dir)

    # Run pipeline
    try:
        with Pipeline(
            categories=categories,
            ollama_host=ollama_host,
            ollama_port=ollama_port,
            ollama_model=args.ollama_model,
            debug_artifacts=debug_artifacts,
        ) as pipeline:
            # Process PDFs
            transactions = pipeline.process(pdf_paths, dry_run=args.dry_run)

            # Write output
            if transactions:
                pipeline.write_csv(transactions, args.output)

                if args.summary:
                    summary_path = args.output.with_stem(args.output.stem + "_summary")
                    pipeline.write_summary_csv(transactions, summary_path)
                    print(f"Summary written to: {summary_path}")

                if args.verbose or args.debug:
                    pipeline.print_summary(transactions)

                print(f"\nOutput written to: {args.output}")
            else:
                print("No transactions found.")
                return 1

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130
    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        return 1

    return 0


def run_summary(args: argparse.Namespace) -> int:
    """Run the summary command to generate summary from existing CSV."""
    if not args.input.exists():
        logger.error(f"File not found: {args.input}")
        return 1

    if args.input.suffix.lower() != ".csv":
        logger.error(f"Input must be a CSV file: {args.input}")
        return 1

    # Load categories (optional for summary)
    categories = load_categories(args.categories) if args.categories else None

    try:
        generate_summary_from_csv(args.input, args.output, categories)
    except Exception as e:
        logger.exception(f"Summary generation failed: {e}")
        return 1

    return 0


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Configure logging
    verbose = getattr(args, "verbose", False)
    debug = getattr(args, "debug", False)
    configure_logging(verbose=verbose, debug=debug)

    if args.command == "process":
        return run_process(args)
    elif args.command == "summary":
        return run_summary(args)
    else:
        logger.error(f"Unknown command: {args.command}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
