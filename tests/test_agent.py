import io
import zipfile

import pytest

from mimic import agent


def _names(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return set(zf.namelist())


def test_workspace_archive_filters_common_secrets_and_noise(tmp_path):
    (tmp_path / "app.py").write_text("print('ok')")
    (tmp_path / ".env").write_text("TOKEN=secret")
    (tmp_path / "client.pem").write_text("secret")
    (tmp_path / "credentials.json").write_text('{"secret": true}')
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret")
    names = _names(agent.build_workspace_zip(tmp_path))
    assert "app.py" in names
    assert ".env" not in names
    assert "client.pem" not in names
    assert "credentials.json" not in names
    assert ".git/config" not in names


def test_invalid_container_size_is_rejected_before_api_call(tmp_path):
    with pytest.raises(ValueError, match="container_size"):
        agent.run_agent("test", path=tmp_path, container_size="huge", client=object())


def test_empty_task_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="cannot be empty"):
        agent.run_agent("   ", path=tmp_path, client=object())
