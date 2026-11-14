"""Offline tests (no internet needed): python -m pytest -q"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from track1 import archive, visits  # noqa: E402

FAKE = [
    # uri, t_start, t_end, band
    ["ibe/data/spherex/qr2/level2/a.fits", 60800.10, 60800.11, "SPHEREx-D2"],
    ["ibe/data/spherex/dr1/level2/a.fits", 60800.10, 60800.11, "SPHEREx-D2"],  # same exposure, newer
    ["ibe/data/spherex/qr3/level2/b.fits", 60800.50, 60800.51, "SPHEREx-D5"],
    ["ibe/data/spherex/qr3/level2/c.fits", 60985.20, 60985.21, "SPHEREx-D2"],  # ~6 months later
]


def fake_tap(query):
    assert "CONTAINS(POINT('ICRS'" in query
    return FAKE


def test_search_dedupes_and_builds_cutout_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "CACHE_DIR", tmp_path)
    exps = archive.search(133.79, -7.24, 0.083, tap=fake_tap)
    assert len(exps) == 3
    assert exps[0].release == "dr1"
    assert exps[0].cutout_url.startswith("https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/")
    assert "?center=133.7900000,-7.2400000d&size=0.08300" in exps[0].cutout_url


def test_visits_split_at_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "CACHE_DIR", tmp_path)
    exps = visits.group_visits(archive.search(10, 20, tap=fake_tap))
    assert [e.visit for e in exps] == [0, 0, 1]
    s = visits.summarize(exps)
    assert len(s) == 2 and s[0]["n_exposures"] == 2


def test_query_filters():
    q = archive.build_query(1, 2, detector="5", t_min_mjd=60000, t_max_mjd=61000)
    assert "energy_bandpassname = 'SPHEREx-D5'" in q
    assert ">= 60000" in q and "<= 61000" in q


def test_api_observations(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from track1 import api
    monkeypatch.setattr(archive, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(archive, "_run_tap", fake_tap)
    r = TestClient(api.app).get("/api/observations", params={"ra": 10, "dec": 20})
    assert r.status_code == 200
    body = r.json()
    assert body["n_exposures"] == 3 and len(body["visits"]) == 2


def _fake_visits(counts):
    """counts e.g. [122, 124, 130] -> exposures in 3 visits ~180 days apart."""
    exps = []
    for v, n in enumerate(counts):
        for i in range(n):
            t = 60800 + v * 180 + i * 0.1
            exps.append(archive.Exposure("u", "c", t, t + 0.001, "", "SPHEREx-D5", "dr1", v))
    return exps


def test_limit_is_shared_across_visits():
    sel = visits.select_frames(_fake_visits([122, 124, 130]), limit=8)
    per = [sum(e.visit == v for e in sel) for v in range(3)]
    assert len(sel) == 8 and per == [3, 3, 2]


def test_per_visit_evenly_spaced():
    sel = visits.select_frames(_fake_visits([122, 124, 130]), per_visit=4)
    assert len(sel) == 12
    v0 = [e for e in sel if e.visit == 0]
    assert v0[0].t_start_mjd == 60800 and abs(v0[-1].t_start_mjd - (60800 + 121 * 0.1)) < 1e-9


def test_small_visit_gives_its_leftover_to_others():
    sel = visits.select_frames(_fake_visits([2, 50, 50]), limit=12)
    assert [sum(e.visit == v for e in sel) for v in range(3)] == [2, 5, 5]


def test_no_options_returns_everything():
    assert len(visits.select_frames(_fake_visits([3, 4]))) == 7


def test_detector_filter_reuses_one_cached_query(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "CACHE_DIR", tmp_path)
    calls = []
    def counting_tap(q):
        calls.append(q)
        return FAKE
    d5 = archive.search(10, 20, detector="D5", tap=counting_tap)
    d2 = archive.search(10, 20, detector="D2", tap=counting_tap)
    allx = archive.search(10, 20, tap=counting_tap)
    assert len(calls) == 1
    assert [e.detector for e in d5] == ["SPHEREx-D5"]
    assert {e.detector for e in d2} == {"SPHEREx-D2"} and len(allx) == 3


def test_targets_load_and_propagate():
    from track1.targets import load_targets
    ts = {t.slug: t for t in load_targets()}
    assert len(ts) == 5
    b = ts["barnard-s-star"]
    assert b.detectors == ["D1", "D2", "D3"] and b.epoch == 2000.0
    ra, dec = b.position_at(2025.9)
    assert abs((dec - b.dec_deg) * 3600 - 10.36 * 25.9) < 0.01      # ~268 arcsec north
    w = ts["wise-0855-0714"]
    assert w.epoch == 2010.5 and w.detectors == ["D5", "D6"]
    ra, dec = w.position_at(2025.9)
    assert 120 < (w.ra_deg - ra) * 3600 * 0.992 < 130                # ~2 arcmin west
    assert ts["r-draconis"].detectors == [f"D{i}" for i in range(1, 7)]
