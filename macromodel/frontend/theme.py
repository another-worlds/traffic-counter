"""Dark editor theme: CSS, dark base tiles, and MDI tool icons."""
from __future__ import annotations

# CartoDB dark base map (best-effort online; overlays render regardless of tiles).
DARK_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"

# MDI icons for the vertical element toolbar.
TOOL_ICONS = {
    "Select": "mdi-cursor-default",
    "Node": "mdi-vector-point",
    "Link": "mdi-vector-line",
    "Zone": "mdi-shape-polygon-plus",
    "Connector": "mdi-transit-connection-variant",
    "Stop": "mdi-bus-stop",
    "Detector": "mdi-cctv",
}

# Accent + chrome styling. We also flip Vuetify to its dark variant in sol_app.py, so
# this mostly handles the map, panels, and a few accents the theme doesn't cover.
CSS = """
.v-application { background: #12161d !important; }
.mm-appbar {
  background: linear-gradient(90deg,#1b2230,#141a24);
  border:1px solid #2a3342; border-radius:10px; padding:8px 14px; margin-bottom:8px;
}
.mm-title { font-weight:700; letter-spacing:.3px; color:#e8eef9; }
.mm-rail {
  background:#171c25; border:1px solid #2a3342; border-radius:10px;
  padding:8px; min-width:164px; max-width:182px;
}
.mm-rail .v-btn { justify-content:flex-start !important; text-transform:none !important;
  letter-spacing:0; margin:2px 0; }
.mm-section-label { color:#8aa0c2; font-size:11px; text-transform:uppercase;
  letter-spacing:.8px; margin:6px 2px 2px; }
.mm-panel {
  background:#1b212c; border:1px solid #2a3342; border-radius:10px; padding:10px 12px;
}
.mm-kv { display:grid; grid-template-columns:auto 1fr; gap:2px 10px; font-size:13px; }
.mm-kv b { color:#9fb3d4; font-weight:600; }
.mm-mono { font-family:ui-monospace,Menlo,monospace; color:#cfe0ff; }
.leaflet-container { background:#0c0f14 !important; border:1px solid #2a3342;
  border-radius:10px; }
.leaflet-bar a { background:#222a36 !important; color:#cfe0ff !important;
  border-color:#33405230 !important; }
.mm-legend { background:rgba(20,26,36,.88); color:#dbe6f7; padding:8px 10px;
  border-radius:8px; font-size:12px; line-height:1.5; border:1px solid #2a3342; }
.mm-legend i { display:inline-block; width:12px; height:12px; border-radius:2px;
  margin-right:6px; vertical-align:middle; }
"""
