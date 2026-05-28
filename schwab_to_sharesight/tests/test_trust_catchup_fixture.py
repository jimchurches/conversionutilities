from pathlib import Path

import pandas as pd

from convert import convert_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_trust_catchup_fixture_converts_without_exceptions(tmp_path):
    """Regression test for the SMSF trust catch-up date range (anonymized fixture)."""
    input_path = FIXTURES / "schwab_trust_catchup_sample.csv"
    output_path = tmp_path / "sharesight_import.csv"
    exceptions_path = tmp_path / "exceptions.csv"

    converted_count, exception_count = convert_file(
        input_path=input_path,
        output_path=output_path,
        config_path=Path(__file__).parent.parent / "config.yaml",
        exceptions_path=exceptions_path,
    )

    assert exception_count == 0, f"Expected no exceptions, got {exception_count}"
    assert converted_count == 125
    assert output_path.exists()
    assert not exceptions_path.exists()

    actual = pd.read_csv(output_path)
    expected = pd.read_csv(FIXTURES / "sharesight_trust_catchup_expected.csv")
    pd.testing.assert_frame_equal(actual, expected)
