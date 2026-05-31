from pathlib import Path


APP_VUE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.vue"
VITE_CONFIG = Path(__file__).resolve().parents[2] / "frontend" / "vite.config.js"


def test_frontend_loads_current_user_and_generic_provider_entries():
    source = APP_VUE.read_text(encoding="utf-8")

    assert "fetch('/auth/me')" in source
    assert "fetch('/auth/providers')" in source
    assert "/auth/${encodeURIComponent(provider)}/login" in source
    assert "enabledAuthProviders" in source
    assert "v-for=\"provider in enabledAuthProviders\"" in source
    assert "providerLoginLabel(provider)" in source
    assert "fetch('/auth/logout', { method: 'POST' })" in source


def test_frontend_has_account_identity_management_entry():
    source = APP_VUE.read_text(encoding="utf-8")

    assert "fetch('/auth/users/me')" in source
    assert "/auth/${encodeURIComponent(provider)}/link" in source
    assert "unlinkIdentity(identity)" in source
    assert "availableLinkProviders" in source


def test_frontend_tasks_no_longer_send_user_id_filter():
    source = APP_VUE.read_text(encoding="utf-8")

    assert "params.set('user_id'" not in source
    assert "taskFilters.userId" not in source


def test_vite_dev_server_proxies_auth_api():
    source = VITE_CONFIG.read_text(encoding="utf-8")

    assert "'/auth': 'http://127.0.0.1:9010'" in source
