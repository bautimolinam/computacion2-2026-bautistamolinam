"""API HTTP (FastAPI) de la plataforma de tareas distribuidas."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response

from .gestor import ColaLlena, Gestor, NoAcepta, NoCancelable
from .modelos import Estado, NuevaTarea

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("tp2.api")


def _env_int(nombre: str, default: int) -> int:
    return int(os.getenv(nombre, default))


def _env_float(nombre: str, default: float) -> float:
    return float(os.getenv(nombre, default))


CONFIG = {
    "n_workers": _env_int("WORKERS", 10),
    "cpu_workers": _env_int("CPU_WORKERS", 0) or None,
    "cola_max": _env_int("COLA_MAX", 10_000),
    "timeout_default": _env_float("TIMEOUT_DEFAULT", 60.0),
    "dir_datos": os.getenv("DIR_DATOS", "datos"),
    "timeout_http": _env_float("TIMEOUT_HTTP", 15.0),
    "max_conexiones": _env_int("MAX_CONEXIONES", 100),
}
LIMITE_APAGADO = _env_float("LIMITE_APAGADO", 10.0)


def _instalar_senales(app: FastAPI, gestor: Gestor) -> None:
    """SIGTERM/SIGINT -> apagado ordenado.

    `loop.add_signal_handler` ejecuta el handler DENTRO del event loop (como
    un callback más), no interrumpiendo código arbitrario como `signal.signal`.
    Reemplaza el handler que uvicorn instaló: dejamos de aceptar tareas YA y
    le pedimos a uvicorn que salga; uvicorn deja de aceptar conexiones y corre
    el shutdown del lifespan, que es donde esperamos y volcamos el estado.
    """
    server: uvicorn.Server | None = getattr(app.state, "server", None)
    if server is None:
        return  # lanzado con `uvicorn app.main:app` o en tests: uvicorn maneja
    loop = asyncio.get_running_loop()

    def al_recibir(sig: signal.Signals) -> None:
        if server.should_exit:
            log.warning("Segunda señal %s: salida forzada", sig.name)
            server.force_exit = True
            return
        log.info("Recibida %s: iniciando apagado ordenado", sig.name)
        gestor.aceptando = False
        server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, al_recibir, sig)
        except (NotImplementedError, RuntimeError, ValueError):
            # Windows sin Docker: no existe add_signal_handler. Queda el
            # handler de uvicorn, que igual dispara el shutdown del lifespan.
            log.warning("add_signal_handler no disponible para %s", sig.name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    gestor = Gestor(**CONFIG)
    await gestor.iniciar()
    app.state.gestor = gestor
    _instalar_senales(app, gestor)
    yield
    archivo = await gestor.apagar(LIMITE_APAGADO)
    log.info("Apagado completo. Estado en %s", archivo)


app = FastAPI(
    title="TP2 - Plataforma de Tareas Distribuidas",
    description="Computación II - Universidad de Mendoza - 2026",
    version="1.0.0",
    lifespan=lifespan,
)


def _g(request: Request) -> Gestor:
    return request.app.state.gestor


# ------------------------------------------------------------------ tareas

@app.post("/tareas", status_code=202)
async def crear_tarea(nueva: NuevaTarea, request: Request):
    """Encola y responde al instante (202 Accepted): no espera la ejecución."""
    try:
        t = _g(request).encolar(nueva.tipo, nueva.params.model_dump(), nueva.timeout)
    except NoAcepta:
        raise HTTPException(503, "El servidor se está apagando") from None
    except ColaLlena:
        raise HTTPException(429, "Cola llena, reintentá más tarde") from None
    return {"id": t.id, "estado": t.estado.value, "url": f"/tareas/{t.id}"}


@app.get("/tareas")
async def listar_tareas(
    request: Request,
    estado: Estado | None = None,
    tipo: str | None = None,
    limite: int = Query(1000, ge=1, le=100_000),
    con_resultado: bool = False,
):
    tareas = _g(request).listar(estado, tipo)
    return {
        "total": len(tareas),
        "tareas": [t.a_dict(con_resultado) for t in tareas[-limite:]],
    }


@app.get("/tareas/{tid}")
async def ver_tarea(tid: str, request: Request):
    t = _g(request).tareas.get(tid)
    if t is None:
        raise HTTPException(404, "No existe la tarea")
    return t.a_dict()


@app.delete("/tareas/{tid}")
async def cancelar_tarea(tid: str, request: Request):
    try:
        t = await _g(request).cancelar(tid)
    except KeyError:
        raise HTTPException(404, "No existe la tarea") from None
    except NoCancelable as e:
        raise HTTPException(409, f"La tarea ya terminó (estado: {e})") from None
    return t.a_dict()


@app.get("/estadisticas")
async def estadisticas(request: Request):
    return _g(request).estadisticas()


# ------------------------------------------------------------------ bonus

@app.get("/metrics", response_class=PlainTextResponse)
async def metricas(request: Request):
    """Métricas en formato de exposición de Prometheus."""
    s = _g(request).estadisticas()
    lineas = [
        "# HELP tp2_tareas Cantidad de tareas por estado",
        "# TYPE tp2_tareas gauge",
        *(f'tp2_tareas{{estado="{k}"}} {v}' for k, v in s["por_estado"].items()),
        "# HELP tp2_workers_ocupados Workers ejecutando una tarea",
        "# TYPE tp2_workers_ocupados gauge",
        f"tp2_workers_ocupados {s['workers']['ocupados']}",
        "# HELP tp2_workers_total Tamaño del pool",
        "# TYPE tp2_workers_total gauge",
        f"tp2_workers_total {s['workers']['total']}",
        "# HELP tp2_cola_longitud Tareas esperando en la cola",
        "# TYPE tp2_cola_longitud gauge",
        f"tp2_cola_longitud {s['en_cola']}",
        "# HELP tp2_duracion_promedio_segundos Duración media de completadas",
        "# TYPE tp2_duracion_promedio_segundos gauge",
        *(f'tp2_duracion_promedio_segundos{{tipo="{k}"}} {v}'
          for k, v in s["tiempo_promedio_por_tipo"].items()),
    ]
    return "\n".join(lineas) + "\n"


@app.websocket("/eventos")
async def eventos(ws: WebSocket):
    """Notifica en tiempo real cada cambio de estado de cualquier tarea."""
    await ws.accept()
    gestor: Gestor = ws.app.state.gestor
    q = gestor.suscribir()
    try:
        while True:
            evento = await q.get()
            if evento is None:  # el servidor se apaga
                break
            await ws.send_json(evento)
    except WebSocketDisconnect:
        pass
    finally:
        gestor.desuscribir(q)
        try:
            await ws.close()
        except RuntimeError:
            pass  # ya estaba cerrado


# ------------------------------------------------------- auxiliares de prueba

@app.get("/lento")
async def lento(segundos: float = Query(0.5, ge=0, le=30),
                kb: int = Query(10, ge=0, le=10_000)):
    """Endpoint que tarda `segundos` en responder `kb` KB. Sirve como
    servidor lento reproducible para la medición de descargas."""
    await asyncio.sleep(segundos)
    return Response(b"x" * (kb * 1024), media_type="application/octet-stream")


@app.get("/salud")
async def salud():
    return {"ok": True}


def main() -> None:
    config = uvicorn.Config(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=_env_int("PORT", 8000),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        timeout_graceful_shutdown=int(LIMITE_APAGADO) + 5,
    )
    server = uvicorn.Server(config)
    app.state.server = server
    server.run()


# Guarda obligatoria: con "spawn" cada proceso hijo del ProcessPoolExecutor
# re-importa este módulo. Sin el if, cada hijo levantaría otro servidor.
if __name__ == "__main__":
    main()
