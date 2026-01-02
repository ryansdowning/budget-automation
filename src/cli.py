"""CLI entry point for transactions categorizer."""

import argparse
import json
import sys
from pathlib import Path

from loguru import logger

from src.logging_config import DebugArtifacts, configure_logging
from src.models import CategoriesConfig
from src.pipeline import Pipeline

# Default categories file path (relative to package)
DEFAULT_CATEGORIES_PATH = Path(__file__).parent.parent / "categories" / "default.json"


def load_categories(categories_path: Path | None) -> CategoriesConfig:
    """Load categories from JSON file or use defaults.

    Args:
        categories_path: Path to custom categories file, or None for defaults

    Returns:
        CategoriesConfig with loaded categories
    """
    if categories_path is None:
        categories_path = DEFAULT_CATEGORIES_PATH

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


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Parse credit card statement PDFs and categorize transactions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s statement.pdf -o output.csv
  %(prog)s statements/*.pdf -o all_transactions.csv
  %(prog)s statement.pdf -c my_categories.json -o output.csv
  %(prog)s statement.pdf --dry-run  # Parse only, no categorization
        """,
    )

    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="PDF file(s) to process",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output.csv"),
        help="Output CSV file path (default: output.csv)",
    )
    parser.add_argument(
        "-c",
        "--categories",
        type=Path,
        default=None,
        help="Categories JSON file (default: built-in categories)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output and save artifacts",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse PDFs only, skip categorization",
    )
    parser.add_argument(
        "--ollama-model",
        default="mistral",
        help="Ollama model for parsing and categorization (default: mistral)",
    )
    parser.add_argument(
        "--ollama-host",
        default="localhost:11434",
        help="Ollama server address (default: localhost:11434)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Generate a summary CSV with category totals",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Configure logging
    configure_logging(verbose=args.verbose, debug=args.debug)

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


if __name__ == "__main__":
    sys.exit(main())
