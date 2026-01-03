"""CLI for re-categorizing transactions from an existing CSV.

This allows you to re-run categorization with updated keywords or categories
without re-parsing PDFs (which is expensive).
"""

import argparse
import csv
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from loguru import logger

from src.categorizer import Categorizer
from src.cli import load_categories
from src.clients.ollama import OllamaClient
from src.logging_config import DebugArtifacts, configure_logging
from src.models import CategorizedTransaction, RawTransaction


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Re-categorize transactions from an existing CSV file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s transactions.csv -o recategorized.csv
  %(prog)s transactions.csv -c updated_categories.json -o recategorized.csv
  %(prog)s transactions.csv --dry-run  # Show what would change
        """,
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Input CSV file with transactions (requires date, description, amount columns)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output CSV file path (default: input_recategorized.csv)",
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
        help="Show category changes without writing output",
    )
    parser.add_argument(
        "--ollama-model",
        default="mistral",
        help="Ollama model for categorization (default: mistral)",
    )
    parser.add_argument(
        "--ollama-host",
        default="localhost:11434",
        help="Ollama server address (default: localhost:11434)",
    )
    parser.add_argument(
        "--show-changes",
        action="store_true",
        help="Show only transactions that changed category",
    )

    return parser.parse_args()


def load_csv_transactions(csv_path: Path) -> tuple[list[RawTransaction], dict[str, str]]:
    """Load transactions from CSV file.

    Args:
        csv_path: Path to input CSV

    Returns:
        Tuple of (list of RawTransaction, dict mapping description to old category)
    """
    transactions: list[RawTransaction] = []
    old_categories: dict[str, str] = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        # Validate required columns
        if reader.fieldnames is None:
            raise ValueError("CSV file is empty or has no headers")

        required = {"date", "description", "amount"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV missing required columns: {missing}")

        has_category = "category" in reader.fieldnames

        for row in reader:
            try:
                # Parse date
                date_str = row["date"].strip()
                parsed_date = None
                for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"]:
                    try:
                        parsed_date = datetime.strptime(date_str, fmt).date()
                        break
                    except ValueError:
                        continue

                if parsed_date is None:
                    logger.warning(f"Could not parse date: {date_str}, skipping row")
                    continue

                # Parse amount
                amount_str = row["amount"].strip().replace("$", "").replace(",", "")
                try:
                    amount = Decimal(amount_str)
                except InvalidOperation:
                    logger.warning(f"Could not parse amount: {row['amount']}, skipping row")
                    continue

                description = row["description"].strip()
                if not description:
                    continue

                transactions.append(
                    RawTransaction(
                        date=parsed_date,
                        description=description,
                        amount=amount,
                        raw_text="",
                    )
                )

                # Track old category if present
                if has_category and row.get("category"):
                    old_categories[description] = row["category"].strip()

            except Exception as e:
                logger.warning(f"Error parsing row: {row}, error: {e}")
                continue

    return transactions, old_categories


def write_csv(transactions: list[CategorizedTransaction], output_path: Path) -> None:
    """Write categorized transactions to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "description", "amount", "category"])
        writer.writeheader()
        for tx in transactions:
            writer.writerow(tx.to_csv_row())


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Configure logging
    configure_logging(verbose=args.verbose, debug=args.debug)

    # Validate input
    if not args.input.exists():
        logger.error(f"Input file not found: {args.input}")
        return 1

    if args.input.suffix.lower() != ".csv":
        logger.error(f"Input must be a CSV file: {args.input}")
        return 1

    # Set output path
    output_path = args.output
    if output_path is None:
        output_path = args.input.with_stem(args.input.stem + "_recategorized")

    # Load categories
    categories = load_categories(args.categories, required=True)
    if categories is None:
        logger.error("Categories file required for processing")
        return 1
    logger.info(f"Loaded {len(categories.categories)} categories")

    # Load transactions from CSV
    try:
        transactions, old_categories = load_csv_transactions(args.input)
    except ValueError as e:
        logger.error(f"Failed to load CSV: {e}")
        return 1

    if not transactions:
        logger.error("No valid transactions found in CSV")
        return 1

    logger.info(f"Loaded {len(transactions)} transactions from {args.input}")

    # Parse Ollama host
    ollama_parts = args.ollama_host.split(":")
    ollama_host = ollama_parts[0]
    ollama_port = int(ollama_parts[1]) if len(ollama_parts) > 1 else 11434

    # Set up debug artifacts
    debug_artifacts = None
    if args.debug:
        debug_dir = output_path.parent / "debug"
        debug_artifacts = DebugArtifacts(debug_dir)

    # Run categorization
    try:
        with OllamaClient(
            host=ollama_host,
            port=ollama_port,
            model=args.ollama_model,
            timeout=600.0,
        ) as client:
            categorizer = Categorizer(
                categories=categories,
                ollama_client=client,
                debug_artifacts=debug_artifacts,
            )

            categorized = categorizer.categorize(transactions)

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130
    except Exception as e:
        logger.exception(f"Categorization failed: {e}")
        return 1

    # Track changes
    changes: list[tuple[str, str, str]] = []  # (description, old, new)
    for tx in categorized:
        old_cat = old_categories.get(tx.description, "")
        if old_cat and old_cat != tx.category:
            changes.append((tx.description, old_cat, tx.category))

    # Show changes
    if args.show_changes or args.dry_run:
        if changes:
            print(f"\n{'='*60}")
            print(f"Category changes: {len(changes)} of {len(categorized)} transactions")
            print(f"{'='*60}")
            for desc, old, new in changes:
                print(f"  {desc[:50]:<50}")
                print(f"    {old} → {new}")
        else:
            print("\nNo category changes detected.")

    # Dry run - don't write output
    if args.dry_run:
        print(f"\n[DRY RUN] Would write {len(categorized)} transactions to {output_path}")
        return 0

    # Write output
    write_csv(categorized, output_path)
    print(f"\nOutput written to: {output_path}")
    print(f"  Total transactions: {len(categorized)}")
    print(f"  Category changes: {len(changes)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
