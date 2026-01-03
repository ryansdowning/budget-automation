"""CLI for generating summary from transactions CSV."""

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.cli import load_categories
from src.logging_config import configure_logging
from src.models import CategoriesConfig


def parse_date(date_str: str) -> tuple[int, int] | None:
    """Parse a date string and return (year, month).

    Supports formats: YYYY-MM-DD, MM/DD/YYYY, MM/DD/YY
    """
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return (dt.year, dt.month)
        except ValueError:
            continue
    return None


def generate_summary(
    input_path: Path,
    output_path: Path,
    categories: CategoriesConfig | None = None,
) -> None:
    """Generate a summary CSV from an existing transactions CSV.

    Groups transactions by year, month, and category.

    Args:
        input_path: Path to input CSV with 'date', 'amount' and 'category' columns
        output_path: Path for output summary CSV
        categories: Optional categories config to include zero-amount categories
    """
    # Read input CSV and sum by (year, month, category)
    # Key: (year, month, category) -> total
    totals: dict[tuple[int, int, str], float] = defaultdict(float)
    year_months: set[tuple[int, int]] = set()

    with open(input_path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        if "amount" not in fieldnames:
            logger.error("Input CSV must have an 'amount' column")
            sys.exit(1)
        if "category" not in fieldnames:
            logger.error("Input CSV must have a 'category' column")
            sys.exit(1)
        if "date" not in fieldnames:
            logger.error("Input CSV must have a 'date' column")
            sys.exit(1)

        for row in reader:
            category = row["category"]

            # Parse date
            date_result = parse_date(row["date"])
            if date_result is None:
                logger.warning(f"Skipping row with invalid date: {row['date']}")
                continue
            year, month = date_result
            year_months.add((year, month))

            # Parse amount
            try:
                amount = float(row["amount"].replace("$", "").replace(",", ""))
            except ValueError:
                logger.warning(f"Skipping invalid amount: {row['amount']}")
                continue

            totals[(year, month, category)] += amount

    # If categories provided, ensure all categories are in output for each year-month
    if categories:
        for year, month in year_months:
            for name in categories.get_category_names():
                key = (year, month, name)
                if key not in totals:
                    totals[key] = 0.0

    # Write summary CSV
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["year", "month", "category", "total"])
        writer.writeheader()

        # Get sorted list of (year, month, category) keys
        if categories:
            category_order = {name: i for i, name in enumerate(categories.get_category_names())}
            sorted_keys = sorted(
                totals.keys(),
                key=lambda k: (k[0], k[1], category_order.get(k[2], 999)),
            )
        else:
            # Sort by year, month, then total descending
            sorted_keys = sorted(
                totals.keys(),
                key=lambda k: (k[0], k[1], -totals[k]),
            )

        for year, month, category in sorted_keys:
            writer.writerow({
                "year": year,
                "month": month,
                "category": category,
                "total": f"{totals[(year, month, category)]:.2f}",
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
