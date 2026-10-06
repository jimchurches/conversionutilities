# CommBank to Sharesight Cash Account CSV Converter

Convert Commonwealth Bank transaction CSV exports into Sharesight bulk cash import CSV files.

## What it does

- Reads a CommBank CSV export (no header row: `Date, Amount, Description, Balance`)
- Converts Australian dates to `d/m/yyyy`
- Maps bank descriptions to your Sharesight cash account notation
- Splits amounts into **Deposit amount** and **Withdrawal amount** columns
- Maps dividend payers to ASX tickers via `config.yaml`
- Writes an exceptions report for rows that cannot be converted confidently

## Setup (once)

```bash
cd commbank_to_sharesight
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## How to use

**1. Activate the virtual environment:**

```bash
cd commbank_to_sharesight
source .venv/bin/activate
```

**2. Run the converter** on your CommBank export:

```bash
python convert.py ~/Downloads/commbank-output.csv output/sharesight_import.csv --config config.yaml
```

**Optional:** copy your CommBank export into `local/` before converting (that folder is gitignored):

```bash
cp ~/Downloads/commbank-output.csv local/
python convert.py local/commbank-output.csv output/sharesight_import.csv --config config.yaml
```

**3. Check the result**

- If conversion succeeds, upload `output/sharesight_import.csv` to Sharesight.
- If there are problems, the tool prints a summary and writes `output/exceptions.csv`. Fix missing mappings in `config.yaml`, then run again. The import file is **not** written until all rows convert successfully.

**4. Run tests:**

```bash
pytest
```

## CommBank CSV format

CommBank NetBank exports have **no header row**:

```csv
02/07/2026,"+478.80","Direct Credit 077669 MQG FNL DIV 001357099821","+20829.74"
01/07/2026,"+31.72","Credit Interest","+20350.94"
01/06/2026,"-3315.97","Direct Debit 062934 COMMSEC SECURITI COMMSEC","+20308.99"
```

Amounts use `+` / `-` prefixes. The balance column is ignored.

## Output format

Sharesight bulk cash import (separate deposit/withdrawal columns):

```csv
Date,Deposit amount,Withdrawal amount,Description
2/7/2026,478.80,,Income: MQG.ASX final dividend
1/7/2026,31.72,,Income: Interest payment
1/6/2026,,3315.97,Transfer to CommSec
```

## Config

`config.yaml` contains:

1. **securities** — text patterns in bank descriptions mapped to Sharesight codes (e.g. `MQG` → `MQG.ASX`)
2. **description_rules** — regex patterns for non-dividend transactions (CommSec transfers, tax, fees, etc.)
3. **transaction_templates** — description patterns for dividends and interest
4. **smsf** — placeholders for super contribution descriptions

Example:

```yaml
securities:
  MQG:
    sharesight_code: MQG.ASX
    patterns: ["MQG"]

description_rules:
  - match: "^Direct Debit .* COMMSEC SECURITI COMMSEC$"
    template: "Transfer to CommSec"
  - match: "^Direct Credit .* COMMONWEALTH SEC COMMSEC$"
    template: "Transfer from CommSec"

transaction_templates:
  Credit Interest: "Income: Interest payment"
  Dividend: "Income: {sharesight_code} dividend"
  Final Dividend: "Income: {sharesight_code} final dividend"
```

## Supported transaction types

- Credit interest
- ASX dividends (interim, final, scheme payments)
- CommSec transfers in/out
- ASIC fees
- ATO tax payments and refunds
- SMSF audit and accounting fees
- Employer super contributions (SuperChoice, ClickSuper)
- Transfers to Charles Schwab, SUPERCentral
- Hub24 distributions

Rows that do not match a known pattern are reported as exceptions for manual review.

## Privacy

Real CommBank exports and generated output stay in `local/` and `output/` (gitignored). Only anonymised fixtures are committed for tests.
