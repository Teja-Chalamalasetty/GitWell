import socket

import pytest
from pytest_socket import SocketConnectBlockedError


def test_real_network_is_blocked() -> None:
    with pytest.raises(SocketConnectBlockedError):
        socket.create_connection(("api.github.com", 443), timeout=1)
