"""S233 defect tests for scripts/wb_research/nat_mesh.py — the national-feed mesh
collector. The one HIGH-severity failure mode (per the build scoping) is the
Celsius->Fahrenheit conversion: every national feed reports °C, the mesh JSONL
schema is °F, and a C-written-as-F row silently corrupts the debias offset table
that the flag-on nowcast signal consumes. These tests pin c_to_f, the row schema
(qc=1, namespaced pws, sid, int epoch), and each feed parser against the raw
samples captured live from the VPS this session."""
import importlib.util
import io
import pathlib
import zipfile

import pytest

# Load the standalone research script by file path (it is not a package module;
# its top-level `bots...` import resolves via PYTHONPATH, and main() is guarded).
_NAT_MESH_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts" / "wb_research" / "nat_mesh.py"
)
_spec = importlib.util.spec_from_file_location("nat_mesh", _NAT_MESH_PATH)
nat_mesh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nat_mesh)


# ── C -> F converter (the HIGH-severity bug surface) ──────────────────────

class TestCelsiusToFahrenheit:
    @pytest.mark.parametrize("c,f", [
        (0.0, 32.0),
        (100.0, 212.0),
        (-40.0, -40.0),        # the fixed point
        (37.0, 98.6),
        (18.7, 65.66),         # DWD Berlin live value this session
        (28.3, 82.94),         # JMA Tokyo live value this session
        (28.2, 82.76),         # data.gov.sg live value this session
        (10.5, 50.9),          # BOM Sydney live value this session
    ])
    def test_c_to_f_exact(self, c, f):
        assert nat_mesh.c_to_f(c) == pytest.approx(f, abs=1e-9)

    def test_c_to_f_is_not_identity(self):
        # Guards against a "already Fahrenheit" regression that drops the convert.
        assert nat_mesh.c_to_f(20.0) != 20.0


# ── row schema (mesh_debias consumes qc==1 + temp_f + sid + epoch + pws) ──

class TestBuildRow:
    def test_row_schema_and_types(self):
        row = nat_mesh.build_row(
            "jma", "RJTT", 1784564400, 82.94, "2026-07-20T16:20:00Z",
            35.5533, 139.78, "2026-07-20T16:21:00Z")
        assert row["sid"] == "RJTT"
        assert row["pws"] == "nat:jma:RJTT"     # stable per (feed, city) key
        assert row["qc"] == 1                    # official source; mesh_debias filters qc==1
        assert row["src"] == "nat"               # provenance tag
        assert isinstance(row["epoch"], int)     # cursor/dedup key must be int
        assert row["temp_f"] == 82.94
        # every key pws_mesh rows carry (mesh_debias reads sid/epoch/temp_f/pws)
        for k in ("sid", "pws", "km", "epoch", "obs_utc", "temp_f", "qc", "lat", "lon", "fetched_at"):
            assert k in row


# ── per-feed parsers vs raw samples captured live from the VPS (S233) ──────

def _patch_get(monkeypatch, dispatch):
    """Replace nat_mesh._get(url, headers, timeout) with a URL-dispatched stub."""
    def fake_get(url, headers, timeout=30):
        for needle, payload in dispatch.items():
            if needle in url:
                return payload
        raise AssertionError("unexpected URL in test: %s" % url)
    monkeypatch.setattr(nat_mesh, "_get", fake_get)


class TestDwdFetch:
    def test_dwd_last_valid_row_c_to_f(self, monkeypatch):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(
                "produkt_zehn_now_tu_20260101_20260720_00427.txt",
                "STATIONS_ID;MESS_DATUM;QN;PP_10;TT_10;TM5_10;RF_10;TD_10;eor\n"
                "427;202607201550;1;-999;17.0;16.5;70.0;12.0;eor\n"
                "427;202607201600;1;-999;18.7;18.0;69.0;13.0;eor\n"
                "427;202607201610;1;-999;-999;-999;-999;-999;eor\n",  # trailing missing
            )
        _patch_get(monkeypatch, {"10minutenwerte_TU_00427_now.zip": buf.getvalue()})
        epoch, temp_f, obs_utc, lat, lon = nat_mesh.dwd_fetch("00427", 52.3807, 13.5306)
        # keeps the LAST valid row (18.7C), skipping the -999 that follows
        assert temp_f == pytest.approx(65.66, abs=1e-2)
        assert obs_utc == "2026-07-20T16:00:00Z"
        assert isinstance(epoch, int)
        assert (lat, lon) == (52.3807, 13.5306)


class TestJmaFetch:
    def test_jma_map_temp_c_to_f_qc_gate(self, monkeypatch):
        _patch_get(monkeypatch, {
            "latest_time.txt": b"2026-07-21T01:20:00+09:00",
            "/map/": b'{"44166": {"temp": [28.3, 0]}, "99999": {"temp": [9.9, 0]}}',
        })
        epoch, temp_f, obs_utc, lat, lon = nat_mesh.jma_fetch("44166", 35.5533, 139.78)
        assert temp_f == pytest.approx(82.94, abs=1e-2)
        assert obs_utc == "2026-07-20T16:20:00Z"   # +09:00 slot -> UTC
        assert isinstance(epoch, int)

    def test_jma_bad_qc_returns_none(self, monkeypatch):
        _patch_get(monkeypatch, {
            "latest_time.txt": b"2026-07-21T01:20:00+09:00",
            "/map/": b'{"44166": {"temp": [28.3, 5]}}',   # qc != 0
        })
        assert nat_mesh.jma_fetch("44166", 35.5533, 139.78) is None


class TestSgFetch:
    def test_sg_nearest_station_c_to_f(self, monkeypatch):
        payload = (
            b'{"metadata": {"stations": ['
            b'{"id": "S107", "location": {"latitude": 1.3133, "longitude": 103.962}},'
            b'{"id": "S50", "location": {"latitude": 1.3337, "longitude": 103.776}}]},'
            b'"items": [{"timestamp": "2026-07-21T00:15:00+08:00",'
            b'"readings": [{"station_id": "S107", "value": 28.2},'
            b'{"station_id": "S50", "value": 40.0}]}]}'
        )
        _patch_get(monkeypatch, {"air-temperature": payload})
        epoch, temp_f, obs_utc, lat, lon = nat_mesh.sg_fetch(1.36, 103.99)
        # S107 (6 km) is nearer WSSS than S50 -> its 28.2C is chosen, not 40.0
        assert temp_f == pytest.approx(82.76, abs=1e-2)
        assert obs_utc == "2026-07-20T16:15:00Z"   # +08:00 -> UTC
        assert (lat, lon) == (1.3133, 103.962)


class TestBomFetch:
    def test_bom_newest_valid_c_to_f(self, monkeypatch):
        payload = (
            b'{"observations": {"data": ['
            b'{"air_temp": 11.0, "aifstime_utc": "20260720153000", "lat": -33.9, "lon": 151.2},'
            b'{"air_temp": 10.5, "aifstime_utc": "20260720160000", "lat": -33.9, "lon": 151.2},'
            b'{"air_temp": null, "aifstime_utc": "20260720163000", "lat": -33.9, "lon": 151.2}]}}'
        )
        _patch_get(monkeypatch, {"IDN60901.94767.json": payload})
        epoch, temp_f, obs_utc, lat, lon = nat_mesh.bom_fetch("YSSY")
        # newest NON-NULL row is 16:00Z @ 10.5C (the 16:30Z row is null -> skipped)
        assert temp_f == pytest.approx(50.9, abs=1e-2)
        assert obs_utc == "2026-07-20T16:00:00Z"
        assert (lat, lon) == (-33.9, 151.2)


class TestFeedsConfigIntegrity:
    def test_all_feed_sids_in_registry(self):
        # Never anchor a ghost: every wired sid must be a current registry ICAO.
        from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY
        ids = {s.station_id for s in STATION_REGISTRY.values()}
        for feed, sid, _station, _fn in nat_mesh.FEEDS:
            assert sid in ids, f"{feed} sid {sid} not in station registry"

    def test_default_mode_is_staging_not_live(self):
        # LIVE must default OFF — the collector must not inject into the consumed
        # pws_mesh files until the operator sets NAT_MESH_LIVE=1.
        import os
        assert nat_mesh.LIVE == (os.environ.get("NAT_MESH_LIVE") == "1")
