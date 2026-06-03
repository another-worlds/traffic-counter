from app.services import georef, osm_import


def test_pcu_factor():
    assert georef.pcu_factor({"car": 10}) == 1.0
    # 1 car (1.0) + 1 truck (2.0) over 2 vehicles -> 1.5
    assert abs(georef.pcu_factor({"car": 1, "truck": 1}) - 1.5) < 1e-9


def test_direction_key():
    assert georef.direction_key("AB") == "positive"
    assert georef.direction_key("BA") == "negative"


def test_snap_to_link_picks_nearest():
    nodes, links, grid = osm_import.build_sample_network(rows=2, cols=2, lat0=0.0, lon0=0.0, spacing_m=300)
    # a point near the first node should snap to a link touching it
    n00 = grid[(0, 0)]
    snapped = georef.snap_to_link(0.0001, 0.0001, links)
    assert snapped is not None
    assert n00 in (snapped["from_node_id"], snapped["to_node_id"])


def test_counts_to_targets():
    counts_resp = {
        "per_line": [{
            "line_id": "L1",
            "total": 1200,
            "by_class": {"car": 800, "truck": 200, "bus": 200},
            "by_direction": {"positive": 700, "negative": 500},
        }]
    }
    # 2-hour video; positive direction
    out = georef.counts_to_targets(counts_resp, "L1", "AB", hours=2.0)
    assert abs(out["observed_vph"] - 350.0) < 1e-6           # 700 / 2h
    # pcu_factor = (800*1 + 200*2 + 200*2.5)/1200 = (800+400+500)/1200 = 1.4166...
    assert abs(out["pcu_vph"] - 350.0 * (1700.0 / 1200.0)) < 1e-6
