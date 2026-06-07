"""Delete timestamp analysis resets artifacts and status."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import scan_control


def test_delete_scan_rejects_in_progress():
    with patch.object(scan_control, "read_scan_doc", return_value={"status": "processing"}):
        with pytest.raises(scan_control.ScanInProgress):
            scan_control.delete_scan("proj", "vid")


def test_delete_scan_clears_artifacts_and_resets_pending():
    storage = MagicMock()
    storage.exists.side_effect = lambda key: True

    with patch.object(scan_control, "get_storage", return_value=storage), \
         patch.object(scan_control, "read_scan_doc", return_value={"status": "done"}), \
         patch.object(scan_control, "delete_scan_row"), \
         patch.object(scan_control, "_artifact_flags", return_value={}), \
         patch.object(scan_control, "persist_scan"):
        result = scan_control.delete_scan("proj", "vid")

    assert storage.delete.call_count == 5
    assert result["status"] == "pending"
    assert result["auto_scan_enabled"] is True
    storage.write_json.assert_called_once()