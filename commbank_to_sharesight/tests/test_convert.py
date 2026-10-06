from pathlib import Path

import pandas as pd
import pytest

from convert import (
    build_description,
    convert_file,
    convert_row,
    load_config,
    parse_commbank_date,
    parse_money,
    read_commbank_csv,
    split_amount,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def config() -> dict:
    return load_config(FIXTURES / "config_test.yaml")


def test_parse_commbank_date():
    parsed = parse_commbank_date("02/07/2026")
    assert parsed.strftime("%d/%m/%Y") == "02/07/2026"


def test_parse_money():
    assert parse_money("+478.80") == 478.80
    assert parse_money("-3315.97") == -3315.97
    assert parse_money("", required=False) is None


def test_build_description_dividend(config):
    description = build_description(
        "Direct Credit 077669 MQG FNL DIV 001357099821",
        config,
    )
    assert description == "Income: MQG.ASX final dividend"


def test_build_description_commsec(config):
    assert (
        build_description("Direct Debit 062934 COMMSEC SECURITI COMMSEC", config)
        == "Transfer to CommSec"
    )
    assert (
        build_description("Direct Credit 062895 COMMONWEALTH SEC COMMSEC", config)
        == "Transfer from CommSec"
    )


def test_build_description_interest(config):
    assert build_description("Credit Interest", config) == "Income: Interest payment"


def test_split_amount(config):
    deposit, withdrawal = split_amount(478.80, config)
    assert deposit == "478.80"
    assert withdrawal == ""

    deposit, withdrawal = split_amount(-3315.97, config)
    assert deposit == ""
    assert withdrawal == "3315.97"


def test_convert_row(config):
    row = pd.Series(
        {
            "Date": "02/07/2026",
            "Amount": "+100.00",
            "Description": "Direct Credit 077669 MQG FNL DIV 000000000001",
            "Balance": "+1000.00",
        }
    )
    converted = convert_row(row, config)
    assert converted.date == "2/7/2026"
    assert converted.deposit == "100.00"
    assert converted.withdrawal == ""
    assert converted.description == "Income: MQG.ASX final dividend"


def test_convert_file_matches_expected(config, tmp_path):
    output_path = tmp_path / "sharesight_import.csv"
    convert_file(
        input_path=FIXTURES / "commbank_sample.csv",
        output_path=output_path,
        config_path=FIXTURES / "config_test.yaml",
        exceptions_path=tmp_path / "exceptions.csv",
    )

    actual = pd.read_csv(output_path)
    expected = pd.read_csv(FIXTURES / "sharesight_expected.csv")
    pd.testing.assert_frame_equal(actual, expected)


def test_read_commbank_csv_has_expected_columns():
    frame = read_commbank_csv(FIXTURES / "commbank_sample.csv")
    assert list(frame.columns) == ["Date", "Amount", "Description", "Balance"]
    assert len(frame) == 9
