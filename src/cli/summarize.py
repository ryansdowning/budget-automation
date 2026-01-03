"""CLI for generating summary from transactions CSV."""

import argparse
import csv
import sys
from pathlib import Path

from loguru import logger

from src.cli import load_categories
from src.logging_config import configure_logging
from src.models import CategoriesConfig


def generate_summary(
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
        description="Generate a summary CSV from a transactions CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s transactions.csv -o summary.csv
  %(prog)s transactions.csv -o summary.csv -c categories.json
        """,
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Input CSV file with 'amount' and 'category' columns",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("summary.csv"),
        help="Output summary CSV file path (default: summary.csv)",
    )
    parser.add_argument(
        "-c",
        "--categories",
        type=Path,
        default=None,
        help="Categories JSON file (optional, fills zeros for missing categories)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Configure logging
    configure_logging(verbose=args.verbose, debug=False)

    # Validate input
    if not args.input.exists():
        logger.error(f"File not found: {args.input}")
        return 1

    if args.input.suffix.lower() != ".csv":
        logger.error(f"Input must be a CSV file: {args.input}")
        return 1

    # Load categories (optional)
    categories = load_categories(args.categories, required=False) if args.categories else None

    try:
        generate_summary(args.input, args.output, categories)
    except Exception as e:
        logger.exception(f"Summary generation failed: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
