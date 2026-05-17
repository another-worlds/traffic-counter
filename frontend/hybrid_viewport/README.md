# Hybrid Viewport (Planned)

This directory is the planned React/Vite bundle for the advanced counting-line overlay.

## Responsibilities

- Render the synchronized frame scrubber.
- Own drag/select/edit state for counting lines.
- Emit line edits and viewport events to the Streamlit host.
- Surface layer toggles, auto-suggest actions, heatmap toggles, and count diagnostics.

## Planned Files

- `package.json`
- `vite.config.ts`
- `src/main.tsx`
- `src/App.tsx`
- `src/components/Viewport.tsx`
- `src/components/LineEditor.tsx`
- `src/components/DiagnosticsPanel.tsx`

## Contract

The Streamlit shell remains the page host. The React/Vite bundle owns local interactivity and sends authoritative edit events back through the host bridge.
