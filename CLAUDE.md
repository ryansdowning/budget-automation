# Claude Code Guidelines

## Project Overview

Transaction categorizer using pdfplumber + Ollama for local processing:
- **pdfplumber**: Direct PDF text extraction with proper line alignment
- **Ollama (mistral)**: LLM for parsing and categorization

## Project Structure

```
src/
├── cli/
│   ├── __init__.py         # Shared utilities (load_categories)
│   ├── categorize.py       # CLI for PDF processing
│   └── summarize.py        # CLI for summary generation
├── pipeline.py             # Orchestrates parse → categorize → CSV
├── categorizer.py          # Batched LLM categorization
├── models.py               # Pydantic models (RawTransaction, etc.)
├── logging_config.py       # Loguru setup + DebugArtifacts
├── clients/
│   └── ollama.py           # Ollama HTTP client
├── parser/
│   ├── base.py             # Abstract BaseParser
│   └── pdfplumber_parser.py # PdfPlumberParser (pdfplumber + LLM)
└── prompts/
    ├── parse.py            # PDF extraction prompts
    └── categorize.py       # Categorization prompts
```

## Key Patterns

### Structured Output
LLM responses use JSON schema constraints for reliability:
- Parser uses `generate_structured()` with Pydantic models
- Categorizer uses dynamic schema with category enum constraint
- Schema ensures only valid category names can be returned

### Logging
Use loguru directly:
```python
from loguru import logger
logger.info(f"Processing {filename}")
logger.debug(f"Request payload: {payload}")
```

### Error Handling
- Client raises `OllamaError` for API/connection issues
- Categorizer falls back to "Other" on failures, never crashes
- Parser skips unparseable transactions with warnings

### Resource Management
All clients support context managers:
```python
with Pipeline(...) as pipeline:
    results = pipeline.process(pdf_paths)
```

### Debug Artifacts
Pass `DebugArtifacts(output_dir)` to save intermediate outputs:
```python
artifacts = DebugArtifacts(Path("debug/"))
artifacts.save_json("request", {"prompt": "..."})
artifacts.save_image("page_1", pil_image)
```

## Extending

### Add a New Parser
1. Create `src/parser/chase.py`
2. Subclass `BaseParser`
3. Implement `parse()` and `supported_formats()`
4. Register in `src/parser/__init__.py`

### Add Categories
Edit `categories/default.json` or pass `-c custom.json`

Available category files:
- `categories/default.json` - Basic categories
- `categories/budget_sheet_categories.json` - Detailed budget categories (50+)

### Change Models
Use `--ollama-model llama3` to use a different model for both parsing and categorization.

## Common Tasks

### Run with verbose logging
```bash
python -m src.cli.categorize statement.pdf -o out.csv -v
```

### Debug parsing issues
```bash
python -m src.cli.categorize statement.pdf -o out.csv --debug --dry-run
# Check debug/*.json and debug/*.txt
```

### Generate category summary (with PDF processing)
```bash
python -m src.cli.categorize statement.pdf -o out.csv --summary
# Creates out.csv and out_summary.csv with category totals
```

### Generate summary from existing CSV
Use this to review/correct transactions before generating the final summary:
```bash
# Generate summary sorted by total
python -m src.cli.summarize transactions.csv -o summary.csv

# Include all categories (fills zeros for unused)
python -m src.cli.summarize transactions.csv -o summary.csv -c categories/budget_sheet_categories.json
```

### Test Ollama connection
```python
from src.clients.ollama import OllamaClient
client = OllamaClient()
print(client.check_connection())  # True/False
print(client.check_model())       # True/False
```

## Dependencies

- **pydantic**: Data validation and models
- **loguru**: Logging
- **httpx**: HTTP client for Ollama API
- **pdfplumber**: PDF text extraction
- **pillow**: Image handling

## Testing Notes

- Mock `OllamaClient` for unit tests
- Use `--dry-run` to test parsing without categorization
- Debug artifacts help diagnose LLM prompt issues

## Gotchas

1. **Ollama must be running** - check with `ollama list`
2. **Batch categorization** may miss transactions if descriptions don't match exactly - falls back to individual
3. **Structured output requires Ollama 0.5+** for JSON schema support
4. **Multi-line transactions** (like flight itineraries) may not parse correctly
