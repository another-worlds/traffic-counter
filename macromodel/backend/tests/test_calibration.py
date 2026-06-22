import numpy as np

from app.services import calibration, netconvert, osm_import


def test_geh_formula():
    assert calibration.geh([100], [100])[0] == 0.0
    # M=200, C=100 -> sqrt(2*100^2/300) ~= 8.165
    assert abs(calibration.geh([200], [100])[0] - 8.1650) < 1e-3


def _aon_assign(G, connectors):
    """Deterministic all-or-nothing assignment (loads every shortest path) for testing."""
    def assign(T):
        vol = {}
        n = len(connectors)
        for i in range(n):
            for j in range(n):
                if i == j or T[i, j] <= 0:
                    continue
                for lid in calibration._path_link_ids(G, connectors[i], connectors[j]):
                    vol[lid] = vol.get(lid, 0.0) + float(T[i, j])
        return vol
    return assign


def test_odme_recovers_synthetic_ground_truth():
    nodes, links, grid = osm_import.build_sample_network(rows=3, cols=3)
    G = netconvert.build_graph(nodes, links)
    connectors = [grid[(0, 0)], grid[(0, 2)], grid[(2, 0)], grid[(2, 2)]]
    n = len(connectors)
    rng = np.random.default_rng(0)

    T_true = rng.uniform(50, 200, size=(n, n))
    np.fill_diagonal(T_true, 0.0)
    assign = _aon_assign(G, connectors)
    sim_true = assign(T_true)

    counted = sorted(sim_true, key=sim_true.get, reverse=True)[:6]
    counters = [{"link_id": lid, "target_vph": sim_true[lid]} for lid in counted]

    T0 = T_true * rng.uniform(0.4, 1.8, size=(n, n))
    np.fill_diagonal(T0, 0.0)

    _, history = calibration.calibrate(T0, connectors, counters, assign, G, max_iters=20, clamp=3.0)

    assert history[-1]["mean_geh"] < history[0]["mean_geh"]      # improved
    assert history[-1]["mean_geh"] < 2.0                          # near-perfect on counted links
    assert history[-1]["pct_geh_lt5"] >= 85.0
