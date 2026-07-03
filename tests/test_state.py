import tempfile
from pathlib import Path

from stockagent.state import State


def _state():
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return State(Path(f.name))


def test_set_get_target_roundtrip():
    st = _state()
    st.set_target_holdings("2026-07-01", {"512480": {"weight": 1 / 3, "entry_date": "2026-06-01"}})
    got = st.get_target_holdings()
    assert "512480" in got
    assert abs(got["512480"]["weight"] - 1 / 3) < 1e-9


def test_entry_date_preserved_for_held_new_for_new():
    st = _state()
    st.set_target_holdings("2026-07-01", {"512480": {"weight": 1 / 3, "entry_date": "2026-06-01"}})
    ed = st.derive_entry_dates("2026-07-02", ["512480", "562500"])
    assert ed["512480"] == "2026-06-01"  # held -> preserved
    assert ed["562500"] == "2026-07-02"  # new -> today


def test_report_idempotency():
    st = _state()
    assert not st.report_sent_today("2026-07-02")
    st.mark_report_sent("2026-07-02", "wecom", True)
    assert st.report_sent_today("2026-07-02")


def test_adherence_drift():
    st = _state()
    st.set_target_holdings("2026-07-01", {
        "512480": {"weight": 1 / 3, "entry_date": "2026-06-01"},
        "562500": {"weight": 1 / 3, "entry_date": "2026-06-01"},
        "512880": {"weight": 1 / 3, "entry_date": "2026-06-01"},
    })
    # user actually only holds two of three (drift)
    st.record_actual("2026-07-01", {"512480": 0.5, "562500": 0.5})
    adh = st.adherence()
    assert adh["available"]
    assert adh["adherence_pct"] < 100.0
