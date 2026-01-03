"""Google Sheets API client using gspread."""

from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

# Default credentials path
DEFAULT_CREDENTIALS_PATH = Path.home() / ".config" / "budget-automation" / "credentials.json"

# Required OAuth scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


class GSheetsError(Exception):
    """Error communicating with Google Sheets."""

    pass


class GSheetsClient:
    """Client for Google Sheets API."""

    def __init__(
        self,
        credentials_path: Path | None = None,
    ):
        """Initialize the client.

        Args:
            credentials_path: Path to service account JSON credentials
        """
        self.credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH
        self._client: gspread.Client | None = None

    def _ensure_client(self) -> gspread.Client:
        """Get or create the gspread client."""
        if self._client is None:
            if not self.credentials_path.exists():
                raise GSheetsError(
                    f"Credentials file not found: {self.credentials_path}\n\n"
                    "To set up Google Sheets access:\n"
                    "1. Create a Google Cloud project and enable the Sheets API\n"
                    "2. Create a service account and download the JSON key\n"
                    "3. Save it to: {self.credentials_path}\n"
                    "4. Share your spreadsheet with the service account email"
                )

            try:
                creds = Credentials.from_service_account_file(
                    str(self.credentials_path),
                    scopes=SCOPES,
                )
                self._client = gspread.authorize(creds)
                logger.debug("Authenticated with Google Sheets API")
            except Exception as e:
                raise GSheetsError(f"Failed to authenticate: {e}") from e

        return self._client

    def open_spreadsheet(self, spreadsheet_id: str) -> gspread.Spreadsheet:
        """Open a spreadsheet by ID.

        Args:
            spreadsheet_id: The spreadsheet ID from the URL

        Returns:
            gspread Spreadsheet object
        """
        client = self._ensure_client()
        try:
            spreadsheet = client.open_by_key(spreadsheet_id)
            logger.debug(f"Opened spreadsheet: {spreadsheet.title}")
            return spreadsheet
        except gspread.SpreadsheetNotFound:
            raise GSheetsError(
                f"Spreadsheet not found: {spreadsheet_id}\n\n"
                "Make sure you've shared the spreadsheet with the service account email."
            )
        except gspread.APIError as e:
            raise GSheetsError(f"API error opening spreadsheet: {e}") from e

    def get_worksheet(
        self,
        spreadsheet_id: str,
        sheet_name: str | None = None,
    ) -> gspread.Worksheet:
        """Get a worksheet from a spreadsheet.

        Args:
            spreadsheet_id: The spreadsheet ID
            sheet_name: Worksheet name (uses first sheet if None)

        Returns:
            gspread Worksheet object
        """
        spreadsheet = self.open_spreadsheet(spreadsheet_id)

        try:
            if sheet_name:
                worksheet = spreadsheet.worksheet(sheet_name)
            else:
                worksheet = spreadsheet.sheet1
            logger.debug(f"Using worksheet: {worksheet.title}")
            return worksheet
        except gspread.WorksheetNotFound:
            available = [ws.title for ws in spreadsheet.worksheets()]
            raise GSheetsError(
                f"Worksheet '{sheet_name}' not found.\n"
                f"Available worksheets: {', '.join(available)}"
            )

    def read_cell(self, worksheet: gspread.Worksheet, cell: str) -> str | None:
        """Read a single cell value.

        Args:
            worksheet: The worksheet to read from
            cell: Cell address (e.g., "A1", "M4")

        Returns:
            Cell value as string, or None if empty
        """
        try:
            value = worksheet.acell(cell).value
            return value
        except gspread.APIError as e:
            raise GSheetsError(f"Failed to read cell {cell}: {e}") from e

    def read_cells(
        self,
        worksheet: gspread.Worksheet,
        cells: list[str],
    ) -> dict[str, str | None]:
        """Read multiple cell values in a batch.

        Args:
            worksheet: The worksheet to read from
            cells: List of cell addresses

        Returns:
            Dict mapping cell address to value
        """
        if not cells:
            return {}

        try:
            # Use batch_get for efficiency
            ranges = cells
            results = worksheet.batch_get(ranges)

            values = {}
            for cell, result in zip(cells, results):
                if result and result[0]:
                    values[cell] = result[0][0]
                else:
                    values[cell] = None

            return values
        except gspread.APIError as e:
            raise GSheetsError(f"Failed to read cells: {e}") from e

    def write_cell(
        self,
        worksheet: gspread.Worksheet,
        cell: str,
        value: float | str,
    ) -> None:
        """Write a single cell value.

        Args:
            worksheet: The worksheet to write to
            cell: Cell address (e.g., "A1", "M4")
            value: Value to write
        """
        try:
            worksheet.update_acell(cell, value)
            logger.debug(f"Updated {cell} = {value}")
        except gspread.APIError as e:
            raise GSheetsError(f"Failed to write cell {cell}: {e}") from e

    def write_cells(
        self,
        worksheet: gspread.Worksheet,
        updates: dict[str, float | str],
    ) -> None:
        """Write multiple cell values in a batch.

        Args:
            worksheet: The worksheet to write to
            updates: Dict mapping cell address to value
        """
        if not updates:
            return

        try:
            # Format for batch_update
            batch_data = [
                {"range": cell, "values": [[value]]}
                for cell, value in updates.items()
            ]
            worksheet.batch_update(batch_data)
            logger.debug(f"Batch updated {len(updates)} cells")
        except gspread.APIError as e:
            raise GSheetsError(f"Failed to batch update cells: {e}") from e

    def duplicate_sheet(
        self,
        spreadsheet_id: str,
        source_sheet_name: str,
        new_sheet_name: str,
    ) -> gspread.Worksheet:
        """Duplicate a worksheet with a new name.

        Args:
            spreadsheet_id: The spreadsheet ID
            source_sheet_name: Name of the sheet to duplicate
            new_sheet_name: Name for the new sheet

        Returns:
            The new worksheet
        """
        spreadsheet = self.open_spreadsheet(spreadsheet_id)

        # Get source worksheet
        try:
            source = spreadsheet.worksheet(source_sheet_name)
        except gspread.WorksheetNotFound:
            available = [ws.title for ws in spreadsheet.worksheets()]
            raise GSheetsError(
                f"Template sheet '{source_sheet_name}' not found.\n"
                f"Available worksheets: {', '.join(available)}"
            )

        # Check if target already exists
        try:
            existing = spreadsheet.worksheet(new_sheet_name)
            logger.info(f"Sheet '{new_sheet_name}' already exists, using it")
            return existing
        except gspread.WorksheetNotFound:
            pass  # Good, we'll create it

        # Duplicate the sheet
        try:
            new_sheet = source.duplicate(new_sheet_name=new_sheet_name)
            logger.info(f"Created new sheet '{new_sheet_name}' from template '{source_sheet_name}'")
            return new_sheet
        except gspread.APIError as e:
            raise GSheetsError(f"Failed to duplicate sheet: {e}") from e

    def check_connection(self, spreadsheet_id: str) -> bool:
        """Check if we can access the spreadsheet.

        Args:
            spreadsheet_id: The spreadsheet ID to check

        Returns:
            True if accessible, False otherwise
        """
        try:
            self.open_spreadsheet(spreadsheet_id)
            return True
        except GSheetsError:
            return False
