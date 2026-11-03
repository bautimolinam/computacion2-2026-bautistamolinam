"""Tests de la API con el TestClient de FastAPI (corre el lifespan)."""
import time

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setitem(main.CONFIG, "dir_datos", str(tmp_path))
    monkeypatch.setitem(main.CONFIG, "cpu_workers", 1)
    with TestClient(main.app) as c:
        yield c


def esperar_fin(c, tid, limite=5):
    t0 = time.time()
    while time.time() - t0 < limite:
        d = c.get(f"/tareas/{tid}").json()
        if d["estado"] in ("completada", "fallida", "cancelada"):
            return d
        time.sleep(0.02)
    raise AssertionError("no terminó")


def test_post_responde_inmediato(cliente):
    t0 = time.perf_counter()
    r = cliente.post("/tareas", json={"tipo": "esperar", "params": {"segundos": 2}})
    assert r.status_code == 202
    assert time.perf_counter() - t0 < 0.5
    assert r.json()["estado"] == "pendiente"
    cliente.delete(f"/tareas/{r.json()['id']}")


def test_validacion(cliente):
    assert cliente.post("/tareas", json={"tipo": "x", "params": {}}).status_code == 422
    r = cliente.post("/tareas", json={"tipo": "hashear", "params": {"texto": "a"}})
    assert r.status_code == 422
    assert cliente.get("/tareas/noexiste").status_code == 404


def test_flujo_completo_y_filtros(cliente):
    a = cliente.post("/tareas", json={"tipo": "esperar", "params": {"segundos": 0}}).json()
    b = cliente.post("/tareas", json={"tipo": "esperar", "params": {"segundos": 30}}).json()
    assert esperar_fin(cliente, a["id"])["estado"] == "completada"
    time.sleep(0.05)
    r = cliente.delete(f"/tareas/{b['id']}")
    assert r.status_code == 200 and r.json()["estado"] == "cancelada"
    assert cliente.delete(f"/tareas/{b['id']}").status_code == 409
    canceladas = cliente.get("/tareas", params={"estado": "cancelada"}).json()
    assert [t["id"] for t in canceladas["tareas"]] == [b["id"]]
    s = cliente.get("/estadisticas").json()
    assert s["por_estado"]["completada"] == 1 and s["por_estado"]["cancelada"] == 1
    assert "tp2_tareas" in cliente.get("/metrics").text


def test_websocket_eventos(cliente):
    with cliente.websocket_connect("/eventos") as ws:
        tid = cliente.post("/tareas", json={"tipo": "esperar",
                                            "params": {"segundos": 0}}).json()["id"]
        estados = [ws.receive_json()["estado"] for _ in range(3)]
        assert estados == ["pendiente", "ejecutando", "completada"]
        assert esperar_fin(cliente, tid)["estado"] == "completada"
