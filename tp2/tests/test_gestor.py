"""Tests del Gestor: estados, cancelación, timeouts, robustez y no-bloqueo."""
import asyncio
import json
import time

import pytest

from app import tareas as mod_tareas
from app.gestor import ColaLlena, Gestor, NoAcepta, NoCancelable
from app.modelos import Estado


@pytest.fixture
async def gestor(tmp_path):
    g = Gestor(n_workers=3, cpu_workers=1, cola_max=50,
               timeout_default=10, dir_datos=str(tmp_path))
    await g.iniciar()
    yield g
    if g.aceptando:
        await g.apagar(limite=1)


async def esperar_estado(g, tid, estados, limite=5.0):
    t0 = time.monotonic()
    while g.tareas[tid].estado not in estados:
        if time.monotonic() - t0 > limite:
            raise AssertionError(f"estado final no alcanzado: {g.tareas[tid]}")
        await asyncio.sleep(0.01)
    return g.tareas[tid]


FINALES = {Estado.COMPLETADA, Estado.FALLIDA, Estado.CANCELADA}


async def test_esperar_completa(gestor):
    t = gestor.encolar("esperar", {"segundos": 0.05}, None)
    assert t.estado is Estado.PENDIENTE
    t = await esperar_estado(gestor, t.id, FINALES)
    assert t.estado is Estado.COMPLETADA
    assert t.resultado == {"durmio": 0.05}


async def test_cancelar_en_ejecucion(gestor):
    t = gestor.encolar("esperar", {"segundos": 30}, None)
    await esperar_estado(gestor, t.id, {Estado.EJECUTANDO})
    t0 = time.monotonic()
    await gestor.cancelar(t.id)
    assert time.monotonic() - t0 < 0.5
    assert t.estado is Estado.CANCELADA
    assert t.handle is None
    # el worker quedó libre y sigue atendiendo
    assert gestor.ocupados == 0
    t2 = gestor.encolar("esperar", {"segundos": 0}, None)
    assert (await esperar_estado(gestor, t2.id, FINALES)).estado is Estado.COMPLETADA


async def test_cancelar_pendiente(gestor):
    # 3 workers ocupados -> la cuarta queda en cola
    largas = [gestor.encolar("esperar", {"segundos": 30}, None) for _ in range(3)]
    t = gestor.encolar("esperar", {"segundos": 0}, None)
    await asyncio.sleep(0.05)
    assert t.estado is Estado.PENDIENTE
    await gestor.cancelar(t.id)
    assert t.estado is Estado.CANCELADA
    for x in largas:
        await gestor.cancelar(x.id)


async def test_cancelar_terminada_falla(gestor):
    t = gestor.encolar("esperar", {"segundos": 0}, None)
    await esperar_estado(gestor, t.id, FINALES)
    with pytest.raises(NoCancelable):
        await gestor.cancelar(t.id)


async def test_timeout_pasa_a_fallida(gestor):
    t = gestor.encolar("esperar", {"segundos": 5}, 0.1)
    t = await esperar_estado(gestor, t.id, FINALES)
    assert t.estado is Estado.FALLIDA
    assert "timeout" in t.error


async def test_cancelar_descarga_cierra_cliente(gestor, monkeypatch):
    """Cancelar a mitad de una descarga: las hijas del TaskGroup se cancelan
    y el AsyncClient se cierra (no quedan tareas huérfanas)."""
    cerrados = []
    original = mod_tareas.httpx.AsyncClient

    class Cliente(original):
        async def get(self, url, **kw):
            await asyncio.sleep(30)

        async def __aexit__(self, *a):
            cerrados.append(True)
            return await super().__aexit__(*a)

    monkeypatch.setattr(mod_tareas.httpx, "AsyncClient", Cliente)
    antes = len(asyncio.all_tasks())
    t = gestor.encolar("descargar", {"urls": ["http://a", "http://b"]}, None)
    await esperar_estado(gestor, t.id, {Estado.EJECUTANDO})
    await asyncio.sleep(0.05)
    await gestor.cancelar(t.id)
    assert t.estado is Estado.CANCELADA
    assert cerrados == [True]
    await asyncio.sleep(0.01)
    assert len(asyncio.all_tasks()) == antes


async def test_excepcion_no_mata_al_worker(gestor, monkeypatch):
    async def explota(params, ctx):
        raise ValueError("boom")

    monkeypatch.setitem(mod_tareas.EJECUTORES, "esperar", explota)
    monkeypatch.setattr("app.gestor.EJECUTORES", mod_tareas.EJECUTORES)
    t = gestor.encolar("esperar", {"segundos": 0}, None)
    t = await esperar_estado(gestor, t.id, FINALES)
    assert t.estado is Estado.FALLIDA and "boom" in t.error
    assert all(not w.done() for w in gestor.workers.values())


async def test_supervisor_relanza_worker(gestor):
    viejo = gestor.workers[0]
    viejo.cancel()  # cancelado (apagado) -> no se relanza
    await asyncio.sleep(0.01)
    assert gestor.workers[0] is viejo
    # murió por una excepción inesperada -> el supervisor lo reemplaza
    gestor._worker_terminado(0, _TareaFallida())
    assert gestor.workers[0] is not viejo and not gestor.workers[0].done()


class _TareaFallida:
    def cancelled(self):
        return False

    def exception(self):
        return RuntimeError("murió")


async def test_hashear_con_executor_no_bloquea_el_loop(gestor):
    """Mientras se hashea en el executor, el loop sigue haciendo ticks."""
    t = gestor.encolar("hashear", {"texto": "a", "iteraciones": 1_500_000}, None)
    max_hueco, ultimo = 0.0, time.perf_counter()
    while gestor.tareas[t.id].estado not in FINALES:
        await asyncio.sleep(0.01)
        ahora = time.perf_counter()
        max_hueco, ultimo = max(max_hueco, ahora - ultimo), ahora
    assert t.estado is Estado.COMPLETADA
    assert max_hueco < 0.2, f"el loop estuvo trabado {max_hueco:.3f}s"


async def test_hashear_resultado_correcto(gestor):
    import hashlib
    h = b"abc"
    for _ in range(450_000):
        h = hashlib.sha256(h).digest()
    t = gestor.encolar("hashear", {"texto": "abc", "iteraciones": 450_000}, None)
    t = await esperar_estado(gestor, t.id, FINALES, limite=20)
    assert t.resultado["hash"] == h.hex()


async def test_cola_llena(tmp_path):
    g = Gestor(n_workers=1, cpu_workers=1, cola_max=2, dir_datos=str(tmp_path))
    # sin iniciar workers: nadie consume
    g.encolar("esperar", {"segundos": 0}, None)
    g.encolar("esperar", {"segundos": 0}, None)
    with pytest.raises(ColaLlena):
        g.encolar("esperar", {"segundos": 0}, None)


async def test_apagado_ordenado_vuelca_estado(tmp_path):
    g = Gestor(n_workers=2, cpu_workers=1, dir_datos=str(tmp_path))
    await g.iniciar()
    corta = g.encolar("esperar", {"segundos": 0.2}, None)
    larga = g.encolar("esperar", {"segundos": 30}, None)
    pendiente = g.encolar("esperar", {"segundos": 0}, None)
    await asyncio.sleep(0.05)
    archivo = await g.apagar(limite=0.5)
    assert corta.estado is Estado.COMPLETADA
    assert larga.estado is Estado.CANCELADA and "apagado" in larga.error
    assert pendiente.estado is Estado.CANCELADA
    with pytest.raises(NoAcepta):
        g.encolar("esperar", {"segundos": 0}, None)
    datos = json.loads(open(archivo, encoding="utf-8").read())
    assert len(datos["tareas"]) == 3
    assert all(w.done() for w in g.workers.values())
