"""Budget sheet uploader for Google Sheets."""

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from src.clients.gsheets import GSheetsClient, GSheetsError

# Default config file path
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "google_sheet_config.json"


@dataclass
class CellUpdate:
    """Represents an update to a cell."""

    category: str
    cell: str
    old_value: float
    new_value: float
    amount_added: float


@dataclass
class UploadResult:
    """Result of a budget upload operation."""

    updates: list[CellUpdate]
    skipped_categories: list[str]
    unmapped_categories: list[str]


def parse_currency(value: str | None) -> float:
    """Parse a currency string to float.

    Handles formats like: "$ 1,234.56", "$1234.56", "1234.56", "", None

    Args:
        value: Currency string to parse

    Returns:
        Float value (0.0 for empty/None)
    """
    if not value:
        return 0.0

    # Remove currency symbols, commas, and whitespace
    cleaned = re.sub(r"[$,\s]", "", value.strip())

    if not cleaned:
        return 0.0

    try:
        return float(cleaned)
    except ValueError:
        logger.warning(f"Could not parse currency value: {value!r}")
        return 0.0


class SheetConfig:
    """Manages Google Sheets configuration and cell mappings."""

    def __init__(self, config_path: Path | None = None):
        """Load config from JSON file.

        Args:
            config_path: Path to config JSON file
        """
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        """Load config from file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path) as f:
            self._data = json.load(f)

        logger.debug(f"Loaded {len(self.mappings)} category mappings from {self.config_path}")

    @property
    def spreadsheet_id(self) -> str | None:
        """Get the spreadsheet ID."""
        return self._data.get("spreadsheet_id")

    @property
    def template_sheet(self) -> str | None:
        """Get the template sheet name to duplicate."""
        return self._data.get("template_sheet")

    @property
    def target_sheet(self) -> str | None:
        """Get the target sheet name."""
        return self._data.get("target_sheet")

    @property
    def mappings(self) -> dict[str, str]:
        """Get the category -> cell mappings."""
        return self._data.get("mappings", {})

    @property
    def unmapped_categories(self) -> list[str]:
        """Get list of intentionally unmapped categories."""
        return self._data.get("unmapped_categories", [])

    @property
    def shallow_copy_cells(self) -> list[str]:
        """Get list of cells to convert from formulas to values when duplicating.

        These cells will have their formulas replaced with their calculated values
        after the template is duplicated. Useful for income category cells that
        won't be populated by expense parsing.
        """
        return self._data.get("shallow_copy_cells", [])

    def get_cell(self, category: str) -> str | None:
        """Get the cell address for a category.

        Args:
            category: Category name

        Returns:
            Cell address (e.g., "M4") or None if not mapped
        """
        return self.mappings.get(category)


# Backwards compatibility alias
CellMapping = SheetConfig


class BudgetUploader:
    """Uploads category totals to a Google Sheets budget."""

    def __init__(
        self,
        gsheets_client: GSheetsClient,
        mapping: CellMapping,
    ):
        """Initialize the uploader.

        Args:
            gsheets_client: Google Sheets client
            mapping: Cell mapping configuration
        """
        self.client = gsheets_client
        self.mapping = mapping

    def load_summary(
        self,
        summary_path: Path,
        year: int | None = None,
        month: int | None = None,
    ) -> dict[str, float]:
        """Load category totals from a summary CSV.

        Args:
            summary_path: Path to summary CSV with 'year', 'month', 'category', 'total' columns
            year: Optional year to filter by
            month: Optional month to filter by

        Returns:
            Dict mapping category name to total amount
        """
        if not summary_path.exists():
            raise FileNotFoundError(f"Summary file not found: {summary_path}")

        totals: dict[str, float] = {}

        with open(summary_path, newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []

            if "category" not in fieldnames:
                raise ValueError("Summary CSV must have a 'category' column")
            if "total" not in fieldnames:
                raise ValueError("Summary CSV must have a 'total' column")

            has_year_month = "year" in fieldnames and "month" in fieldnames

            for row in reader:
                # Filter by year/month if specified and columns exist
                if has_year_month and year is not None:
                    row_year = int(row["year"])
                    if row_year != year:
                        continue
                if has_year_month and month is not None:
                    row_month = int(row["month"])
                    if row_month != month:
                        continue

                category = row["category"]
                try:
                    total = float(row["total"].replace("$", "").replace(",", ""))
                    # Aggregate if same category appears multiple times
                    totals[category] = totals.get(category, 0) + total
                except ValueError:
                    logger.warning(f"Skipping invalid total for {category}: {row['total']}")

        filter_desc = ""
        if year is not None or month is not None:
            filter_desc = f" (filtered: year={year}, month={month})"
        logger.info(f"Loaded {len(totals)} category totals from {summary_path}{filter_desc}")
        return totals

    def upload(
        self,
        summary_path: Path,
        spreadsheet_id: str,
        target_sheet: str,
        year: int | None = None,
        month: int | None = None,
        dry_run: bool = False,
    ) -> UploadResult:
        """Upload category totals to the budget sheet.

        Creates a new sheet from the template if it doesn't exist.

        Args:
            summary_path: Path to summary CSV
            spreadsheet_id: Google Sheets spreadsheet ID
            target_sheet: Name for the new/target worksheet
            year: Optional year to filter summary by
            month: Optional month to filter summary by
            dry_run: If True, don't actually update the sheet

        Returns:
            UploadResult with details of what was updated
        """
        # Load summary
        totals = self.load_summary(summary_path, year=year, month=month)

        # Create worksheet from template (or use existing)
        template = self.mapping.template_sheet
        if not template:
            raise ValueError("No template_sheet specified in config")

        if dry_run:
            logger.info(f"[DRY RUN] Would create sheet '{target_sheet}' from template '{template}'")
            # For dry run, just get the template to read current values
            worksheet = self.client.get_worksheet(spreadsheet_id, template)
        else:
            worksheet = self.client.duplicate_sheet(spreadsheet_id, template, target_sheet)

            # Convert formula cells to values if specified
            shallow_cells = self.mapping.shallow_copy_cells
            if shallow_cells:
                logger.info(f"Converting {len(shallow_cells)} cells from formulas to values...")
                current_values = self.client.read_cells(worksheet, shallow_cells)
                # Write back as values (this replaces any formulas)
                values_to_write = {
                    cell: parse_currency(value) for cell, value in current_values.items()
                }
                self.client.write_cells(worksheet, values_to_write)
                logger.debug(f"Shallow copied cells: {shallow_cells}")

        # Determine which cells we need to update (include zeros to overwrite formulas)
        cells_to_update: dict[str, tuple[str, float]] = {}  # cell -> (category, amount)
        unmapped: list[str] = []

        for category, amount in totals.items():
            cell = self.mapping.get_cell(category)
            if cell is None:
                if category in self.mapping.unmapped_categories:
                    unmapped.append(category)
                else:
                    logger.warning(f"No mapping for category: {category}")
                    unmapped.append(category)
                continue

            cells_to_update[cell] = (category, amount)

        if not cells_to_update:
            logger.info("No cells to update")
            return UploadResult(updates=[], skipped_categories=[], unmapped_categories=unmapped)

        # Read current values
        logger.info(f"Reading {len(cells_to_update)} cells from sheet...")
        current_values = self.client.read_cells(worksheet, list(cells_to_update.keys()))

        # Calculate updates
        updates: list[CellUpdate] = []
        new_values: dict[str, float] = {}

        for cell, (category, amount) in cells_to_update.items():
            old_value = parse_currency(current_values.get(cell))
            new_value = old_value + amount

            updates.append(CellUpdate(
                category=category,
                cell=cell,
                old_value=old_value,
                new_value=new_value,
                amount_added=amount,
            ))
            new_values[cell] = new_value

        # Apply updates
        if dry_run:
            logger.info(f"[DRY RUN] Would update {len(updates)} cells")
        else:
            logger.info(f"Updating {len(updates)} cells...")
            self.client.write_cells(worksheet, new_values)
            logger.info(f"Successfully updated {len(updates)} cells")

        return UploadResult(
            updates=updates,
            skipped_categories=[],
            unmapped_categories=unmapped,
        )
