import socket

import pytest


def test_network_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="blocked"):
        socket.create_connection(("example.com", 443), timeout=1)
