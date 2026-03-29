from __future__ import annotations

from fastapi.testclient import TestClient


def build_admin_session_headers(
    client: TestClient,
    *,
    api_key: str = "test-server-key",
    username: str = "admin",
    password: str = "test-admin-password",
    rotated_username: str = "ops-admin",
    rotated_password: str = "test-admin-password-rotated",
) -> dict[str, str]:
    auth_headers = {"Authorization": f"Bearer {api_key}"}

    def _login(candidate_username: str, candidate_password: str):
        return client.post(
            "/v1/admin/login",
            headers=auth_headers,
            json={"username": candidate_username, "password": candidate_password},
        )

    login_response = _login(username, password)
    if login_response.status_code == 401:
        fallback_response = _login(rotated_username, rotated_password)
        fallback_response.raise_for_status()
        fallback_payload = fallback_response.json()
        return {
            **auth_headers,
            "X-Admin-Session": fallback_payload.get("session_token", ""),
        }

    login_response.raise_for_status()
    login_payload = login_response.json()
    session_headers = {
        **auth_headers,
        "X-Admin-Session": login_payload.get("session_token", ""),
    }
    return session_headers
