"""Las tres mediciones obligatorias del TP2.

Uso (con la API levantada, por ejemplo con `docker compose up --build`):

    python mediciones/medir.py                      # todas
    python mediciones/medir.py --solo 1             # una sola
    python mediciones/medir.py --externas           # medición 1 con URLs reales

Requiere solo `httpx` en la máquina que mide. Guarda los números en
mediciones/resultados.json e imprime un resumen en Markdown para el README.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

import httpx

FINALES = {"completada", "fallida", "cancelada"}

# URLs reales para la variante --externas (pueden variar mucho entre corridas)
EXTERNAS = [
    "https://www.python.org", "https://docs.python.org/3/", "https://pypi.org",
    "https://fastapi.tiangolo.com", "https://www.python-httpx.org",
    "https://docs.aiohttp.org", "https://www.um.edu.ar", "https://github.com",
    "https://www.wikipedia.org", "https://es.wikipedia.org/wiki/Python",
    "https://www.debian.org", "https://www.kernel.org", "https://www.gnu.org",
    "https://www.mozilla.org", "https://www.w3.org", "https://www.iana.org",
    "https://httpbin.org/get", "https://example.com", "https://www.docker.com",
    "https://peps.python.org/pep-0492/",
]


async def crear(c: httpx.AsyncClient, tipo: str, params: dict, timeout=None) -> str:
    body = {"tipo": tipo, "params": params}
    if timeout:
        body["timeout"] = timeout
    r = await c.post("/tareas", json=body)
    r.raise_for_status()
    return r.json()["id"]


async def esperar_fin(c: httpx.AsyncClient, tid: str, cada=0.05) -> dict:
    while True:
        d = (await c.get(f"/tareas/{tid}")).json()
        if d["estado"] in FINALES:
            return d
        await asyncio.sleep(cada)


async def esperar_todas(c: httpx.AsyncClient, ids: list[str], cada=0.1) -> None:
    pendientes = set(ids)
    while pendientes:
        r = (await c.get("/tareas", params={"limite": 100_000})).json()
        hechas = {t["id"] for t in r["tareas"] if t["estado"] in FINALES}
        pendientes -= hechas
        if pendientes:
            await asyncio.sleep(cada)


# ------------------------------------------------------------------ medición 1

async def medicion_1(c: httpx.AsyncClient, base_interna: str, externas: bool) -> dict:
    print("\n== Medición 1: 20 descargas secuenciales vs concurrentes ==")
    if externas:
        urls = EXTERNAS
        fuente = "URLs reales de internet"
    else:
        # Demoras distintas entre 0.2 y 1.1 s: la suma es 13.0 s y la más
        # lenta tarda 1.1 s. Es un "servidor lento" reproducible.
        urls = [f"{base_interna}/lento?segundos={0.2 + (i % 10) * 0.1:.1f}&kb=50"
                for i in range(20)]
        fuente = "endpoint /lento (demoras de 0.2 a 1.1 s, 50 KB c/u)"
    out = {"fuente": fuente, "corridas": []}
    for rep in range(3):
        fila = {}
        for modo in ("secuencial", "concurrente"):
            tid = await crear(c, "descargar",
                              {"urls": urls, "secuencial": modo == "secuencial"},
                              timeout=300)
            d = await esperar_fin(c, tid)
            res = d["resultado"]
            fila[modo] = res["total_segundos"]
            fila[f"{modo}_ok"] = res["ok"]
            fila[f"{modo}_mas_lenta"] = max(r["segundos"] for r in res["resultados"])
            fila[f"{modo}_suma"] = round(sum(r["segundos"] for r in res["resultados"]), 3)
        fila["speedup"] = round(fila["secuencial"] / fila["concurrente"], 2)
        out["corridas"].append(fila)
        print(f"  corrida {rep + 1}: secuencial {fila['secuencial']:.3f} s | "
              f"concurrente {fila['concurrente']:.3f} s "
              f"(más lenta {fila['concurrente_mas_lenta']:.3f} s) | "
              f"speedup x{fila['speedup']}")
    out["speedup_promedio"] = round(
        statistics.mean(f["speedup"] for f in out["corridas"]), 2)
    return out


# ------------------------------------------------------------------ medición 2

async def _sondear(c: httpx.AsyncClient, hasta: asyncio.Event, cada=0.05) -> list[float]:
    lat = []
    while not hasta.is_set():
        t0 = time.perf_counter()
        await c.get("/tareas", params={"limite": 10})
        lat.append(time.perf_counter() - t0)
        await asyncio.sleep(cada)
    return lat


async def medicion_2(c: httpx.AsyncClient, iteraciones: int) -> dict:
    print(f"\n== Medición 2: GET /tareas durante un hash de {iteraciones:,} "
          "iteraciones ==")
    out = {"iteraciones": iteraciones}
    # Línea base: sin carga
    fin = asyncio.Event()
    sonda = asyncio.create_task(_sondear(c, fin))
    await asyncio.sleep(2)
    fin.set()
    base = await sonda
    out["sin_carga"] = _resumen(base)
    print(f"  sin carga       : {_fmt(out['sin_carga'])}")

    for modo in ("bloqueante", "hilo", "executor"):
        fin = asyncio.Event()
        t0 = time.perf_counter()
        tid = await crear(c, "hashear",
                          {"texto": "computacion2", "iteraciones": iteraciones,
                           "modo": modo}, timeout=600)
        sonda = asyncio.create_task(_sondear(c, fin))
        # esperar_fin también hace GETs, así que en modo bloqueante su primer
        # GET queda trabado hasta que termina el hash. Es lo que queremos ver.
        d = await esperar_fin(c, tid, cada=0.1)
        fin.set()
        lat = await sonda
        r = _resumen(lat)
        r["duracion_hash"] = d["resultado"]["segundos"]
        r["total_pared"] = round(time.perf_counter() - t0, 3)
        out[modo] = r
        print(f"  {modo:<15} : {_fmt(r)} | hash {r['duracion_hash']:.2f} s")
    return out


def _resumen(lat: list[float]) -> dict:
    ms = sorted(x * 1000 for x in lat)
    return {
        "n": len(ms),
        "promedio_ms": round(statistics.mean(ms), 2),
        "mediana_ms": round(statistics.median(ms), 2),
        "p95_ms": round(ms[int(len(ms) * 0.95) - 1] if len(ms) > 1 else ms[0], 2),
        "max_ms": round(ms[-1], 2),
    }


def _fmt(r: dict) -> str:
    return (f"n={r['n']:<3} prom {r['promedio_ms']:>8.1f} ms | mediana "
            f"{r['mediana_ms']:>7.1f} ms | máx {r['max_ms']:>8.1f} ms")


# ------------------------------------------------------------------ medición 3

async def medicion_3(c: httpx.AsyncClient, iteraciones_cpu: int) -> dict:
    stats = (await c.get("/estadisticas")).json()
    w = stats["workers"]["total"]
    cpus = stats["workers"]["procesos_cpu"]
    print(f"\n== Medición 3: techo del pool ({w} workers, {cpus} procesos CPU) ==")
    out = {"workers": w, "procesos_cpu": cpus, "io": [], "cpu": [], "api": []}

    print("  3a) K tareas `esperar` de 1 s")
    for k in (1, 5, 10, 11, 20, 40, 80, 160):
        t0 = time.perf_counter()
        ids = await asyncio.gather(*(crear(c, "esperar", {"segundos": 1})
                                     for _ in range(k)))
        await esperar_todas(c, ids, cada=0.05)
        dt = round(time.perf_counter() - t0, 3)
        teorico = -(-k // w) * 1.0
        out["io"].append({"k": k, "segundos": dt, "teorico": teorico})
        print(f"     K={k:<4} {dt:>7.2f} s  (teórico ceil(K/{w})·1s = {teorico:.0f} s)")

    print(f"  3b) K tareas `hashear` de {iteraciones_cpu:,} iteraciones")
    for k in (1, 2, 4, 8, 16):
        t0 = time.perf_counter()
        ids = await asyncio.gather(*(crear(c, "hashear",
                                           {"texto": f"t{i}",
                                            "iteraciones": iteraciones_cpu},
                                           timeout=600)
                                     for i in range(k)))
        await esperar_todas(c, ids, cada=0.05)
        dt = round(time.perf_counter() - t0, 3)
        out["cpu"].append({"k": k, "segundos": dt})
        print(f"     K={k:<4} {dt:>7.2f} s")

    print("  3c) Latencia de la API con muchas tareas en memoria")
    for extra in (0, 1000, 5000, 20000):
        if extra:
            sem = asyncio.Semaphore(50)

            async def una():
                async with sem:
                    await crear(c, "esperar", {"segundos": 0})
            t0 = time.perf_counter()
            await asyncio.gather(*(una() for _ in range(extra)))
            post_rate = round(extra / (time.perf_counter() - t0))
        else:
            post_rate = None
        await asyncio.sleep(1)
        lat_est, lat_lista = [], []
        for _ in range(5):
            t0 = time.perf_counter()
            await c.get("/estadisticas")
            lat_est.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            await c.get("/tareas", params={"limite": 100_000})
            lat_lista.append((time.perf_counter() - t0) * 1000)
        total = (await c.get("/estadisticas")).json()["total"]
        fila = {"tareas_en_memoria": total, "post_por_segundo": post_rate,
                "estadisticas_ms": round(statistics.median(lat_est), 1),
                "listar_todo_ms": round(statistics.median(lat_lista), 1)}
        out["api"].append(fila)
        print(f"     {total:>6} tareas: GET /estadisticas {fila['estadisticas_ms']:>7.1f} ms"
              f" | GET /tareas (todas) {fila['listar_todo_ms']:>8.1f} ms"
              + (f" | {post_rate} POST/s" if post_rate else ""))
    return out


# ------------------------------------------------------------------ main

async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--base-interna", default="http://127.0.0.1:8000",
                    help="cómo se ve la API a sí misma (dentro del contenedor)")
    ap.add_argument("--externas", action="store_true")
    ap.add_argument("--solo", type=int, choices=(1, 2, 3))
    ap.add_argument("--iter-pesado", type=int, default=6_000_000)
    ap.add_argument("--iter-cpu", type=int, default=1_500_000)
    args = ap.parse_args()

    res: dict = {"fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "base": args.base,
                 "cpu_host": os.cpu_count()}
    async with httpx.AsyncClient(base_url=args.base, timeout=600,
                                 limits=httpx.Limits(max_connections=60)) as c:
        (await c.get("/salud")).raise_for_status()
        if args.solo in (None, 1):
            res["m1"] = await medicion_1(c, args.base_interna, args.externas)
        if args.solo in (None, 2):
            res["m2"] = await medicion_2(c, args.iter_pesado)
        if args.solo in (None, 3):
            res["m3"] = await medicion_3(c, args.iter_cpu)

    destino = os.path.join(os.path.dirname(__file__), "resultados.json")
    previo = {}
    if os.path.exists(destino):
        with open(destino, encoding="utf-8") as f:
            previo = json.load(f)
    previo.update(res)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(previo, f, indent=2, ensure_ascii=False)
    print(f"\nResultados guardados en {destino}")


if __name__ == "__main__":
    asyncio.run(main())
