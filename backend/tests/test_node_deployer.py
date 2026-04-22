from datetime import datetime, timezone

import pytest

from backend.models.upstream_node import NodePeer, NodeStatus, ProvisioningMode, UpstreamNode
from backend.services.node_deployer import _get_node, _make_env_content, _make_node_server_config


@pytest.mark.asyncio
async def test_get_node_eager_loads_shared_peers(db_session) -> None:
    node = UpstreamNode(
        name="test-node",
        host="203.0.113.10",
        ssh_port=22,
        awg_port=51821,
        provisioning_mode=ProvisioningMode.managed,
        awg_address="10.20.0.3/32",
        private_key="node-private-key",
        public_key="node-public-key",
        status=NodeStatus.online,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(node)
    await db_session.flush()

    peer = NodePeer(
        node_id=node.id,
        name="shared-peer-1",
        private_key="peer-private-key",
        public_key="peer-public-key",
        tunnel_address="10.30.0.2/32",
        allowed_ips="0.0.0.0/0",
        persistent_keepalive=25,
        enabled=True,
    )
    db_session.add(peer)
    await db_session.commit()

    loaded = await _get_node(node.id, db_session)

    shared_peers = list(loaded.shared_peers)
    assert len(shared_peers) == 1
    assert shared_peers[0].name == "shared-peer-1"
    assert shared_peers[0].tunnel_address == "10.30.0.2/32"


def test_make_env_content_uses_24_mask_for_node_interface() -> None:
    env_content = _make_env_content(
        private_key="node-private-key",
        awg_address="10.20.0.3/32",
        awg_port=51821,
    )

    assert "AWG_ADDRESS=10.20.0.3/24" in env_content
    assert "AWG_ADDRESS=10.20.0.3/32" not in env_content
