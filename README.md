# Transactions Categorizer

Parse credit card statement PDFs and categorize transactions using local LLMs.

## Prerequisites

- Python 3.13+
- [Ollama](https://ollama.ai) running locally

```bash
# macOS
ollama pull mistral
```

## Installation

```bash
uv sync
```

## Usage

There are four CLI scripts:
- `categorize` - Process PDFs and categorize transactions
- `recategorize` - Re-categorize existing CSV (skip expensive PDF parsing)
- `summarize` - Generate summary from transactions CSV
- `upload_budget` - Upload summary to Google Sheets budget

### Categorize Transactions

```bash
# Basic usage
python -m src.cli.categorize statement.pdf -o output.csv

# Multiple files
python -m src.cli.categorize statements/*.pdf -o all_transactions.csv

# Custom categories
python -m src.cli.categorize statement.pdf -c my_categories.json -o output.csv

# Verbose output (shows progress)
python -m src.cli.categorize statement.pdf -o output.csv -v

# Debug mode (saves intermediate files)
python -m src.cli.categorize statement.pdf -o output.csv --debug

# Parse only, skip categorization
python -m src.cli.categorize statement.pdf -o output.csv --dry-run

# Also generate summary CSV with category totals
python -m src.cli.categorize statement.pdf -o output.csv --summary

# Use different Ollama model
python -m src.cli.categorize statement.pdf -o output.csv --ollama-model llama3
```

### Re-categorize Existing CSV

Re-run categorization on an existing CSV file without re-parsing PDFs. This is useful when you've updated category keywords or added new categories and want to apply those changes to previously parsed transactions.

```bash
# Re-categorize with updated categories
python -m src.cli.recategorize transactions.csv -o recategorized.csv

# Use custom categories file
python -m src.cli.recategorize transactions.csv -o recategorized.csv -c categories/budget_sheet_categories.json

# Preview what would change without writing output
python -m src.cli.recategorize transactions.csv --dry-run --show-changes

# Show category changes in output
python -m src.cli.recategorize transactions.csv -o recategorized.csv --show-changes -v
```

### Generate Summary from CSV

Generate a summary from an existing transactions CSV. This allows you to review and correct the categorized transactions before generating the final summary.

```bash
# Generate summary (sorted by total descending)
python -m src.cli.summarize transactions.csv -o summary.csv

# Include all categories with zeros for unused ones
python -m src.cli.summarize transactions.csv -o summary.csv -c categories/budget_sheet_categories.json
```

### Upload to Google Sheets Budget

Upload category totals to a Google Sheets budget spreadsheet. Requires one-time setup of Google Cloud credentials (see [BUDGET_SHEETS_DESIGN.md](BUDGET_SHEETS_DESIGN.md) for setup instructions).

```bash
# Upload summary to budget sheet
python -m src.cli.upload_budget summary.csv --sheet-id YOUR_SPREADSHEET_ID

# Preview changes without updating
python -m src.cli.upload_budget summary.csv --sheet-id YOUR_SPREADSHEET_ID --dry-run

# Verbose output
python -m src.cli.upload_budget summary.csv --sheet-id YOUR_SPREADSHEET_ID -v
```

The spreadsheet ID is found in the Google Sheets URL: `https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`

## Output Format

CSV with columns: `date`, `description`, `amount`, `category`

```csv
date,description,amount,category
2024-01-15,AMAZON.COM,45.99,Shopping
2024-01-16,TRADER JOES #123,87.32,Groceries
2024-01-18,UBER TRIP,23.50,Transportation
```

### Summary Output (--summary)

When using `--summary`, an additional `<output>_summary.csv` is generated with category totals:

```csv
category,total
Groceries,87.32
Shopping,45.99
Transportation,23.50
Dining Out,0.00
```

All categories from your config are included, with `0.00` for unused categories.

## Custom Categories

Create a JSON file with your categories:

```json
{
  "categories": [
    {
      "name": "Groceries",
      "description": "Food and grocery stores",
      "keywords": ["SAFEWAY", "TRADER JOE", "WHOLE FOODS"]
    }
  ]
}
```

The `keywords` field helps the LLM with common merchant names but isn't required.

## How It Works

1. **PDF Text Extraction**: pdfplumber extracts text with proper line alignment
2. **Parsing**: Ollama (mistral) structures the text into transactions
3. **Categorization**: Ollama classifies each transaction into categories
4. **Output**: Results written to CSV with category breakdown

All processing runs locally - no data is sent to external services.

## Architecture

- [CATEGORIZATION_DESIGN.md](CATEGORIZATION_DESIGN.md) - PDF parsing and categorization pipeline
- [BUDGET_SHEETS_DESIGN.md](BUDGET_SHEETS_DESIGN.md) - Google Sheets budget integration

## Development

```bash
uv sync --group dev
ruff check src/
pytest
```
