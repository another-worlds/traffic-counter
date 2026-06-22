from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..helpers import latest_assignment, linkflows_fc
from ..models import Counter
from ..schemas import SummaryOut
from ..services import loader, pipeline
from ..storage import storage
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["results"])


def _load_sim(db: Session, scenario_id: str):
    res = latest_assignment(db, scenario_id)
    if not res:
        return None, {}
    df = storage.load_df(res.storage_ref)
    return res, {row["link_id"]: float(row["sim_vph"]) for _, row in df.iterrows()}


@router.get("/scenarios/{scenario_id}/results/link-flows")
def link_flows(scenario_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    _, links = loader.load_network(db, scenario_id)
    _, sim = _load_sim(db, scenario_id)
    targets = loader.load_counter_targets(db, scenario_id)
    return linkflows_fc(links, sim, targets)


@router.get("/scenarios/{scenario_id}/results/summary", response_model=SummaryOut)
def summary(scenario_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    _, links = loader.load_network(db, scenario_id)
    _, sim = _load_sim(db, scenario_id)
    targets = loader.load_counter_targets(db, scenario_id)
    metrics = pipeline.evaluate(sim, targets)
    return SummaryOut(
        scenario_id=scenario_id, n_links=len(links),
        n_counters=db.query(Counter).filter(Counter.scenario_id == scenario_id).count(),
        mean_geh=metrics["mean_geh"], pct_geh_lt5=metrics["pct_geh_lt5"], rmse=metrics["rmse"],
        total_vkt=(pipeline.total_vkt(sim, links) if sim else None),
    )
