import pytest

from medextract.config import ConfigError, load_settings, read_env_file


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "GEMINI_THINKING_LEVEL", "MEDEXTRACT_NO_CACHE",
                 "GEMINI_TIMEOUT_S", "GEMINI_MAX_RETRIES"]:
        monkeypatch.delenv(name, raising=False)


def test_env_file_parsing(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\n\nGEMINI_API_KEY="abc"\nexport GEMINI_MODEL=m-2\nBROKEN LINE\n', encoding="utf-8")
    assert read_env_file(env) == {"GEMINI_API_KEY": "abc", "GEMINI_MODEL": "m-2"}


def test_inline_comments_are_stripped(tmp_path):
    env = tmp_path / ".env"
    env.write_text('GEMINI_TIMEOUT_S=90       # per request\nGEMINI_API_KEY="a#b"\n', encoding="utf-8")
    assert read_env_file(env) == {"GEMINI_TIMEOUT_S": "90", "GEMINI_API_KEY": "a#b"}


def test_timeout_and_retries_from_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=k\nGEMINI_TIMEOUT_S=45 # short\nGEMINI_MAX_RETRIES=2\n", encoding="utf-8")
    settings = load_settings(env)
    assert settings.timeout_s == 45.0 and settings.max_retries == 2


def test_bad_timeout_is_a_config_error(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=k\nGEMINI_TIMEOUT_S=soon\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="GEMINI_TIMEOUT_S"):
        load_settings(env)


def test_settings_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=abc\nGEMINI_MODEL=m-2\n", encoding="utf-8")
    settings = load_settings(env)
    assert settings.api_key == "abc" and settings.model == "m-2"


def test_environment_wins_over_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert load_settings(env).api_key == "from-env"


def test_missing_key_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        load_settings(tmp_path / "missing.env")


def test_repr_never_shows_the_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=super-secret\n", encoding="utf-8")
    assert "super-secret" not in repr(load_settings(env))


def test_cache_can_be_disabled(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=k\n", encoding="utf-8")
    monkeypatch.setenv("MEDEXTRACT_NO_CACHE", "1")
    assert load_settings(env).cache_dir is None
