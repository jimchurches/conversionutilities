#!/usr/bin/env python3
"""Convert Charles Schwab CSV exports to Sharesight cash account import CSV."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

OPTION_SYMBOL_RE = re.compile(
    r"^(?P<underlying>[A-Z0-9.]+)\s+"
    r"(?P<expiry>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<strike>[\d.]+)\s+"
    r"(?P<right>[CP])$",
    re.IGNORECASE,
)
SCHWAB_DATE_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})")
NO_SECURITY_ACTIONS = {
    "NRA Tax Adj",
    "Foreign Tax Paid",
    "Pr Yr NRA Tax",
    "Margin Interest",
    "Credit Interest",
    "Wire Sent",
    "Service Fee",
    "Misc Cash Entry",
}
# Import-file order is reversed so Sharesight's newest-first display reads sell then buy.
OPTION_ACTION_ORDER = {
    "Buy to Close": 0,
    "Buy to Open": 1,
    "Sell to Close": 2,
    "Sell to Open": 3,
}


@dataclass
class OptionContract:
    underlying: str
    expiry: datetime
    strike: str
    right: str

    @property
    def option_type(self) -> str:
        return "CALL" if self.right.upper() == "C" else "PUT"

    def format_contract(self, sharesight_code: str | None = None) -> str:
        ticker = sharesight_code or self.underlying
        expiry = self.expiry.strftime("%d/%b/%Y").upper()
        return f"{ticker} {expiry} {self.strike} {self.option_type}"


@dataclass
class ConvertedRow:
    date: str
    deposit: str
    withdrawal: str
    description: str

    def to_dict(self, columns: list[str]) -> dict[str, Any]:
        mapping = {
            "Date": self.date,
            "Deposit amount": self.deposit,
            "Withdrawal amount": self.withdrawal,
            "Description": self.description,
        }
        return {column: mapping[column] for column in columns}


@dataclass
class ExceptionRow:
    source_symbol: str
    source_description: str
    transaction_type: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_symbol": self.source_symbol,
            "source_description": self.source_description,
            "transaction_type": self.transaction_type,
            "reason": self.reason,
        }


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def parse_money(value: Any, *, required: bool = True) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        if required:
            raise ValueError("Missing money value")
        return None

    text = str(value).strip()
    if not text or text.lower() == "nan":
        if required:
            raise ValueError("Missing money value")
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    elif text.startswith("-"):
        negative = True
        text = text[1:].strip()

    text = text.replace("$", "").replace(",", "").strip()
    if not text:
        if required:
            raise ValueError("Missing money value")
        return None

    amount = float(text)
    return -amount if negative else amount


def parse_schwab_date(value: Any) -> datetime:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise ValueError("Missing date value")

    text = str(value).strip()
    if not text:
        raise ValueError("Missing date value")

    match = SCHWAB_DATE_RE.match(text)
    if not match:
        raise ValueError(f"Malformed date value: {value!r}")

    date_text = match.group(1)
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_text, fmt)
        except ValueError:
            continue

    raise ValueError(f"Malformed date value: {value!r}")


def format_output_date(value: datetime, date_format: str) -> str:
    if date_format in {"d/m/Y", "j/n/Y"}:
        return f"{value.day}/{value.month}/{value.year}"
    return value.strftime(date_format)


def format_output_amount(amount: float | None, config: dict[str, Any]) -> str:
    if amount is None:
        return ""

    output = config.get("output", {})
    formatted = f"{abs(amount):,.2f}" if output.get("include_currency_symbol", False) else f"{abs(amount):.2f}"
    if output.get("withdrawal_parentheses", False) and amount < 0:
        return f"({formatted})"
    return formatted


def split_amount(amount: float | None, config: dict[str, Any]) -> tuple[str, str]:
    if amount is None or amount == 0:
        return "", ""

    formatted = format_output_amount(amount, config)
    if amount > 0:
        return formatted, ""
    return "", formatted


def row_symbol(row: pd.Series) -> str:
    if pd.isna(row.get("Symbol")):
        return ""
    return str(row["Symbol"]).strip()


def row_action(row: pd.Series) -> str:
    if pd.isna(row.get("Action")):
        return ""
    return str(row["Action"]).strip()


def row_quantity(row: pd.Series) -> float | None:
    if pd.isna(row.get("Quantity")) or str(row.get("Quantity")).strip() == "":
        return None
    return float(row["Quantity"])


def parse_option_contract(symbol: str) -> OptionContract:
    match = OPTION_SYMBOL_RE.match(symbol.strip())
    if not match:
        raise ValueError(f"Unable to parse option symbol: {symbol!r}")

    expiry = parse_schwab_date(match.group("expiry"))
    return OptionContract(
        underlying=match.group("underlying"),
        expiry=expiry,
        strike=match.group("strike"),
        right=match.group("right"),
    )


def underlying_symbol(symbol: str) -> str:
    if not symbol:
        return ""
    if OPTION_SYMBOL_RE.match(symbol.strip()):
        return parse_option_contract(symbol).underlying
    return symbol.split()[0]


def action_needs_security(action: str) -> bool:
    return action not in NO_SECURITY_ACTIONS


def get_security_mapping(symbol: str, config: dict[str, Any]) -> dict[str, str]:
    securities = config.get("securities", {})
    mapping = securities.get(symbol)
    if not mapping:
        raise KeyError("Missing security mapping in config.yaml")
    return mapping


def default_unit_label(config: dict[str, Any]) -> str:
    return config.get("defaults", {}).get("unit_label", "shares")


def build_template_context(row: pd.Series, config: dict[str, Any]) -> dict[str, Any]:
    action = row_action(row)
    symbol = row_symbol(row)
    stock_symbol = underlying_symbol(symbol)
    quantity = row_quantity(row)
    assignment_note = row.get("_assignment_note", "")
    context: dict[str, Any] = {
        "quantity": abs(quantity) if quantity is not None else "",
        "symbol": stock_symbol,
        "source_description": "" if pd.isna(row.get("Description")) else str(row["Description"]).strip(),
        "unit_label": default_unit_label(config),
        "assignment_note": assignment_note if pd.notna(assignment_note) and assignment_note else "",
    }

    if action_needs_security(action) and stock_symbol:
        mapping = get_security_mapping(stock_symbol, config)
        context["sharesight_code"] = mapping["sharesight_code"]
        context["unit_label"] = mapping.get("unit_label", default_unit_label(config))
        context["name"] = mapping.get("name", "")

    if action in {"Sell to Open", "Buy to Close", "Sell to Close", "Buy to Open", "Expired"}:
        contract = parse_option_contract(symbol)
        mapped_code = None
        if stock_symbol:
            try:
                mapped_code = get_security_mapping(stock_symbol, config)["sharesight_code"]
            except KeyError:
                mapped_code = None
        context["option_contract"] = contract.format_contract(contract.underlying)
        context["option_contract_mapped"] = contract.format_contract(mapped_code or contract.underlying)
        if quantity is not None and quantity != 0:
            context["position"] = "LONG" if quantity < 0 else "SHORT"
        else:
            context["position"] = "SHORT"

    return context


def is_valid_row(row: pd.Series) -> bool:
    if pd.isna(row.get("Date")) or not str(row.get("Date")).strip():
        return False
    if pd.isna(row.get("Action")) or not str(row.get("Action")).strip():
        return False
    return True


def build_adjustment_description(row: pd.Series, config: dict[str, Any]) -> str:
    amount = parse_money(row.get("Amount"), required=False) or 0
    source_desc = "" if pd.isna(row.get("Description")) else str(row["Description"]).strip().upper()
    symbol = underlying_symbol(row_symbol(row))

    if not symbol:
        return "Adjustment ( Foreign Tax )"

    code = get_security_mapping(symbol, config)["sharesight_code"]
    if amount > 0:
        return f"Adjustment: Reverse NRA withholding {code}"
    return f"Adjustment: NRA withholding {code}"


def build_stock_split_description(row: pd.Series, config: dict[str, Any]) -> str:
    symbol = underlying_symbol(row_symbol(row))
    mapping = get_security_mapping(symbol, config)
    code = mapping["sharesight_code"]
    split_config = config.get("stock_splits", {}).get(symbol, {})
    ratio = split_config.get("ratio")
    if ratio:
        return f"Stock split: {code} {ratio}"
    return f"Stock split: {code}"


def build_description(row: pd.Series, config: dict[str, Any]) -> str:
    action = row_action(row)
    if action == "Adjustment":
        return build_adjustment_description(row, config)
    if action == "Stock Split":
        return build_stock_split_description(row, config)

    templates = config.get("transaction_templates", {})
    template = templates.get(action)
    if not template:
        raise KeyError(f"Unsupported transaction type: {action}")

    context = build_template_context(row, config)
    return template.format(**context)


def merge_assigned_buys(rows: list[pd.Series], config: dict[str, Any]) -> list[pd.Series]:
    merged: list[pd.Series] = []
    pending_buys: dict[tuple[str, str], pd.Series] = {}

    for row in rows:
        if not is_valid_row(row):
            continue
        action = row_action(row)
        date_key = parse_schwab_date(row["Date"]).date().isoformat()

        if action == "Buy":
            key = (date_key, underlying_symbol(row_symbol(row)))
            buy_row = row.copy()
            buy_row["_assignment_note"] = ""
            pending_buys[key] = buy_row
            merged.append(buy_row)
            continue

        if action == "Assigned":
            contract = parse_option_contract(row_symbol(row))
            key = (date_key, contract.underlying)
            buy_row = pending_buys.get(key)
            if buy_row is None:
                merged.append(row)
                continue

            try:
                code = get_security_mapping(contract.underlying, config)["sharesight_code"]
            except KeyError:
                code = contract.underlying
            assignment_qty = abs(row_quantity(row) or 1)
            buy_row["_assignment_note"] = (
                f" --> Assigned: {assignment_qty:g} x {contract.format_contract(code)}"
            )
            continue

        merged.append(row)

    return merged


def reorder_same_day_option_rows(rows: list[pd.Series]) -> list[pd.Series]:
    """Order option rows for Sharesight import (BTC before STO in the CSV)."""
    result = list(rows)
    by_date: dict[Any, list[int]] = {}

    for idx, row in enumerate(result):
        action = row_action(row)
        if action not in OPTION_ACTION_ORDER:
            continue
        by_date.setdefault(parse_schwab_date(row["Date"]).date(), []).append(idx)

    for indices in by_date.values():
        if len(indices) < 2:
            continue
        slot_indices = sorted(indices)
        option_rows = [result[i] for i in indices]
        option_rows.sort(key=lambda row: OPTION_ACTION_ORDER[row_action(row)])
        for slot, row in zip(slot_indices, option_rows, strict=True):
            result[slot] = row

    return result


def convert_row(row: pd.Series, config: dict[str, Any]) -> ConvertedRow | ExceptionRow:
    symbol = row_symbol(row)
    source_description = "" if pd.isna(row.get("Description")) else str(row["Description"]).strip()
    action = row_action(row)

    try:
        parsed_date = parse_schwab_date(row["Date"])
        amount = parse_money(row.get("Amount"), required=False)
        description = build_description(row, config)
    except KeyError as exc:
        message = exc.args[0] if exc.args else str(exc)
        if message == "Missing security mapping in config.yaml":
            reason = message
        else:
            reason = f"Unsupported transaction type: {action}"
        return ExceptionRow(symbol, source_description, action, reason)
    except ValueError as exc:
        return ExceptionRow(symbol, source_description, action, str(exc))

    date_format = config.get("output", {}).get("date_format", "d/m/Y")
    deposit, withdrawal = split_amount(amount, config)
    return ConvertedRow(
        date=format_output_date(parsed_date, date_format),
        deposit=deposit,
        withdrawal=withdrawal,
        description=description,
    )


def convert_file(
    input_path: str | Path,
    output_path: str | Path,
    config_path: str | Path,
    exceptions_path: str | Path | None = None,
) -> tuple[int, int]:
    config = load_config(config_path)
    source_df = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    source_df = source_df.replace("", pd.NA)
    source_df = source_df[source_df.apply(is_valid_row, axis=1)]

    source_rows = merge_assigned_buys([row for _, row in source_df.iterrows()], config)
    source_rows = reorder_same_day_option_rows(source_rows)

    converted_rows: list[ConvertedRow] = []
    exception_rows: list[ExceptionRow] = []

    for row in source_rows:
        action = row_action(row)
        if action == "Assigned":
            continue
        result = convert_row(row, config)
        if isinstance(result, ConvertedRow):
            converted_rows.append(result)
        else:
            exception_rows.append(result)

    total_rows = len(source_rows)
    converted_count = len(converted_rows)
    exception_count = len(exception_rows)

    if exceptions_path and exception_rows:
        exceptions_df = pd.DataFrame([item.to_dict() for item in exception_rows])
        exceptions_path = Path(exceptions_path)
        exceptions_path.parent.mkdir(parents=True, exist_ok=True)
        exceptions_df.to_csv(exceptions_path, index=False)

    if exception_rows:
        print(f"Read {total_rows} Schwab rows.")
        print(f"Converted {converted_count} rows.")
        print(f"Found {exception_count} exceptions.")
        if exceptions_path:
            print(f"Wrote exceptions to {exceptions_path}.")
        print("No Sharesight import file was written because exceptions must be fixed first.")
        return converted_count, exception_count

    columns = config.get("output", {}).get(
        "columns",
        ["Date", "Deposit amount", "Withdrawal amount", "Description"],
    )
    output_df = pd.DataFrame([row.to_dict(columns) for row in converted_rows], columns=columns)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_csv(output_path, index=False)

    print(f"Read {total_rows} Schwab rows.")
    print(f"Converted {converted_count} rows.")
    print(f"Wrote Sharesight import file to {output_path}.")
    return converted_count, exception_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert Schwab CSV exports to Sharesight cash account import CSV."
    )
    parser.add_argument("input_path", help="Path to Schwab CSV export")
    parser.add_argument("output_path", help="Path for Sharesight import CSV")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--exceptions",
        default=None,
        help="Path for exceptions CSV (default: exceptions.csv next to output file)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    exceptions_path = args.exceptions
    if exceptions_path is None:
        output_path = Path(args.output_path)
        exceptions_path = output_path.parent / "exceptions.csv"

    convert_file(
        input_path=args.input_path,
        output_path=args.output_path,
        config_path=args.config,
        exceptions_path=exceptions_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
