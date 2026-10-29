"""Without the `data` extra, /v1/data/* still exists (the contract doesn't depend on extras) and answers 501."""


def test_unavailable_router_answers_501():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from shipcube_api import data_layer

    app = FastAPI()
    app.include_router(data_layer.unavailable_router(), prefix="/v1/data")
    with TestClient(app) as c:
        for method, path in data_layer.PATHS:
            r = c.request(method, "/v1/data" + path.replace("{name}", "orders").replace("{id}", "0006_x"))
            assert r.status_code == 501 and r.json()["detail"]["code"] == "data_unavailable", (method, path)
