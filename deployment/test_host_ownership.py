import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import image_pipeline


class HostOwnershipTests(unittest.TestCase):
    """The scheduler may run as root (cron on OpenWrt) while stage containers run as worker_uid."""

    def test_directories_made_by_the_host_are_handed_to_the_worker(self):
        project = {"worker_uid": 1200, "worker_gid": 1201}
        with tempfile.TemporaryDirectory() as temp, patch.object(image_pipeline.os, "chown") as chown:
            image_pipeline._own(Path(temp), project)
        chown.assert_called_once_with(Path(temp), 1200, 1201)

    def test_non_root_scheduler_keeps_working(self):
        project = {"worker_uid": 1200, "worker_gid": 1201}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(image_pipeline.os, "chown", side_effect=PermissionError):
            image_pipeline._own(Path(temp), project)  # must not raise

    def test_private_json_owns_a_new_parent_directory(self):
        project = {"worker_uid": 1200, "worker_gid": 1201}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(image_pipeline.settings, "load_project", return_value=project), \
             patch.object(image_pipeline.os, "chown") as chown:
            target = Path(temp) / "job" / "result.json"
            image_pipeline._private_json(target, {"ok": True})
        self.assertIn(((Path(temp) / "job"), 1200, 1201), [call.args for call in chown.call_args_list])


if __name__ == "__main__":
    unittest.main()
