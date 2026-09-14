"""健康检查（供 docker compose verify 服务做启动探测）。"""


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
