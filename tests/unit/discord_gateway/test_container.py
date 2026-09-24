from pathlib import Path


def test_container_uses_signal_handled_by_discord_client():
    dockerfile = Path("src/discord_gateway/Dockerfile").read_text()

    assert "STOPSIGNAL SIGINT" in dockerfile
