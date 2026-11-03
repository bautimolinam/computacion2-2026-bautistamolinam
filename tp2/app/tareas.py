"""Implementación de los tres tipos de tarea.

Cada ejecutor es una corrutina `async def f(params, ctx) -> resultado`.
`ctx` es el Gestor, de donde sacan el executor de procesos.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .gestor import Gestor

# Cuántas iteraciones de SHA-256 se mandan al executor por vez. Partir el
# trabajo en bloques permite cancelar entre bloque y bloque: un proceso que ya
# está calculando no se puede interrumpir, pero sí podemos no mandarle el
# siguiente bloque. Con 200k iteraciones cada bloque tarda ~0.1-0.2 s.
BLOQUE_HASH = 200_000


# ------------------------------------------------------------------ hashear

def hash_iterado(datos: bytes, n: int) -> bytes:
    """Aplica SHA-256 n veces. Función pura y CPU-bound.

    Está a nivel de módulo (no es una lambda ni un método) porque
    ProcessPoolExecutor la tiene que serializar con pickle para mandarla al
    proceso hijo, y pickle guarda las funciones por nombre importable.
    """
    h = datos
    for _ in range(n):
        h = hashlib.sha256(h).digest()
    return h


async def hashear(params: dict[str, Any], ctx: "Gestor") -> dict[str, Any]:
    datos = params["texto"].encode()
    total = params["iteraciones"]
    modo = params.get("modo", "executor")
    loop = asyncio.get_running_loop()

    t0 = time.perf_counter()
    if modo == "bloqueante":
        # BUG A PROPÓSITO (medición 2): no hay ningún await, así que el event
        # loop no recupera el control hasta terminar. Ninguna otra corrutina,
        # ni siquiera el servidor HTTP, avanza mientras tanto.
        datos = hash_iterado(datos, total)
    else:
        restantes = total
        while restantes > 0:
            n = min(BLOQUE_HASH, restantes)
            if modo == "hilo":
                datos = await asyncio.to_thread(hash_iterado, datos, n)
            else:
                datos = await loop.run_in_executor(
                    ctx.executor, hash_iterado, datos, n
                )
            restantes -= n
    return {
        "hash": datos.hex(),
        "iteraciones": total,
        "modo": modo,
        "segundos": round(time.perf_counter() - t0, 4),
    }


# ---------------------------------------------------------------- descargar

async def _descargar_una(cliente: httpx.AsyncClient, url: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        resp = await cliente.get(url)
        return {
            "url": url,
            "status": resp.status_code,
            "bytes": len(resp.content),
            "segundos": round(time.perf_counter() - t0, 4),
        }
    except httpx.HTTPError as e:
        # Un error en una URL no tumba al resto: queda registrado en su fila.
        return {
            "url": url,
            "status": None,
            "error": f"{type(e).__name__}: {e}",
            "segundos": round(time.perf_counter() - t0, 4),
        }


async def descargar(params: dict[str, Any], ctx: "Gestor") -> dict[str, Any]:
    urls: list[str] = params["urls"]
    t0 = time.perf_counter()
    # `async with` garantiza que el cliente cierre sus conexiones incluso si la
    # tarea se cancela a mitad de camino (CancelledError atraviesa el bloque y
    # __aexit__ se ejecuta igual).
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(ctx.timeout_http),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=ctx.max_conexiones),
    ) as cliente:
        if params.get("secuencial"):
            # for + await: cada descarga empieza cuando terminó la anterior.
            resultados = [await _descargar_una(cliente, u) for u in urls]
        else:
            # TaskGroup (3.11): lanza todas a la vez y, si la tarea padre se
            # cancela, cancela a todas las hijas y espera a que terminen.
            # Nada queda huérfano.
            async with asyncio.TaskGroup() as tg:
                hijas = [tg.create_task(_descargar_una(cliente, u)) for u in urls]
            resultados = [h.result() for h in hijas]
    return {
        "modo": "secuencial" if params.get("secuencial") else "concurrente",
        "total_segundos": round(time.perf_counter() - t0, 4),
        "ok": sum(1 for r in resultados if r.get("status") is not None),
        "resultados": resultados,
    }


# ------------------------------------------------------------------ esperar

async def esperar(params: dict[str, Any], ctx: "Gestor") -> dict[str, Any]:
    # asyncio.sleep cede el control al loop; time.sleep lo congelaría.
    await asyncio.sleep(params["segundos"])
    return {"durmio": params["segundos"]}


EJECUTORES = {
    "descargar": descargar,
    "hashear": hashear,
    "esperar": esperar,
}
