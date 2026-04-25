from api import config


def test_is_config_complete_reflects_missing_values(monkeypatch):
    monkeypatch.setattr(config, 'get_missing_config', lambda: ['TG_TOKEN'])

    assert config.is_config_complete() is False


def test_is_config_complete_returns_true_when_nothing_missing(monkeypatch):
    monkeypatch.setattr(config, 'get_missing_config', lambda: [])

    assert config.is_config_complete() is True
