"""Integration tests for pod restoration functionality."""
import pytest
import asyncio
from kubernetes_asyncio import client
from kubernetes_asyncio.client.rest import ApiException
from src.main import on_pod_gone, is_offline

@pytest.mark.asyncio
async def test_pod_restore_offline(mock_api, mock_gossip, mock_cache):
    """Test pod restoration when control plane is offline."""
    # Setup
    mock_api.get_api_resources.side_effect = ApiException(status=503)
    mock_gossip.healthy_peers.return_value = {
        "test-node": 4,
        "other-node": 2
    }
    
    # Execute
    await on_pod_gone(
        meta={"uid": "test-uid"},
        namespace="default",
        name="test-pod"
    )
    
    # Verify
    mock_api.create_namespaced_pod_binding.assert_called_once()
    call_args = mock_api.create_namespaced_pod_binding.call_args[1]
    assert call_args["name"] == "test-pod"
    assert call_args["namespace"] == "default"
    assert call_args["target"].name == "test-node"

@pytest.mark.asyncio
async def test_pod_restore_online(mock_api, mock_gossip, mock_cache):
    """Test that pod restoration is skipped when control plane is online."""
    # Setup
    mock_api.get_api_resources.return_value = None  # API is reachable
    
    # Execute
    await on_pod_gone(
        meta={"uid": "test-uid"},
        namespace="default",
        name="test-pod"
    )
    
    # Verify
    mock_api.create_namespaced_pod_binding.assert_not_called()

@pytest.mark.asyncio
async def test_control_plane_offline_detection(mock_api):
    """Test detection of control plane offline state."""
    # Setup
    mock_api.get_api_resources.side_effect = ApiException(status=503)
    
    # Execute
    offline = await is_offline()
    
    # Verify
    assert offline is True

@pytest.mark.asyncio
async def test_control_plane_online_detection(mock_api):
    """Test detection of control plane online state."""
    # Setup
    mock_api.get_api_resources.return_value = None
    
    # Execute
    offline = await is_offline()
    
    # Verify
    assert offline is False 