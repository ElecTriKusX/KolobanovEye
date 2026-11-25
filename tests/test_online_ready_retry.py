import unittest
from unittest.mock import Mock

from tools.ke_online_capture import await_value, read_ready_acknowledgement


class OnlineReadyRetryTests(unittest.TestCase):
    def test_missing_locked_partial_then_ready(self):
        path = Mock()
        path.read_bytes.side_effect = [
            FileNotFoundError(), PermissionError(13, "sharing violation"), b"", b"ok\n"]
        process = Mock()
        process.poll.return_value = None
        consume = Mock(return_value={"index": 23})

        def completed():
            if not read_ready_acknowledgement(path):
                return None
            return consume()

        self.assertEqual(await_value(process, completed, 1, "frame 23"), {"index": 23})
        self.assertEqual(path.read_bytes.call_count, 4)
        consume.assert_called_once_with()

    def test_non_transient_io_error_is_not_hidden(self):
        path = Mock()
        path.read_bytes.side_effect = OSError(5, "disk error")
        with self.assertRaises(OSError):
            read_ready_acknowledgement(path)
