# Budget Sheets Integration - Design Document

## Overview

This feature uploads categorized transaction summaries to a Google Sheets budget spreadsheet, automatically populating the "Actual" column for each category.

```
INPUT: summary.csv (category totals) + cell_mapping.json
OUTPUT: Updated Google Sheet with "Actual" values populated
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLI Entry Point                                 │
│                       (src/cli/upload_budget.py)                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Budget Uploader                                   │
│                        (src/sheets/uploader.py)                             │
│  - Loads summary CSV                                                        │
│  - Loads cell mapping config                                                │
│  - Reads existing values, adds new amounts                                  │
│  - Batch updates cells                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Google Sheets Client                                │
│                       (src/clients/gsheets.py)                              │
│  - Authenticates via service account                                        │
│  - Reads/writes cell values                                                 │
│  - Handles batch updates for efficiency                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                            Google Sheets API
```

---

## Configuration

### Cell Mapping (`config/cell_mapping.json`)

Maps category names to spreadsheet cell addresses:

```json
{
  "description": "Maps category names to 'Actual' column cells",
  "sheet_name": "Budget",
  "mappings": {
    "Groceries": "M4",
    "Dining Out": "M6",
    "Fuel": "H21",
    ...
  },
  "unmapped_categories": ["Credit Card Payment", "Other"]
}
```

### Google Sheets Credentials

Service account credentials stored at `~/.config/budget-automation/credentials.json` (configurable via `--credentials` flag).

---

## CLI Interface

```bash
# Basic usage
python -m src.cli.upload_budget summary.csv --sheet-id <SPREADSHEET_ID>

# With custom mapping
python -m src.cli.upload_budget summary.csv \
  --sheet-id <SPREADSHEET_ID> \
  --mapping config/cell_mapping.json

# Dry-run (show what would be updated without making changes)
python -m src.cli.upload_budget summary.csv \
  --sheet-id <SPREADSHEET_ID> \
  --dry-run

# Verbose output
python -m src.cli.upload_budget summary.csv \
  --sheet-id <SPREADSHEET_ID> \
  -v
```

**Arguments:**

| Arg | Type | Default | Description |
|-----|------|---------|-------------|
| `input` | positional | required | Summary CSV file |
| `--sheet-id` | string | required | Google Sheets spreadsheet ID |
| `--sheet-name` | string | from mapping | Worksheet name (tab) |
| `--mapping` | path | `config/cell_mapping.json` | Cell mapping config |
| `--credentials` | path | `~/.config/.../credentials.json` | Service account JSON |
| `--dry-run` | flag | false | Preview changes without applying |
| `-v, --verbose` | flag | false | Show detailed output |

---

## Implementation Details

### Update Logic

For each category in the summary:

1. Look up cell address from mapping
2. Read current cell value (may be empty, number, or formula result)
3. Parse as float (treat empty/error as 0)
4. Add summary amount to existing value
5. Write new value back to cell

```python
def update_cell(sheet, cell: str, amount: float) -> float:
    """Add amount to existing cell value."""
    current = sheet.acell(cell).value
    existing = parse_currency(current) if current else 0.0
    new_value = existing + amount
    sheet.update_acell(cell, new_value)
    return new_value
```

### Batch Updates

To minimize API calls, use `gspread`'s batch update:

```python
# Collect all updates
updates = []
for category, amount in summary.items():
    cell = mapping.get(category)
    if cell:
        current = existing_values.get(cell, 0)
        updates.append({'range': cell, 'values': [[current + amount]]})

# Single batch call
sheet.batch_update(updates)
```

### Currency Parsing

Handle various formats from the sheet:

```python
def parse_currency(value: str) -> float:
    """Parse currency string to float.

    Handles: "$ 1,234.56", "$1234.56", "1234.56", "", None
    """
    if not value:
        return 0.0
    cleaned = value.replace('$', '').replace(',', '').replace(' ', '').strip()
    return float(cleaned) if cleaned else 0.0
```

---

## File Structure

```
src/
├── cli/
│   ├── upload_budget.py    # CLI for budget upload
│   └── ...
├── clients/
│   ├── gsheets.py          # Google Sheets API client
│   └── ...
└── sheets/
    └── uploader.py         # Budget upload logic

config/
└── cell_mapping.json       # Category → cell mapping
```

---

## Dependencies

Add to `pyproject.toml`:

```toml
dependencies = [
    ...
    "gspread>=6.0",
    "google-auth>=2.0",
]
```

---

## Google Cloud Setup

### 1. Create Project & Enable API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable the **Google Sheets API**:
   - Navigate to "APIs & Services" → "Library"
   - Search for "Google Sheets API" → Enable

### 2. Create Service Account

1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "Service Account"
3. Name it (e.g., `budget-uploader`)
4. Skip optional steps, click "Done"
5. Click on the service account → "Keys" tab
6. "Add Key" → "Create new key" → JSON
7. Save the downloaded JSON file

### 3. Share Sheet with Service Account

1. Open the JSON file, find the `client_email` field
2. Open your Google Sheet
3. Click "Share" → paste the service account email
4. Give "Editor" access

### 4. Configure Credentials

```bash
# Create config directory
mkdir -p ~/.config/budget-automation

# Move credentials
mv ~/Downloads/your-credentials.json ~/.config/budget-automation/credentials.json
```

---

## Error Handling

| Error | Handling |
|-------|----------|
| Missing mapping for category | Log warning, skip category |
| Invalid cell value | Treat as 0, log warning |
| API rate limit | Retry with exponential backoff |
| Authentication failure | Clear error message with setup instructions |
| Sheet not found | Error with spreadsheet ID hint |

---

## Example Workflow

```bash
# 1. Process PDFs and categorize
python -m src.cli.categorize statements/*.pdf -o march_transactions.csv

# 2. Review and correct categories in CSV
# (manual step - edit march_transactions.csv)

# 3. Generate summary
python -m src.cli.summarize march_transactions.csv -o march_summary.csv \
  -c categories/budget_sheet_categories.json

# 4. Upload to Google Sheets
python -m src.cli.upload_budget march_summary.csv \
  --sheet-id 1ABC123... \
  -v

# Output:
# Updating Groceries (M4): $0.00 → $535.01
# Updating Dining Out (M6): $0.00 → $837.23
# Updating Fuel (H21): $0.00 → $61.91
# ...
# Updated 45 cells in Budget sheet
# Skipped 2 unmapped categories: Credit Card Payment, Other
```

---

## Future Enhancements

1. **Multiple months**: Support uploading to different columns for different months
2. **Validation**: Compare totals after upload to verify accuracy
3. **Undo**: Save previous values to allow rollback
4. **Templates**: Create new budget sheets from template
