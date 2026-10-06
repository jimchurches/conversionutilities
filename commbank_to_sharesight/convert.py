#!/usr/bin/env python3
"""Convert Commonwealth Bank CSV exports to Sharesight cash account import CSV."""

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

COMMBANK_DATE_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})")
DIVIDEND_RE = re.compile(
    r"^Direct Credit \d+ (.+?) (?:"
    r"(FNL|ITM) DIV|"
    r"SCHEME PAY|"
    r"DIVIDEND|"
    r"DIV"
    r")\b",
    re.IGNORECASE,
)
NON_DIVIDEND_DIRECT_CREDIT_RES = (
    r"COMMONWEALTH SEC COMMSEC",
    r"\bATO\b",
    r"SuperChoice",
    r"ClickSuper",
    r"QUICKSUPER",
    r"ITF HUB24",
    r"PUSHPAY HOLDINGS",
)
HUB24_DISTRIBUTION_RE = re.compile(
    r"^Direct Credit \d+ ITF HUB24\b",
    re.IGNORECASE,
)
ATO_REFUND_RE = re.compile(
    r"^Direct Credit \d+ ATO\b",
    re.IGNORECASE,
)


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
    source_description: str
    amount: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_description": self.source_description,
            "amount": self.amount,
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
    elif text.startswith("+"):
        text = text[1:].strip()
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


def parse_commbank_date(value: Any) -> datetime:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise ValueError("Missing date value")

    text = str(value).strip()
    if not text:
        raise ValueError("Missing date value")

    match = COMMBANK_DATE_RE.match(text)
    if not match:
        raise ValueError(f"Malformed date value: {value!r}")

    return datetime.strptime(match.group(1), "%d/%m/%Y")


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


def normalize_description(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def build_template_context(config: dict[str, Any]) -> dict[str, Any]:
    smsf = config.get("smsf", {})
    return {
        "member_name": smsf.get("member_name", "YOUR SMSF MEMBER NAME"),
        "employer_name": smsf.get("employer_name", "YOUR EMPLOYER NAME"),
    }


def match_description_pattern(description: str, config: dict[str, Any]) -> str | None:
    context = build_template_context(config)
    for rule in config.get("description_rules", []):
        pattern = rule.get("match")
        if pattern and re.search(pattern, description, re.IGNORECASE):
            return format_template(rule["template"], context)
    return None


def find_security(description: str, config: dict[str, Any]) -> dict[str, str] | None:
    upper = description.upper()
    best: tuple[int, dict[str, str]] | None = None

    for _key, mapping in config.get("securities", {}).items():
        for pattern in mapping.get("patterns", []):
            pattern_upper = pattern.upper()
            if pattern_upper in upper:
                score = len(pattern_upper)
                if best is None or score > best[0]:
                    best = (score, mapping)

    return best[1] if best else None


def dividend_kind(description: str) -> str:
    upper = description.upper()
    if " SCHEME PAY" in upper:
        return "scheme payment"
    if " ITM DIV" in upper or " ITM " in upper:
        return "interim dividend"
    if " FNL DIV" in upper or " FNL " in upper:
        return "final dividend"
    return "dividend"


def format_template(template: str, context: dict[str, Any]) -> str:
    return template.format(**context)


def is_direct_credit_dividend(description: str, config: dict[str, Any]) -> bool:
    if not description.upper().startswith("DIRECT CREDIT"):
        return False
    for pattern in NON_DIVIDEND_DIRECT_CREDIT_RES:
        if re.search(pattern, description, re.IGNORECASE):
            return False
    return find_security(description, config) is not None


def build_dividend_description(description: str, config: dict[str, Any]) -> str:
    if not DIVIDEND_RE.match(description) and not is_direct_credit_dividend(description, config):
        raise ValueError(f"Unable to parse dividend description: {description!r}")

    security = find_security(description, config)
    if security is None:
        raise KeyError("Missing security mapping in config.yaml")

    templates = config.get("transaction_templates", {})
    kind = dividend_kind(description)
    template_key = {
        "dividend": "Dividend",
        "interim dividend": "Interim Dividend",
        "final dividend": "Final Dividend",
        "scheme payment": "Scheme Payment",
    }[kind]
    template = templates.get(template_key) or templates.get("Dividend")
    if not template:
        raise KeyError(f"Missing transaction template for {template_key}")

    return format_template(
        template,
        {
            "sharesight_code": security["sharesight_code"],
            "name": security.get("name", ""),
            "source_description": description,
        },
    )


def build_description(description: str, config: dict[str, Any]) -> str:
    if description == "Credit Interest":
        template = config.get("transaction_templates", {}).get("Credit Interest")
        if not template:
            raise KeyError("Missing transaction template for Credit Interest")
        return template

    if HUB24_DISTRIBUTION_RE.match(description):
        security = find_security(description, config) or config.get("securities", {}).get("HUB24")
        if security is None:
            raise KeyError("Missing security mapping for HUB24 distribution")
        template = config.get("transaction_templates", {}).get("Hub24 Distribution")
        if not template:
            raise KeyError("Missing transaction template for Hub24 Distribution")
        return format_template(template, {"sharesight_code": security["sharesight_code"]})

    if ATO_REFUND_RE.match(description):
        template = config.get("transaction_templates", {}).get("ATO Refund")
        if not template:
            raise KeyError("Missing transaction template for ATO Refund")
        return template

    if DIVIDEND_RE.match(description) or is_direct_credit_dividend(description, config):
        return build_dividend_description(description, config)

    matched_template = match_description_pattern(description, config)
    if matched_template:
        return matched_template

    raise KeyError(f"Unsupported transaction description: {description}")


def is_valid_row(row: pd.Series) -> bool:
    if pd.isna(row.get("Date")) or not str(row.get("Date")).strip():
        return False
    if pd.isna(row.get("Description")) or not str(row.get("Description")).strip():
        return False
    return True


def read_commbank_csv(input_path: str | Path) -> pd.DataFrame:
    raw = pd.read_csv(
        input_path,
        header=None,
        names=["Date", "Amount", "Description", "Balance"],
        dtype=str,
        keep_default_na=False,
    )
    return raw.replace("", pd.NA)


def convert_row(row: pd.Series, config: dict[str, Any]) -> ConvertedRow | ExceptionRow:
    description = normalize_description(row.get("Description"))
    amount_text = "" if pd.isna(row.get("Amount")) else str(row.get("Amount")).strip()

    try:
        parsed_date = parse_commbank_date(row["Date"])
        amount = parse_money(row.get("Amount"))
        built_description = build_description(description, config)
    except KeyError as exc:
        message = exc.args[0] if exc.args else str(exc)
        if message == "Missing security mapping in config.yaml":
            reason = message
        else:
            reason = message
        return ExceptionRow(description, amount_text, reason)
    except ValueError as exc:
        return ExceptionRow(description, amount_text, str(exc))

    date_format = config.get("output", {}).get("date_format", "d/m/Y")
    deposit, withdrawal = split_amount(amount, config)
    return ConvertedRow(
        date=format_output_date(parsed_date, date_format),
        deposit=deposit,
        withdrawal=withdrawal,
        description=built_description,
    )


def convert_file(
    input_path: str | Path,
    output_path: str | Path,
    config_path: str | Path,
    exceptions_path: str | Path | None = None,
) -> tuple[int, int]:
    config = load_config(config_path)
    source_df = read_commbank_csv(input_path)
    source_df = source_df[source_df.apply(is_valid_row, axis=1)]

    converted_rows: list[ConvertedRow] = []
    exception_rows: list[ExceptionRow] = []

    for _, row in source_df.iterrows():
        result = convert_row(row, config)
        if isinstance(result, ConvertedRow):
            converted_rows.append(result)
        else:
            exception_rows.append(result)

    total_rows = len(source_df)
    converted_count = len(converted_rows)
    exception_count = len(exception_rows)

    if exceptions_path and exception_rows:
        exceptions_df = pd.DataFrame([item.to_dict() for item in exception_rows])
        exceptions_path = Path(exceptions_path)
        exceptions_path.parent.mkdir(parents=True, exist_ok=True)
        exceptions_df.to_csv(exceptions_path, index=False)

    if exception_rows:
        print(f"Read {total_rows} CommBank rows.")
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

    print(f"Read {total_rows} CommBank rows.")
    print(f"Converted {converted_count} rows.")
    print(f"Wrote Sharesight import file to {output_path}.")
    return converted_count, exception_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert CommBank CSV exports to Sharesight cash account import CSV."
    )
    parser.add_argument("input_path", help="Path to CommBank CSV export")
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
