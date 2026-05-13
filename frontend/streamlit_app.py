import streamlit as st
import api_client as api

st.set_page_config(page_title="Traffic Counter", page_icon="🚗", layout="wide")

st.title("Traffic Counter")
st.caption("Project-organized vehicle tracking and counting.")

# Sidebar: project picker / creator
with st.sidebar:
    st.header("Projects")
    try:
        projects = api.list_projects()
    except Exception as e:
        st.error(f"API unreachable: {e}")
        st.stop()

    project_map = {p["name"]: p for p in projects}
    selected_name = st.selectbox(
        "Active project",
        options=["— select —"] + list(project_map.keys()),
        key="selected_project_name",
    )
    if selected_name and selected_name != "— select —":
        st.session_state["project"] = project_map[selected_name]
    else:
        st.session_state.pop("project", None)

    st.divider()
    with st.expander("Create new project"):
        with st.form("create_project_form", clear_on_submit=True):
            name = st.text_input("Name")
            desc = st.text_area("Description (optional)")
            if st.form_submit_button("Create") and name.strip():
                try:
                    api.create_project(name.strip(), desc.strip())
                    st.success(f"Created {name}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

# Main pane
proj = st.session_state.get("project")
if not proj:
    st.info("Pick or create a project in the sidebar to begin.")
    if projects:
        st.subheader("All projects")
        for p in projects:
            with st.container(border=True):
                st.write(f"**{p['name']}**")
                if p.get("description"):
                    st.caption(p["description"])
                st.caption(f"Created {p['created_at']}")
    st.stop()

st.subheader(f"Project: {proj['name']}")
if proj.get("description"):
    st.caption(proj["description"])

st.markdown("""
**Workflow**

1. **Videos** — upload videos taken from the same camera position. Trigger analysis on each.
2. **Count & export** — once videos are analyzed, draw counting lines on the trajectory overlay,
   see live counts per line, and export to Excel.

Use the pages in the left navigation.
""")

col1, col2, col3 = st.columns(3)
videos = api.list_videos(proj["id"])
lines = api.list_lines(proj["id"])
col1.metric("Videos in project", len(videos))
col2.metric("Analyzed", sum(1 for v in videos if v["status"] == "analyzed"))
col3.metric("Counting lines", len(lines))

st.divider()
if st.button("Delete project", type="secondary"):
    confirm_key = f"confirm_del_{proj['id']}"
    if st.session_state.get(confirm_key):
        api.delete_project(proj["id"])
        st.session_state.pop("project", None)
        st.session_state.pop(confirm_key, None)
        st.rerun()
    else:
        st.session_state[confirm_key] = True
        st.warning("Click again to confirm — this deletes all videos and lines in the project.")
