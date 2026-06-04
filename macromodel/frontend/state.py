"""Shared reactive state for the multi-section app."""
from __future__ import annotations

import solara

# app shell
scenarios = solara.reactive([])          # list of scenario dicts
scenario_id = solara.reactive("")
section = solara.reactive("Network")
status = solara.reactive("Load the demo scenario, or create one, to begin.")

# map / network editor
map_center = solara.reactive((41.305, 69.265))
map_zoom = solara.reactive(14)
map_data = solara.reactive(None)         # /map response: {nodes, links, zones, connectors, stops, lines, detectors}
flows_fc = solara.reactive(None)         # results/link-flows
active_tool = solara.reactive("Select")
selected = solara.reactive(None)         # {"obj": str, "id": str, "props": dict}
drag_pos = solara.reactive(None)         # [lon, lat] live position of a node being moved
pending_link_from = solara.reactive(None)
link_type_id = solara.reactive("")       # link type for inserts
# detector source binding
source_labels = solara.reactive([])
selected_source_label = solara.reactive("")
direction = solara.reactive("AB")

# lists
list_obj = solara.reactive("links")

# procedures
proc_log = solara.reactive([])           # list of {op_type,status,log}
proc_metrics = solara.reactive(None)

# matrices
selected_matrix = solara.reactive("")
matrix_view = solara.reactive(None)      # values payload
