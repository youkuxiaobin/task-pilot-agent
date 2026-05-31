from pydantic import SecretStr

from config.config import DBSettings, normalize_database_url


def test_bare_mysql_url_uses_pymysql_driver():
    assert (
        normalize_database_url("mysql://user:pass@127.0.0.1:3306/taskpilot")
        == "mysql+pymysql://user:pass@127.0.0.1:3306/taskpilot"
    )


def test_db_settings_default_dsn_uses_pymysql_driver():
    settings = DBSettings(
        host="127.0.0.1",
        port=3306,
        user="taskpilot",
        password=SecretStr("secret"),
        name="taskpilot",
    )

    assert settings.dsn == "mysql+pymysql://taskpilot:secret@127.0.0.1:3306/taskpilot"

