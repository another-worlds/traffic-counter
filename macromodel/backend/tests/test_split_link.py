"""split-link: inserting a node on a link replaces it (and its reverse twin) with two
attribute-inheriting halves, and re-snaps detectors that were on the old link. Exercised
through the real FastAPI routes with a SQLite stand-in."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _links(c, sid):
    return c.get(f"/scenarios/{sid}/objects/links").json()


def _nodes(c, sid):
    return c.get(f"/scenarios/{sid}/objects/nodes").json()


def _ends(link):
    return link["geom"]["coordinates"][0], link["geom"]["coordinates"][-1]


def test_split_link_creates_node_and_two_inheriting_halves():
    with TestClient(app) as c:
        sid = c.post("/scenarios", json={"name": "s"}).json()["id"]
        c.post(f"/scenarios/{sid}/network/load-sample", json={})
        links0, nodes0 = _links(c, sid), _nodes(c, sid)
        link = links0[0]
        twin = any(l["from_node_id"] == link["to_node_id"] and l["to_node_id"] == link["from_node_id"]
                   for l in links0)
        (ax, ay), (bx, by) = _ends(link)
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0

        r = c.post(f"/scenarios/{sid}/network/split-link",
                   json={"link_id": link["id"], "lon": mx, "lat": my})
        assert r.status_code == 200, r.text
        out = r.json()

        links1, nodes1 = _links(c, sid), _nodes(c, sid)
        assert len(nodes1) == len(nodes0) + 1
        assert len(links1) == len(links0) + (2 if twin else 1)
        assert link["id"] not in {l["id"] for l in links1}          # old link gone

        new = [l for l in links1 if l["id"] in set(out["new_link_ids"])]
        assert new
        assert all(l["lanes"] == link["lanes"] for l in new)         # inherited attributes
        assert all(l["link_type_id"] == link["link_type_id"] for l in new)
        # the primary link's two halves chain from -> mid -> to
        node_id = out["node_id"]
        assert any(l["from_node_id"] == link["from_node_id"] and l["to_node_id"] == node_id for l in new)
        assert any(l["from_node_id"] == node_id and l["to_node_id"] == link["to_node_id"] for l in new)


def test_split_at_endpoint_is_rejected():
    with TestClient(app) as c:
        sid = c.post("/scenarios", json={"name": "s"}).json()["id"]
        c.post(f"/scenarios/{sid}/network/load-sample", json={})
        link = _links(c, sid)[0]
        (ax, ay), _ = _ends(link)
        r = c.post(f"/scenarios/{sid}/network/split-link",
                   json={"link_id": link["id"], "lon": ax, "lat": ay})
        assert r.status_code == 400


def test_split_link_resnaps_detector():
    with TestClient(app) as c:
        sid = c.post("/scenarios", json={"name": "s"}).json()["id"]
        c.post(f"/scenarios/{sid}/network/load-sample", json={})
        link = _links(c, sid)[0]
        (ax, ay), (bx, by) = _ends(link)
        qx, qy = ax * 0.75 + bx * 0.25, ay * 0.75 + by * 0.25      # near the from-quarter
        cid = c.post(f"/scenarios/{sid}/network/insert-detector",
                     json={"lat": qy, "lon": qx, "name": "d", "link_id": link["id"], "snap": "link"}).json()["id"]
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
        out = c.post(f"/scenarios/{sid}/network/split-link",
                     json={"link_id": link["id"], "lon": mx, "lat": my}).json()
        d = next(x for x in c.get(f"/scenarios/{sid}/objects/detectors").json() if x["id"] == cid)
        assert d["snapped_link_id"] in set(out["new_link_ids"])     # repointed, not orphaned
