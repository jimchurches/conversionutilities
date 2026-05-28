# Schwab to Sharesight Cash Account CSV Converter

Convert Charles Schwab transaction CSV exports into Sharesight bulk cash import CSV files.

## What it does

- Reads a Schwab CSV export (`Date, Action, Symbol, Description, Quantity, Price, Fees & Comm, Amount`)
- Converts US dates to `d/m/yyyy` (handles `MM/DD/YYYY as of MM/DD/YYYY` rows)
- Maps Schwab actions to your Sharesight description notation
- Splits amounts into **Deposit amount** and **Withdrawal amount** columns
- Looks up exchange suffixes from `config.yaml` (does not guess exchanges)
- Writes an exceptions report for rows that cannot be converted confidently

## Install

```bash
cd schwab_to_sharesight
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python convert.py input/schwab.csv output/sharesight_import.csv --config config.yaml
```

Exceptions are written to `output/exceptions.csv` by default. If any exceptions exist, the import file is **not** written until they are resolved.

## Output format

Sharesight bulk cash import (separate deposit/withdrawal columns):

```csv
Date,Deposit amount,Withdrawal amount,Description
27/5/2026,1319.87,,SELL 10 x LULU.NASDAQ shares
14/5/2026,,18.63,Foreign Tax (United States NRA Withholding)
14/5/2026,124.20,,Income: APPL.NASDAQ qualified dividend
29/4/2026,,436.11,Margin interest
```

## Config

`config.yaml` contains:

1. **securities** — Schwab ticker to Sharesight code (e.g. `AAPL` → `APPL.NASDAQ`)
2. **transaction_templates** — description patterns per Schwab `Action`
3. **output** — date format and column names

Add tickers as they appear in exceptions. Use `unit_label: units` for ETFs.

Example:

```yaml
securities:
  AAPL:
    sharesight_code: APPL.NASDAQ
    unit_label: shares
  ARKK:
    sharesight_code: ARKK.NYSEARCA
    unit_label: units

transaction_templates:
  Sell: "SELL {quantity:g} x {sharesight_code} {unit_label}"
  Buy: "BUY {quantity:g} x {sharesight_code} {unit_label}{assignment_note}"
  Qualified Dividend: "Income: {sharesight_code} qualified dividend"
  NRA Tax Adj: "Foreign Tax (United States NRA Withholding)"
```

The bundled `config.yaml` includes mappings derived from your existing Sharesight ledgers.

## Supported Schwab actions

Buy, Sell, dividends, NRA/foreign tax, margin/credit interest, ADR fees, return of capital, and basic option premium rows (STO/BTC/STC/BTO/Expired). Assigned option rows are merged into the matching Buy line.

Corporate actions (mergers, reverse splits, wire fees, etc.) are reported as exceptions for now.

Stock splits use ratios from `stock_splits` in config:

```yaml
stock_splits:
  NOW:
    ratio: "1:5"
  NFLX:
    ratio: "1:10"
```

Withholding corrections (`Adjustment` rows) map to your `Adjustment: NRA withholding` / `Adjustment: Reverse NRA withholding` notation.

## Test fixtures

Committed fixtures use generic filenames and contain no account identifiers:

- `tests/fixtures/schwab_trust_catchup_sample.csv` — anonymized Schwab export (125 rows, Sep 2025–May 2026)
- `tests/fixtures/sharesight_trust_catchup_expected.csv` — matching Sharesight import

Real Schwab export filenames (often containing account numbers) are gitignored. Keep working copies in `local/`:

```bash
python convert.py local/my_export.csv output/import.csv
pytest
```

## Tests

```bash
pytest
```

## Example

```bash
python convert.py input/sample_schwab.csv output/sharesight_import.csv
pytest
```
