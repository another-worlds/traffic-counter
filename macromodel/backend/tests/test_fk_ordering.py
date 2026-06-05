"""persist_network must insert nodes before links, so the foreign key holds on databases
that enforce it (Postgres in production). The default test SQLite does NOT enforce foreign
keys, which is why an insert-ordering bug here stayed invisible; this test spins up a
dedicated FK-enforcing SQLite engine to lock the ordering in."""
import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import models  # noqa: E402
from app.db import Base  # noqa: E402
from app.services import loader  # noqa: E402


def _fk_engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return engine


def test_persist_network_orders_nodes_before_links():
    s = Session(_fk_engine())
    sc = models.Scenario(name="t")
    s.add(sc)
    s.flush()
    nodes = [{"id": "n1", "name": "a", "geom": {"type": "Point", "coordinates": [0, 0]}},
             {"id": "n2", "name": "b", "geom": {"type": "Point", "coordinates": [1, 1]}}]
    links = [{"id": "l1", "name": "a->b", "from_node_id": "n1", "to_node_id": "n2",
              "geom": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
              "length_m": 1.0, "lanes": 1, "free_flow_speed_ms": 10.0, "jam_density": 0.2,
              "oneway": True}]
    loader.persist_network(s, sc.id, nodes, links)
    s.flush()  # raised IntegrityError before the fix (links inserted before their nodes)
    assert s.query(models.Link).count() == 1
    assert s.query(models.Node).count() == 2
