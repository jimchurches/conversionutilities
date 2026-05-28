from pathlib import Path

import pandas as pd
import pytest

from convert import (
    build_description,
    convert_file,
    convert_row,
    get_security_mapping,
    load_config,
    merge_assigned_buys,
    parse_money,
    parse_option_contract,
    parse_schwab_date,
    reorder_same_day_dividend_tax_rows,
    reorder_same_day_option_rows,
    row_action,
    split_amount,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def config() -> dict:
    return load_config(FIXTURES / "config_test.yaml")


def test_parse_schwab_date():
    parsed = parse_schwab_date("05/27/2026")
    assert parsed.strftime("%d/%m/%Y") == "27/05/2026"
    parsed_asof = parse_schwab_date("01/21/2025 as of 01/17/2025")
    assert parsed_asof.strftime("%d/%m/%Y") == "21/01/2025"


def test_parse_money():
    assert parse_money("$1,319.87") == 1319.87
    assert parse_money("($100.50)") == -100.50
    assert parse_money("-25.00") == -25.0
    assert parse_money("", required=False) is None


def test_parse_option_contract():
    contract = parse_option_contract("LSCC 06/18/2026 85.00 C")
    assert contract.underlying == "LSCC"
    assert contract.format_contract("LSCC") == "LSCC 18/JUN/2026 85.00 CALL"


def test_build_description(config):
    row = pd.Series(
        {
            "Action": "Sell",
            "Symbol": "LULU",
            "Quantity": "10",
            "Description": "LULULEMON ATHLETICA INC",
        }
    )
    description = build_description(row, config)
    assert description == "SELL 10 x LULU.NASDAQ shares"


def test_split_amount(config):
    deposit, withdrawal = split_amount(1319.87, config)
    assert deposit == "1319.87"
    assert withdrawal == ""

    deposit, withdrawal = split_amount(-18.63, config)
    assert deposit == ""
    assert withdrawal == "18.63"


def test_dividend_and_tax_rows(config):
    dividend = pd.Series(
        {
            "Date": "05/14/2026",
            "Action": "Qualified Dividend",
            "Symbol": "AAPL",
            "Description": "APPLE INC",
            "Quantity": "",
            "Amount": "$124.20",
        }
    )
    tax = pd.Series(
        {
            "Date": "05/14/2026",
            "Action": "NRA Tax Adj",
            "Symbol": "AAPL",
            "Description": "APPLE INC",
            "Quantity": "",
            "Amount": "-$18.63",
        }
    )

    div_row = convert_row(dividend, config)
    tax_row = convert_row(tax, config)

    assert div_row.deposit == "124.20"
    assert div_row.withdrawal == ""
    assert div_row.description == "Income: APPL.NASDAQ qualified dividend"
    assert tax_row.deposit == ""
    assert tax_row.withdrawal == "18.63"
    assert tax_row.description == "Foreign Tax (United States NRA Withholding)"


def test_reorder_same_day_option_rows():
    btc = pd.Series(
        {
            "Date": "12/19/2025",
            "Action": "Buy to Close",
            "Symbol": "LSCC 12/19/2025 70.00 C",
            "Description": "CALL",
            "Quantity": "1",
            "Amount": "($387.66)",
        }
    )
    sto = pd.Series(
        {
            "Date": "12/19/2025",
            "Action": "Sell to Open",
            "Symbol": "LSCC 03/20/2026 75.00 C",
            "Description": "CALL",
            "Quantity": "1",
            "Amount": "$696.34",
        }
    )
    dividend = pd.Series(
        {
            "Date": "12/19/2025",
            "Action": "Qualified Dividend",
            "Symbol": "NVDA",
            "Description": "NVIDIA CORP",
            "Quantity": "",
            "Amount": "$1.70",
        }
    )

    reordered = reorder_same_day_option_rows([dividend, btc, sto])
    assert row_action(reordered[0]) == "Qualified Dividend"
    assert row_action(reordered[1]) == "Buy to Close"
    assert row_action(reordered[2]) == "Sell to Open"


def test_reorder_same_day_dividend_tax_rows():
    tax = pd.Series(
        {
            "Date": "05/26/2026",
            "Action": "NRA Tax Adj",
            "Symbol": "WMT",
            "Description": "WALMART INC",
            "Quantity": "",
            "Amount": "($2.34)",
        }
    )
    dividend = pd.Series(
        {
            "Date": "05/26/2026",
            "Action": "Qualified Dividend",
            "Symbol": "WMT",
            "Description": "WALMART INC",
            "Quantity": "",
            "Amount": "$15.59",
        }
    )

    reordered = reorder_same_day_dividend_tax_rows([tax, dividend])
    assert row_action(reordered[0]) == "Qualified Dividend"
    assert row_action(reordered[1]) == "NRA Tax Adj"


def test_merge_assigned_buy(config):
    buy = pd.Series(
        {
            "Date": "02/24/2023 as of 02/23/2023",
            "Action": "Buy",
            "Symbol": "INTC",
            "Description": "INTEL CORP",
            "Quantity": "100",
            "Amount": "-$4500.00",
        }
    )
    assigned = pd.Series(
        {
            "Date": "02/24/2023 as of 02/23/2023",
            "Action": "Assigned",
            "Symbol": "INTC 01/17/2025 45.00 P",
            "Description": "PUT INTEL CORP $45 EXP 01/17/25",
            "Quantity": "1",
            "Amount": "",
        }
    )

    config_with_intc = {
        **config,
        "securities": {
            **config["securities"],
            "INTC": {"sharesight_code": "INTC.NASDAQ", "unit_label": "shares"},
        },
    }
    merged = merge_assigned_buys([buy, assigned], config_with_intc)
    result = convert_row(merged[0], config_with_intc)

    assert "BUY 100 x INTC.NASDAQ shares --> Assigned: 1 x INTC.NASDAQ 17/JAN/2025 45.00 PUT" == result.description
    assert result.withdrawal == "4500.00"


def test_full_csv_conversion(config, tmp_path):
    output_path = tmp_path / "sharesight.csv"
    exceptions_path = tmp_path / "exceptions.csv"

    converted_count, exception_count = convert_file(
        input_path=FIXTURES / "schwab_sample.csv",
        output_path=output_path,
        config_path=FIXTURES / "config_test.yaml",
        exceptions_path=exceptions_path,
    )

    assert converted_count == 1
    assert exception_count == 0
    assert output_path.exists()
    assert not exceptions_path.exists()

    actual = pd.read_csv(output_path)
    expected = pd.read_csv(FIXTURES / "sharesight_expected.csv")
    pd.testing.assert_frame_equal(actual, expected)


def test_missing_ticker_mapping(config):
    row = pd.Series(
        {
            "Date": "05/27/2026",
            "Action": "Sell",
            "Symbol": "XYZ",
            "Description": "Example Holding Inc",
            "Quantity": "10",
            "Amount": "$100.00",
        }
    )
    result = convert_row(row, config)
    assert result.source_symbol == "XYZ"
    assert result.transaction_type == "Sell"
    assert result.reason == "Missing security mapping in config.yaml"


def test_unsupported_transaction_type(config):
    row = pd.Series(
        {
            "Date": "05/27/2026",
            "Action": "Wire Sent",
            "Symbol": "",
            "Description": "FX WIRE OUT",
            "Quantity": "",
            "Amount": "-$100.00",
        }
    )
    result = convert_row(row, config)
    assert result.transaction_type == "Wire Sent"
    assert result.reason == "Unsupported transaction type: Wire Sent"


def test_conversion_stops_on_exceptions(config, tmp_path):
    input_path = tmp_path / "schwab.csv"
    input_path.write_text(
        "\n".join(
            [
                "Date,Action,Symbol,Description,Quantity,Price,Fees & Comm,Amount",
                '05/27/2026,Sell,LULU,LULULEMON ATHLETICA INC,10,$131.99,$0.03,"$1,319.87"',
                "05/28/2026,Sell,XYZ,Example Holding Inc,5,$10.00,$0.00,$50.00",
            ]
        )
    )

    output_path = tmp_path / "sharesight.csv"
    exceptions_path = tmp_path / "exceptions.csv"

    converted_count, exception_count = convert_file(
        input_path=input_path,
        output_path=output_path,
        config_path=FIXTURES / "config_test.yaml",
        exceptions_path=exceptions_path,
    )

    assert converted_count == 1
    assert exception_count == 1
    assert not output_path.exists()
    assert exceptions_path.exists()

    exceptions = pd.read_csv(exceptions_path)
    assert len(exceptions) == 1
    assert exceptions.iloc[0]["source_symbol"] == "XYZ"
