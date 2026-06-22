import numpy as np
import pytest

from app.services import modechoice

pytest.importorskip("uxsim")  # the procedures run drives UXsim


def test_logit_split_conserves_total():
    T = np.array([[0.0, 100.0], [80.0, 0.0]])
    t = np.array([[1.0, 10.0], [10.0, 1.0]])
    params = {"PrT": {"asc": 0.0, "beta_time": -0.06}, "PuT": {"asc": -0.8, "beta_time": -0.04}}
    prt, put = modechoice.logit_split(T, t, t * 1.6, params)
    assert np.allclose(prt + put, T)
    assert (prt >= 0).all() and (put >= 0).all()
    # PrT should win the cheap OD pair (lower time, asc 0)
    assert prt[0, 1] > put[0, 1]


def test_procedure_sequence_runs_and_calibrates_demo():
    from app.db import SessionLocal, init_db
    from app.services import demo, procedures

    init_db()
    db = SessionLocal()
    try:
        sid = demo.build_demo(db, seed=7)
        res = procedures.run_sequence(db, sid)
        statuses = {r["op_type"]: r["status"] for r in res["results"]}
        assert set(statuses.values()) == {"ok"}, statuses
        # full default sequence present
        assert {"TripGeneration", "TripDistribution", "ModeChoice", "PrTAssignment",
                "MatrixCorrection", "Validation"} <= set(statuses)
        # calibration brought the fit to a usable level
        assert res["metrics"]["mean_geh"] < 6.0
        assert res["metrics"]["pct_geh_lt5"] >= 70.0
    finally:
        db.close()
