import json
import pytest
import config
from test_api import client


def test_save_directory_is_restart_only(tmp_path,monkeypatch):
    monkeypatch.delenv('SUBMIT_RESUME_DATA',raising=False)
    monkeypatch.setattr(config,'LOCAL_CONFIG',tmp_path/'workspace.local.json')
    active=config.DATA
    target=tmp_path/'new workspace'
    assert config.save_directory(str(target))==str(target.resolve())
    assert config.DATA==active
    assert config.saved_directory()==str(target.resolve())
    assert config.LOCAL_CONFIG.is_file()


def test_directory_validation(tmp_path,monkeypatch):
    monkeypatch.delenv('SUBMIT_RESUME_DATA',raising=False)
    monkeypatch.setattr(config,'LOCAL_CONFIG',tmp_path/'workspace.local.json')
    with pytest.raises(ValueError):config.save_directory('relative')
    with pytest.raises(ValueError):config.save_directory(str(config.ROOT))
    target=tmp_path/'nonempty';target.mkdir();(target/'unrelated.txt').write_text('retain')
    with pytest.raises(ValueError):config.save_directory(str(target))
    assert (target/'unrelated.txt').read_text()=='retain'
    monkeypatch.setenv('SUBMIT_RESUME_DATA',str(target))
    with pytest.raises(ValueError):config.save_directory(str(tmp_path/'other'))


def test_workspace_api_and_greeting_validation(client,tmp_path,monkeypatch):
    monkeypatch.delenv('SUBMIT_RESUME_DATA',raising=False)
    monkeypatch.setattr(config,'LOCAL_CONFIG',tmp_path/'workspace.local.json')
    response=client.put('/api/settings/workspace',json={'data_dir':str(tmp_path/'new')})
    assert response.status_code==200 and response.json()['restart_required']
    assert client.get('/api/settings').json()['next_data_dir']==str(tmp_path/'new')
    for value in (0,121):
        assert client.post('/api/plans',json={'name':'test','keyword':'Python','max_greetings':value}).status_code==422
    client.app.state.runner.snapshot=lambda:{'busy':True}
    assert client.put('/api/settings/workspace',json={'data_dir':str(tmp_path/'other')}).status_code==400
