import pytest
from orcp import ORCP
from orcp.transport import MockTransport


def make_robot(*responses: str):
    """Create an ORCP client backed by a MockTransport with pre-queued responses."""
    transport = MockTransport()
    for r in responses:
        transport.queue_response(r)
    robot = ORCP("mock://", _transport=transport)
    return robot, transport


@pytest.fixture
def mock_transport():
    return MockTransport()
