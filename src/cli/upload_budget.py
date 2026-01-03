"""CLI for uploading category totals to Google Sheets budget."""

import argparse
import sys
from pathlib import Path

from loguru import logger

from src.clients.gsheets import GSheetsClient, GSheetsError
from src.logging_config import configure_logging
from src.sheets.uploader import BudgetUploader, SheetConfig

# Default paths
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "google_sheet_config.json"
DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "budget-automation" / "credentials.json"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Upload category totals to Google Sheets budget",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s summary.csv --year 2024 --month 12
  %(prog)s summary.csv --year 2024 --month 12 --dry-run
  %(prog)s summary.csv --target-sheet "January 2025" --year 2025 --month 1 -v

Setup:
  1. Create a Google Cloud project and enable the Sheets API
  2. Create a service account and download the JSON key
  3. Save credentials to: ~/.config/budget-automation/credentials.json
  4. Share your spreadsheet with the service account email
        """,
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Summary CSV file with 'category' and 'total' columns",
    )
    parser.add_argument(
        "--target-sheet",
        default=None,
        help="Name for the new budget sheet (default: from config)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Filter summary by year",
    )
    parser.add_argument(
        "--month",
        type=int,
        default=None,
        help="Filter summary by month (1-12)",
    )
    parser.add_argument(
        "--sheet-id",
        default=None,
        help="Google Sheets spreadsheet ID (default: from config)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Google Sheets config JSON file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_CREDENTIALS_PATH,
        help=f"Google service account credentials (default: {DEFAULT_CREDENTIALS_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without updating the sheet",
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
        logger.error(f"Summary file not found: {args.input}")
        return 1

    if args.input.suffix.lower() != ".csv":
        logger.error(f"Input must be a CSV file: {args.input}")
        return 1

    # Load config
    try:
        config = SheetConfig(args.config)
        logger.info(f"Loaded {len(config.mappings)} category mappings")
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    # Get spreadsheet ID (CLI arg overrides config)
    spreadsheet_id = args.sheet_id or config.spreadsheet_id
    if not spreadsheet_id:
        logger.error("No spreadsheet ID provided. Use --sheet-id or add 'spreadsheet_id' to config.")
        return 1

    # Get target sheet (CLI arg overrides config)
    target_sheet = args.target_sheet or config.target_sheet
    if not target_sheet:
        logger.error("No target sheet provided. Use --target-sheet or add 'target_sheet' to config.")
        return 1

    # Create clients
    try:
        gsheets = GSheetsClient(credentials_path=args.credentials)
        uploader = BudgetUploader(gsheets, config)
    except GSheetsError as e:
        logger.error(str(e))
        return 1

    # Upload
    try:
        result = uploader.upload(
            summary_path=args.input,
            spreadsheet_id=spreadsheet_id,
            target_sheet=target_sheet,
            year=args.year,
            month=args.month,
            dry_run=args.dry_run,
        )
    except GSheetsError as e:
        logger.error(f"Upload failed: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1

    # Print results
    prefix = "[DRY RUN] " if args.dry_run else ""

    if result.updates:
        print(f"\n{prefix}Updated {len(result.updates)} cells:")
        for update in result.updates:
            old_str = f"${update.old_value:,.2f}" if update.old_value else "$0.00"
            new_str = f"${update.new_value:,.2f}"
            print(f"  {update.category} ({update.cell}): {old_str} → {new_str}")
    else:
        print("\nNo cells updated (all categories were zero or unmapped)")

    if result.skipped_categories:
        print(f"\nSkipped {len(result.skipped_categories)} zero-value categories")
        if args.verbose:
            for cat in result.skipped_categories:
                print(f"  - {cat}")

    if result.unmapped_categories:
        print(f"\nUnmapped categories ({len(result.unmapped_categories)}):")
        for cat in result.unmapped_categories:
            print(f"  - {cat}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
