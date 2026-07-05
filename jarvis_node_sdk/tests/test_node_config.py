"""Node config sourcing tests (Priority #4 Milestone 8) -- the production
entrypoint's fail-closed configuration loader.
"""

from __future__ import annotations

import pytest

from jarvis_node_sdk.node_config import NodeConfigError, load_node_config

_FULL = {
    "CHIMERA_NODE_ID": "jarvis-core-pc",
    "CHIMERA_HMAC_KEY": "f" * 64,
}


def test_minimal_env_gives_defaults() -> None:
    cfg = load_node_config(env=_FULL)

    assert cfg.node_id == "jarvis-core-pc"
    assert cfg.hmac_key == "f" * 64
    assert cfg.broker_host == "127.0.0.1"
    assert cfg.broker_port == 1883
    assert cfg.ceilings_path is None


def test_missing_node_id_refuses_to_start() -> None:
    with pytest.raises(NodeConfigError, match="CHIMERA_NODE_ID"):
        load_node_config(env={"CHIMERA_HMAC_KEY": "x"})


def test_missing_hmac_key_refuses_to_start() -> None:
    with pytest.raises(NodeConfigError, match="CHIMERA_HMAC_KEY"):
        load_node_config(env={"CHIMERA_NODE_ID": "n"})


def test_bad_port_refuses_to_start() -> None:
    with pytest.raises(NodeConfigError, match="CHIMERA_BROKER_PORT"):
        load_node_config(env={**_FULL, "CHIMERA_BROKER_PORT": "not-a-port"})


def test_overrides_and_ceilings() -> None:
    cfg = load_node_config(
        env={
            **_FULL,
            "CHIMERA_BROKER_HOST": "100.64.0.1",
            "CHIMERA_BROKER_PORT": "8883",
            "CHIMERA_CEILINGS": "C:/chimera/ceilings.json",
        }
    )

    assert cfg.broker_host == "100.64.0.1"
    assert cfg.broker_port == 8883
    assert cfg.ceilings_path == "C:/chimera/ceilings.json"


def test_env_file_seeds_missing_but_real_env_wins(tmp_path) -> None:
    env_file = tmp_path / "node.env"
    env_file.write_text(
        "# comment line\n"
        "CHIMERA_NODE_ID=file-node\n"
        "CHIMERA_HMAC_KEY=file-key\n"
        "CHIMERA_BROKER_HOST=file-host\n"
    )

    cfg = load_node_config(
        env={"CHIMERA_NODE_ID": "real-node"}, env_file=str(env_file)
    )

    assert cfg.node_id == "real-node"  # real environment wins
    assert cfg.hmac_key == "file-key"  # file seeds what's missing
    assert cfg.broker_host == "file-host"


def test_missing_env_file_is_an_error_not_silent() -> None:
    with pytest.raises(NodeConfigError, match="env file not found"):
        load_node_config(env=_FULL, env_file="Z:/nope/node.env")
