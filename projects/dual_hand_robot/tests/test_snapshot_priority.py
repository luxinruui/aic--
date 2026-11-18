"""Disk snapshots cannot delay publishing a fresh command or a fault STOP."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from left_hand_drive.core import Controller
from left_hand_drive.dual_gui import RobotApp
from left_hand_drive.gui import App
from dual_link.profile import AXES


class SnapshotPriorityTests(unittest.TestCase):
    def fixture(self):
        app = RobotApp.__new__(RobotApp)
        app.client = Mock()
        app.input_session = 'i'*32
        upper = SimpleNamespace(input_valid=True,enabled=True,source='camera',frame_id=1,
                                sample_age_ms=0.,targets=dict.fromkeys(AXES,0.),active_groups=(True,)*5)
        app.upper_preview = SimpleNamespace(controller=SimpleNamespace(command=Mock(return_value=upper)),
                                            mode=SimpleNamespace(get=lambda:'仅上肢'))
        command = replace(Controller().command(100),enabled=True,input_valid=True,source='camera',frame_id=1)
        return app,command

    def test_publish_precedes_potentially_blocking_disk_write(self):
        app,command = self.fixture()
        def write(_self,_command):
            app.client.publish.assert_called_once()
            self.assertTrue(app.client.publish.call_args.args[0]['enabled'])
        with patch.object(App,'write_snapshot',write):
            app.write_snapshot(command)

    def test_disk_error_immediately_publishes_off_without_waiting_for_next_gui_tick(self):
        app,command = self.fixture()
        with patch.object(App,'write_snapshot',side_effect=OSError('locked snapshot')):
            with self.assertRaises(OSError): app.write_snapshot(command)
        self.assertEqual(app.client.publish.call_count,2)
        stopped = app.client.publish.call_args.args[0]
        self.assertFalse(stopped['enabled'])
        self.assertEqual((stopped['forward'],stopped['yaw_left']),(0.,0.))
        self.assertTrue(all(value is None for value in stopped['targets'].values()))


if __name__ == '__main__': unittest.main()
