"""Unit tests for the scheduler module."""
import pytest
from kubernetes_asyncio.client.rest import ApiException
from src.scheduler import bid_and_bind, BindConflict

@pytest.mark.asyncio
async def test_bid_and_bind_wins(mock_api, mock_gossip):
    """Test successful bid and bind when our node has highest CPU."""
    # Setup
    mock_gossip.healthy_peers.return_value = {
        "test-node": 4,  # Our node has highest CPU
        "other-node": 2
    }
    
    # Execute
    await bid_and_bind(mock_api, mock_gossip, {}, "default", "test-pod")
    
    # Verify
    mock_api.create_namespaced_pod_binding.assert_called_once()
    call_args = mock_api.create_namespaced_pod_binding.call_args[1]
    assert call_args["name"] == "test-pod"
    assert call_args["namespace"] == "default"
    assert call_args["target"].name == "test-node"

@pytest.mark.asyncio
async def test_bid_and_bind_loses(mock_api, mock_gossip):
    """Test bid loss when another node has higher CPU."""
    # Setup
    mock_gossip.healthy_peers.return_value = {
        "test-node": 2,
        "other-node": 4  # Other node has higher CPU
    }
    
    # Execute
    await bid_and_bind(mock_api, mock_gossip, {}, "default", "test-pod")
    
    # Verify
    mock_api.create_namespaced_pod_binding.assert_not_called()

@pytest.mark.asyncio
async def test_bid_and_bind_conflict(mock_api, mock_gossip):
    """Test handling of binding conflicts."""
    # Setup
    mock_gossip.healthy_peers.return_value = {
        "test-node": 4,
        "other-node": 2
    }
    mock_api.create_namespaced_pod_binding.side_effect = ApiException(status=409)
    
    # Execute and verify
    with pytest.raises(BindConflict):
        await bid_and_bind(mock_api, mock_gossip, {}, "default", "test-pod") 