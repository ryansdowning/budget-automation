# Transactions Categorizer - Design Document

## Overview

A Python CLI tool that parses credit card statement PDFs and categorizes transactions using locally-run LLMs (via Ollama) for complete data privacy.

```
INPUT: PDF file(s) + categories.json
OUTPUT: CSV with columns [date, description, amount, category]
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLI Entry Point                                 │
│                            (src/cli.py)                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Pipeline Orchestrator                           │
│                            (src/pipeline.py)                                │
│  - Coordinates parsing → categorization flow                                │
│  - Handles batch processing of multiple PDFs                                │
│  - Writes CSV output with summary                                           │
└─────────────────────────────────────────────────────────────────────────────┘
                          │                       │
                          ▼                       ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────────┐
│       OllamaParser               │  │          Categorizer                  │
│   (src/parser/ollama.py)         │  │      (src/categorizer.py)            │
│                                  │  │                                      │
│  - Converts PDF → images         │  │  - Batches transactions (15/batch)   │
│  - Extracts via Ollama vision    │  │  - Falls back to single if batch     │
│  - Deduplicates transactions     │  │    fails                             │
└──────────────────────────────────┘  └──────────────────────────────────────┘
              │                                     │
              └────────────────┬────────────────────┘
                               ▼
              ┌──────────────────────────────────────┐
              │         OllamaClient                 │
              │     (src/clients/ollama.py)          │
              │                                      │
              │  - HTTP client for Ollama API        │
              │  - Vision + text generation          │
              │  - JSON mode for structured output   │
              │  - Connection/model health checks    │
              └──────────────────────────────────────┘
```

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| LLM Runtime | Ollama | Single runtime for both parsing and categorization |
| OCR | Tesseract (pytesseract) | Reliable text extraction from PDF images |
| Text Parsing | mistral | Structures OCR text into JSON transactions |
| Categorization | mistral | Fast, accurate text categorization |
| Logging | Loguru | Simpler API than structlog, good defaults |
| PDF → Image | pdf2image + poppler | Reliable, handles complex PDFs |
| Deduplication | By (date, description, amount) | Handles multi-page overlaps |
| Batch size | 15 transactions | Balance between speed and reliability |
| Error handling | Fallback to "Other" | Never fails, logs warnings |

## Data Flow

```
PDF File
    │
    ▼ (pdf2image @ 200 DPI)
Page Images
    │
    ▼ (pytesseract OCR)
Raw Text
    │
    ▼ (OllamaClient.generate → JSON)
Raw JSON [{date, description, amount}, ...]
    │
    ▼ (OllamaParser._parse_transaction)
list[RawTransaction]  (deduplicated)
    │
    ▼ (Categorizer.categorize, batched)
list[CategorizedTransaction]
    │
    ▼ (Pipeline.write_csv)
output.csv
```

## Observability

### Log Levels

| Flag | Level | Output |
|------|-------|--------|
| (none) | WARNING | Errors and warnings only |
| `-v` | INFO | Progress, file counts, summaries |
| `--debug` | DEBUG | LLM requests/responses, timing |

### Debug Artifacts

When `--debug` is enabled, saves to `{output_dir}/debug/`:
- `{filename}_page_{n}_input.png` - Page images for OCR
- `{filename}_page_{n}_ocr.txt` - Raw OCR text per page
- `{filename}_full_ocr.txt` - Combined OCR text
- `{filename}_llm_response.txt` - LLM parsing response
- `{filename}_transactions.json` - Parsed transactions
- `categorize_batch_{n}_request.json` - LLM prompts
- `categorize_batch_{n}_response.json` - LLM responses

## Categories

18 built-in categories in `categories/default.json`:
- Groceries, Restaurants, Transportation, Utilities
- Entertainment, Shopping, Health, Travel
- Subscriptions, Insurance, Education, Personal Care
- Home, Pets, Gifts & Donations, Fees & Charges
- Income, Other

Custom categories via `-c path/to/categories.json`.

## Dependencies

```toml
dependencies = [
    "pydantic==2.10.6",
    "loguru==0.7.3",
    "httpx==0.28.1",
    "pdf2image==1.17.0",
    "pillow==11.1.0",
    "pytesseract==0.3.13",
]
```

**System requirements:**
- `poppler` for PDF conversion: `brew install poppler`
- `tesseract` for OCR: `brew install tesseract`
- Ollama with model pulled: `ollama pull mistral`

## Future Extensibility

- **Multiple providers**: Subclass `BaseParser` for Chase, Amex, etc.
- **Alternative models**: Use `--vision-model` flag or `--ollama-model`
- **Web UI**: Wrap Pipeline in FastAPI
- **Fine-tuning**: Collect corrections to improve categorization
