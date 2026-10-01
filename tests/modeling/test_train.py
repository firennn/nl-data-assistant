import json
from datetime import date

import pytest

from modeling.train import load_weekly_revenue, main, train


def test_weekly_revenue_from_the_fixture(sample_db):
    df = load_weekly_revenue(start=date(2018, 1, 1), through=date(2018, 1, 21), db_path=sample_db)
    assert list(df["week"]) == [date(2018, 1, 1), date(2018, 1, 8), date(2018, 1, 15)]
    # o1 and o2 in the first week; the second week only has the canceled order.
    assert list(df["revenue"]) == pytest.approx([405.8, 0.0, 0.0])


def test_week_start_is_aligned_to_monday(sample_db):
    df = load_weekly_revenue(start=date(2018, 1, 3), through=date(2018, 1, 7), db_path=sample_db)
    assert list(df["week"]) == [date(2018, 1, 1)]
    assert df["revenue"].iloc[0] == pytest.approx(405.8)


def test_through_must_be_a_sunday(sample_db):
    with pytest.raises(ValueError, match="Sunday"):
        load_weekly_revenue(through=date(2018, 1, 20), db_path=sample_db)


def test_train_writes_model_and_metrics(sample_db, tmp_path):
    result = train(through=date(2018, 1, 21), horizon=2, out_dir=tmp_path, db_path=sample_db)
    model = json.loads((tmp_path / "forecast_model.json").read_text(encoding="utf-8"))
    metrics = json.loads((tmp_path / "forecast_metrics.json").read_text(encoding="utf-8"))
    assert model == result["model"]
    assert model["trained_through"] == "2018-01-21"
    assert model["horizon_weeks"] == 2
    assert [row["week"] for row in model["forecast"]] == ["2018-01-22", "2018-01-29"]
    assert set(metrics["backtest"]) == {"naive", "holt_damped"}


def test_cli_prints_scores_and_saves(sample_db, tmp_path, capsys):
    args = ["--through", "2018-01-21", "--horizon", "2", "--out", str(tmp_path)]
    main([*args, "--db", str(sample_db)])
    output = capsys.readouterr().out
    assert "Backtest (rolling origin)" in output
    assert "naive baseline" in output
    assert (tmp_path / "forecast_model.json").exists()
