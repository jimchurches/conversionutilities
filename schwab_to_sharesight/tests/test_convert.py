from pathlib import Path

import pandas as pd
import pytest

from convert import (
    ConvertedRow,
    build_description,
    convert_file,
    convert_row,
    derive_row_amount,
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


def test_journaled_shares_inbound(config):
    row = pd.Series(
        {
            "Date": "06/23/2026",
            "Action": "Journaled Shares",
            "Symbol": "AAPL",
            "Description": "APPLE INC",
            "Quantity": "25",
            "Price": "$295.91 ",
            "Amount": "",
        }
    )

    assert derive_row_amount(row) == pytest.approx(-7397.75)

    result = convert_row(row, config)
    assert len(result) == 2
    deposit_row, withdrawal_row = result
    assert deposit_row.deposit == "7397.75"
    assert deposit_row.withdrawal == ""
    assert (
        deposit_row.description
        == "Non-Concessional Contribution (Test Member) (In-Specie Transfer 25 x APPL.NASDAQ)"
    )
    assert withdrawal_row.deposit == ""
    assert withdrawal_row.withdrawal == "7397.75"
    assert withdrawal_row.description == "BUY 25 APPL.NASDAQ shares (In-Specie Transfer)"


def test_journaled_shares_outbound(config):
    row = pd.Series(
        {
            "Date": "06/23/2026",
            "Action": "Journaled Shares",
            "Symbol": "AAPL",
            "Description": "APPLE INC",
            "Quantity": "-25",
            "Price": "$295.91 ",
            "Amount": "",
        }
    )

    assert derive_row_amount(row) is None

    result = convert_row(row, config)
    assert isinstance(result, ConvertedRow)
    assert result.deposit == ""
    assert result.withdrawal == ""
    assert (
        result.description
        == "Journaled Shares 25 x APPL.NASDAQ: Transfer shares to Test Super Fund ( Voluntary Contribution )"
    )


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

    assert "BUY 100 x INTC.NASDAQ shares --> Assigned 1 x INTC.NASDAQ 17/JAN/2025 45.00 PUT (SHORT)" == result.description
    assert result.withdrawal == "4500.00"
    assert len(merged) == 1


def test_assigned_short_call_merges_into_later_sell(config):
    assigned = pd.Series(
        {
            "Date": "10/01/2026 as of 09/30/2026",
            "Action": "Assigned",
            "Symbol": "NVDA 09/30/2026 227.50 C",
            "Description": "CALL NVIDIA CORP $227.5 EXP 09/30/26",
            "Quantity": "1",
            "Amount": "",
        }
    )
    sell = pd.Series(
        {
            "Date": "10/01/2026 as of 09/30/2026",
            "Action": "Sell",
            "Symbol": "NVDA",
            "Description": "NVIDIA CORP",
            "Quantity": "100",
            "Amount": "$22749.53",
        }
    )
    config_with_nvda = {
        **config,
        "securities": {
            **config["securities"],
            "NVDA": {"sharesight_code": "NVDA.NASDAQ", "unit_label": "shares"},
        },
    }
    merged = merge_assigned_buys([assigned, sell], config_with_nvda)
    assert len(merged) == 1
    result = convert_row(merged[0], config_with_nvda)
    assert (
        result.description
        == "SELL 100 x NVDA.NASDAQ shares --> Assigned 1 x NVDA.NASDAQ 30/SEP/2026 227.50 CALL (SHORT)"
    )
    assert result.deposit == "22749.53"


def test_assigned_long_call_merges_into_buy(config):
    buy = pd.Series(
        {
            "Date": "10/01/2026",
            "Action": "Buy",
            "Symbol": "NVDA",
            "Description": "NVIDIA CORP",
            "Quantity": "100",
            "Amount": "-$22750.00",
        }
    )
    assigned = pd.Series(
        {
            "Date": "10/01/2026",
            "Action": "Assigned",
            "Symbol": "NVDA 09/30/2026 227.50 C",
            "Description": "CALL NVIDIA CORP $227.5 EXP 09/30/26",
            "Quantity": "-1",
            "Amount": "",
        }
    )
    config_with_nvda = {
        **config,
        "securities": {
            **config["securities"],
            "NVDA": {"sharesight_code": "NVDA.NASDAQ", "unit_label": "shares"},
        },
    }
    merged = merge_assigned_buys([assigned, buy], config_with_nvda)
    result = convert_row(merged[0], config_with_nvda)
    assert (
        result.description
        == "BUY 100 x NVDA.NASDAQ shares --> Assigned 1 x NVDA.NASDAQ 30/SEP/2026 227.50 CALL (LONG)"
    )


def test_assigned_long_put_merges_into_sell(config):
    sell = pd.Series(
        {
            "Date": "10/01/2026",
            "Action": "Sell",
            "Symbol": "NVDA",
            "Description": "NVIDIA CORP",
            "Quantity": "100",
            "Amount": "$22750.00",
        }
    )
    assigned = pd.Series(
        {
            "Date": "10/01/2026",
            "Action": "Assigned",
            "Symbol": "NVDA 09/30/2026 227.50 P",
            "Description": "PUT NVIDIA CORP $227.5 EXP 09/30/26",
            "Quantity": "-1",
            "Amount": "",
        }
    )
    config_with_nvda = {
        **config,
        "securities": {
            **config["securities"],
            "NVDA": {"sharesight_code": "NVDA.NASDAQ", "unit_label": "shares"},
        },
    }
    merged = merge_assigned_buys([sell, assigned], config_with_nvda)
    result = convert_row(merged[0], config_with_nvda)
    assert (
        result.description
        == "SELL 100 x NVDA.NASDAQ shares --> Assigned 1 x NVDA.NASDAQ 30/SEP/2026 227.50 PUT (LONG)"
    )


def test_unmatched_assigned_row(config):
    assigned = pd.Series(
        {
            "Date": "07/24/2026 as of 07/23/2026",
            "Action": "Assigned",
            "Symbol": "DDOG 07/24/2026 265.00 P",
            "Description": "PUT DATADOG INC $265 EXP 07/24/26",
            "Quantity": "1",
            "Amount": "",
        }
    )
    config_with_ddog = {
        **config,
        "securities": {
            **config["securities"],
            "DDOG": {"sharesight_code": "DDOG.NASDAQ", "unit_label": "shares"},
        },
    }
    result = convert_row(assigned, config_with_ddog)
    assert isinstance(result, ConvertedRow)
    assert result.date == "24/7/2026"
    assert result.deposit == ""
    assert result.withdrawal == ""
    assert result.description == "Assigned 1 x DDOG.NASDAQ 24/JUL/2026 265.00 PUT (SHORT)"


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


def test_wire_transfer_rows(config):
    wire = convert_row(
        pd.Series(
            {
                "Date": "07/29/2026",
                "Action": "Wire Sent",
                "Symbol": "",
                "Description": "WIRED FUNDS DISBURSED",
                "Quantity": "",
                "Amount": "-$1900.00",
            }
        ),
        config,
    )
    assert isinstance(wire, ConvertedRow)
    assert wire.description == "Wire Transfer ([Destination])"
    assert wire.withdrawal == "1900.00"
    assert wire.deposit == ""

    fee = convert_row(
        pd.Series(
            {
                "Date": "07/29/2026",
                "Action": "Service Fee",
                "Symbol": "",
                "Description": "WIRED FUNDS FEE",
                "Quantity": "",
                "Amount": "-$15.00",
            }
        ),
        config,
    )
    assert isinstance(fee, ConvertedRow)
    assert fee.description == "Wire Fee"
    assert fee.withdrawal == "15.00"

    refund = convert_row(
        pd.Series(
            {
                "Date": "07/29/2026",
                "Action": "Misc Cash Entry",
                "Symbol": "",
                "Description": "WAIVE WIRE FEE",
                "Quantity": "",
                "Amount": "$15.00",
            }
        ),
        config,
    )
    assert isinstance(refund, ConvertedRow)
    assert refund.description == "Wire Fee Refund"
    assert refund.deposit == "15.00"


def test_unsupported_service_fee(config):
    result = convert_row(
        pd.Series(
            {
                "Date": "05/27/2026",
                "Action": "Service Fee",
                "Symbol": "",
                "Description": "OTHER SERVICE FEE",
                "Quantity": "",
                "Amount": "-$10.00",
            }
        ),
        config,
    )
    assert result.transaction_type == "Service Fee"
    assert result.reason == "Unsupported transaction type: Service Fee"


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
