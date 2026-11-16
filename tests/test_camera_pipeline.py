"""Exercise actual CPU inference through a simulated capture device, no webcam."""
import importlib.util
import unittest
from unittest.mock import patch

from aic_hand.capture import Failure, Sample, Worker
from aic_hand.config import load

HAS_VISION = all(importlib.util.find_spec(name) is not None for name in ("cv2", "mediapipe", "numpy"))


@unittest.skipUnless(HAS_VISION, "Install requirements.txt for real model/capture pipeline checks")
class CameraPipelineTests(unittest.TestCase):
    def test_actual_streaming_model_with_one_simulated_blank_capture(self):
        import numpy as np
        worker = Worker(load())
        class SimulatedCamera:
            def __init__(self):
                self.reads, self.released = 0, False
            def isOpened(self):
                return True
            def set(self, *arguments):
                return True
            def read(self):
                self.reads += 1
                if self.reads == 1:
                    return True, np.zeros((480, 640, 3), dtype=np.uint8)
                worker.stop()
                return False, None
            def release(self):
                self.released = True
        camera = SimulatedCamera()
        with patch("cv2.VideoCapture", return_value=camera):
            worker.start()
            worker.join(timeout=30)
        try:
            self.assertFalse(worker.is_alive())
            item = worker.latest()
            self.assertIsInstance(item, Sample, item)
            self.assertEqual(item.observation.hands, ())
            self.assertEqual((item.observation.width, item.observation.height), (640, 480))
            self.assertEqual(item.observation.frame_id, 1)
            self.assertGreater(item.observation.inference_ms, 0)
            self.assertTrue(camera.released)
        finally:
            worker.stop()
            worker.join(timeout=1)

    def test_camera_open_failure_is_reported_and_device_released(self):
        from unittest.mock import Mock
        camera = Mock()
        camera.isOpened.return_value = False
        worker = Worker(load())
        with patch("cv2.VideoCapture", return_value=camera):
            worker.start()
            worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        message = worker.latest()
        self.assertIsInstance(message, Failure)
        self.assertIn("无法打开摄像头", message.message)
        camera.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
