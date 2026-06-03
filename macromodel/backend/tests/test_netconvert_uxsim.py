import numpy as np
import pytest

from app.services import netconvert, osm_import

# UXsim is only needed for the live-simulation check; skip cleanly if absent.
pytest.importorskip("uxsim")


def test_build_graph_is_connected_grid():
    import networkx as nx

    nodes, links, _ = osm_import.build_sample_network(rows=3, cols=3)
    G = netconvert.build_graph(nodes, links)
    assert G.number_of_nodes() == 9
    assert nx.is_strongly_connected(G)


def test_uxsim_assignment_runs_and_loads_links():
    from app.services import assignment

    nodes, links, grid = osm_import.build_sample_network(rows=3, cols=3)
    connectors = [grid[(0, 0)], grid[(2, 2)]]
    T = np.zeros((2, 2))
    T[0, 1] = 400.0  # 400 veh/h from corner to corner

    sim, W = assignment.assign(nodes, links, connectors, T, deltan=5, tmax=3600)
    assert isinstance(sim, dict) and len(sim) > 0
    assert sum(sim.values()) > 0  # some links carried flow
