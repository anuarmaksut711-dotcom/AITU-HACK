def test_liveness_and_database_readiness_are_available_without_login(client):
    assert client.get("/api/health/live").status_code == 200
    assert client.get("/api/health/ready").status_code == 200
