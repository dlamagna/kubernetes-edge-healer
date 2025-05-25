"""Common test fixtures and utilities."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from kubernetes_asyncio import client
from src.gossip import SerfGossip
from src.cache import DesiredStateCache

@pytest.fixture
def mock_api():
    """Mock Kubernetes API client."""
    api = AsyncMock(spec=client.CoreV1Api)
    return api

@pytest.fixture
def mock_gossip():
    """Mock SerfGossip instance."""
    gossip = MagicMock(spec=SerfGossip)
    gossip.node = "test-node"
    gossip.healthy_peers.return_value = {
        "test-node": 4,
        "other-node": 2
    }
    return gossip

@pytest.fixture
def mock_cache(tmp_path):
    """Mock DesiredStateCache instance."""
    cache = MagicMock(spec=DesiredStateCache)
    cache.init = AsyncMock()
    return cache

@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close() 