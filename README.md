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

```bash
# Basic usage
python -m src.cli statement.pdf -o output.csv

# Multiple files
python -m src.cli statements/*.pdf -o all_transactions.csv

# Custom categories
python -m src.cli statement.pdf -c my_categories.json -o output.csv

# Verbose output (shows progress)
python -m src.cli statement.pdf -o output.csv -v

# Debug mode (saves intermediate files)
python -m src.cli statement.pdf -o output.csv --debug

# Parse only, skip categorization
python -m src.cli statement.pdf -o output.csv --dry-run

# Generate summary CSV with category totals
python -m src.cli statement.pdf -o output.csv --summary

# Use different Ollama model
python -m src.cli statement.pdf -o output.csv --ollama-model llama3
```

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

See [DESIGN.md](DESIGN.md) for detailed architecture documentation.

## Development

```bash
uv sync --group dev
ruff check src/
pytest
```
