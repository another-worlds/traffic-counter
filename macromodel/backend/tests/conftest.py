import os
import sys
import tempfile

# Put backend/ (which contains the `app` package) on the path for `from app... import`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# DB-touching tests use a throwaway SQLite DB and temp storage unless the environment
# (Docker / CI) overrides them. The ORM uses portable column types, so SQLite stands in
# for PostGIS here. Set before any `app` import so the engine picks these up.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("STORAGE_ROOT", tempfile.mkdtemp())
os.environ.setdefault("TRAFFIC_COUNTER_API_URL", "http://127.0.0.1:59999")  # unreachable on purpose
