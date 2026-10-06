# Schwab to Sharesight Cash Account CSV Converter

Convert Charles Schwab transaction CSV exports into Sharesight bulk cash import CSV files.

## What it does

- Reads a Schwab CSV export (`Date, Action, Symbol, Description, Quantity, Price, Fees & Comm, Amount`)
- Converts US dates to `d/m/yyyy` (handles `MM/DD/YYYY as of MM/DD/YYYY` rows)
- Maps Schwab actions to your Sharesight description notation
- Splits amounts into **Deposit amount** and **Withdrawal amount** columns
- Looks up exchange suffixes from `config.yaml` (does not guess exchanges)
- Writes an exceptions report for rows that cannot be converted confidently

## Setup (once)

From the project directory:

```bash
cd schwab_to_sharesight
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On macOS/Linux, activate the virtual environment with `source .venv/bin/activate` each time you open a new terminal. Your prompt should show `(.venv)`.

## How to use

**1. Activate the virtual environment** (skip if already active):

```bash
cd schwab_to_sharesight
source .venv/bin/activate
```

**2. Run the converter** on your Schwab export:

```bash
python convert.py "/path/to/your/schwab_export.csv" output/sharesight_import.csv --config config.yaml
```

Example using a file from Downloads:

```bash
python convert.py ~/Downloads/schwab_export.csv output/sharesight_import.csv --config config.yaml
```

**3. Check the result**

- If conversion succeeds, upload `output/sharesight_import.csv` to Sharesight.
- If there are problems, the tool prints a summary and writes `output/exceptions.csv`. Fix missing tickers in `config.yaml` (or other issues listed), then run again. The import file is **not** written until all rows convert successfully.

**Optional:** copy your Schwab export into `local/` before converting (that folder is gitignored):

```bash
cp ~/Downloads/schwab_export.csv local/
python convert.py local/schwab_export.csv output/sharesight_import.csv --config config.yaml
```

**4. Run tests** (optional):

```bash
pytest
```

## Run (quick reference)

```bash
source .venv/bin/activate
python convert.py input/schwab.csv output/sharesight_import.csv --config config.yaml
```

Exceptions are written to `output/exceptions.csv` by default.

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
  Sell: "SELL {quantity:g} x {sharesight_code} {unit_label}{assignment_note}"
  Buy: "BUY {quantity:g} x {sharesight_code} {unit_label}{assignment_note}"
  Qualified Dividend: "Income: {sharesight_code} qualified dividend"
  NRA Tax Adj: "Foreign Tax (United States NRA Withholding)"
```

The bundled `config.yaml` includes mappings derived from your existing Sharesight ledgers.

## Supported Schwab actions

Buy, Sell, Journaled Shares (in-specie transfer pairs), dividends, NRA/foreign tax, margin/credit interest, ADR fees, return of capital, wire transfers (with fee and waiver), and basic option premium rows (STO/BTC/STC/BTO/Expired/Assigned). An Assigned row is merged into the same-day share trade: short calls and long puts onto the Sell, short puts and long calls onto the Buy. The line looks like `SELL 100 x NVDA.NASDAQ shares --> Assigned 1 x NVDA.NASDAQ 30/SEP/2026 227.50 CALL (SHORT)`. If that share trade is already in Sharesight, the assignment stays as its own $0 line.

**Journaled Shares** — Schwab uses signed quantity to distinguish direction (no separate config per account):

| Quantity | Account role | Output |
|----------|--------------|--------|
| Positive | Destination (shares in) | Two offsetting entries: contribution deposit + BUY withdrawal |
| Negative | Source (shares out) | One $0 entry documenting the transfer out |

**Destination** (positive quantity) — two offsetting cash entries (net zero balance):

1. **Deposit** — `Non-Concessional Contribution ({member_name}) (In-Specie Transfer …)`
2. **Withdrawal** — `BUY … shares (In-Specie Transfer)`

**Source** (negative quantity) — one zero-amount row:

- `Journaled Shares 25 x APPL.NASDAQ: Transfer shares to {destination_account} ( {contribution_type} )`

Configure placeholders in `config.yaml`:

```yaml
smsf:
  member_name: Renee Churches              # destination account
  destination_account: Living Trust        # source account (where shares are going)
  contribution_type: Voluntary Contribution
  # aud_exchange_rate: 1.52  # optional; adds (AU$…) to the inbound contribution line
```

The inbound USD amount is derived from `Quantity` × `Price`. Deposit is written before withdrawal in the CSV so Sharesight's newest-first ledger shows the BUY line above the contribution. Outbound rows leave both amount columns empty ($0).

Corporate actions (mergers, reverse splits, etc.) are reported as exceptions for now.

**Wire transfers** — Schwab exports three lines per wire (disbursement, fee, fee waiver). These map to:

| Schwab action | Description | Sharesight description |
|---------------|-------------|------------------------|
| Wire Sent | WIRED FUNDS DISBURSED | `Wire Transfer ([Destination])` |
| Service Fee | WIRED FUNDS FEE | `Wire Fee` |
| Misc Cash Entry | WAIVE WIRE FEE | `Wire Fee Refund` |

Replace `[Destination]` in Sharesight after import (e.g. `ANZ Offset Account`). Other `Service Fee` / `Misc Cash Entry` rows remain exceptions until a template is added.

Stock splits use ratios from `stock_splits` in config:

```yaml
stock_splits:
  NOW:
    ratio: "1:5"
  NFLX:
    ratio: "1:10"
  MNST:
    ratio: "1:2"
```

Withholding corrections (`Adjustment` rows) map to your `Adjustment: NRA withholding` / `Adjustment: Reverse NRA withholding` notation.

## Test fixtures

Committed fixtures use generic filenames and contain no account identifiers:

- `tests/fixtures/schwab_trust_catchup_sample.csv` — anonymized Schwab export (125 rows, Sep 2025–May 2026)
- `tests/fixtures/sharesight_trust_catchup_expected.csv` — matching Sharesight import

Real Schwab export filenames (often containing account numbers) are gitignored. Prefer keeping working copies in `local/` (see **How to use** above).
