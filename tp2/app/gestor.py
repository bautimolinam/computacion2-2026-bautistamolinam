"""El Gestor: cola de tareas, pool de workers, cancelación, timeouts,
estadísticas, eventos y volcado de estado.

Todo lo que hay acá corre en UN solo hilo (el del event loop). Por eso no hay
ningún Lock: entre dos `await` ninguna otra corrutina puede ejecutarse, así que
operaciones como `self.ocupados += 1` o modificar `self.tareas` son atómicas
respecto de las demás corrutinas.
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import multiprocessing
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Any

from .modelos import ESTADOS_FINALES, Estado, Tarea
from .tareas import EJECUTORES

log = logging.getLogger("tp2.gestor")


class NoAcepta(Exception):
    """El sistema se está apagando y no acepta tareas nuevas."""


class ColaLlena(Exception):
    """La cola llegó a su tamaño máximo (backpressure)."""


class NoCancelable(Exception):
    """La tarea ya terminó."""


def _nada() -> None:
    """Función vacía para precalentar los procesos del executor."""


class Gestor:
    def __init__(
        self,
        n_workers: int = 10,
        cpu_workers: int | None = None,
        cola_max: int = 10_000,
        timeout_default: float = 60.0,
        dir_datos: str = "datos",
        timeout_http: float = 15.0,
        max_conexiones: int = 100,
    ) -> None:
        self.n_workers = n_workers
        self.cpu_workers = cpu_workers or os.cpu_count() or 1
        self.timeout_default = timeout_default
        self.dir_datos = dir_datos
        self.timeout_http = timeout_http
        self.max_conexiones = max_conexiones

        self.tareas: dict[str, Tarea] = {}
        # La cola guarda IDs, no objetos: así una tarea cancelada estando
        # pendiente simplemente se saltea cuando un worker la saca.
        self.cola: asyncio.Queue[str] = asyncio.Queue(maxsize=cola_max)
        self.workers: dict[int, asyncio.Task] = {}
        self.ocupados = 0
        self.aceptando = True
        self.executor: ProcessPoolExecutor | None = None
        self.suscriptores: set[asyncio.Queue] = set()
        self.inicio = time.time()

    # ------------------------------------------------------------ arranque

    async def iniciar(self) -> None:
        os.makedirs(self.dir_datos, exist_ok=True)
        self._reportar_volcado_previo()

        # "spawn" en todas las plataformas: hacer fork() de un proceso que ya
        # tiene un event loop y threads es inseguro (Python 3.12+ lo avisa).
        ctx = multiprocessing.get_context("spawn")
        self.executor = ProcessPoolExecutor(
            max_workers=self.cpu_workers, mp_context=ctx
        )
        # Precalentar: que los procesos arranquen ahora y no en la primera
        # tarea (spawn tarda ~100 ms por proceso en re-importar el módulo).
        loop = asyncio.get_running_loop()
        await asyncio.gather(
            *(loop.run_in_executor(self.executor, _nada)
              for _ in range(self.cpu_workers))
        )

        for i in range(self.n_workers):
            self._lanzar_worker(i)
        log.info(
            "Gestor listo: %d workers async, %d procesos CPU",
            self.n_workers, self.cpu_workers,
        )

    def _reportar_volcado_previo(self) -> None:
        archivos = sorted(glob.glob(os.path.join(self.dir_datos, "estado_*.json")))
        if not archivos:
            log.info("No hay volcados de estado previos en %s", self.dir_datos)
            return
        ultimo = archivos[-1]
        try:
            with open(ultimo, encoding="utf-8") as f:
                datos = json.load(f)
            conteo = Counter(t["estado"] for t in datos.get("tareas", []))
            log.info(
                "Volcado previo encontrado: %s (%d tareas: %s). "
                "Hay %d volcados en total.",
                ultimo, sum(conteo.values()), dict(conteo), len(archivos),
            )
        except (OSError, ValueError) as e:
            log.warning("Volcado previo %s ilegible: %s", ultimo, e)

    # ------------------------------------------------------------- workers

    def _lanzar_worker(self, wid: int) -> None:
        t = asyncio.create_task(self._worker(wid), name=f"worker-{wid}")
        t.add_done_callback(lambda tk, wid=wid: self._worker_terminado(wid, tk))
        self.workers[wid] = t

    def _worker_terminado(self, wid: int, t: asyncio.Task) -> None:
        """Supervisor: si un worker muere por una excepción inesperada, se
        registra y se lanza uno nuevo para que el pool no se achique."""
        if t.cancelled() or not self.aceptando:
            return
        exc = t.exception()
        log.error("worker-%d murió (%r); relanzando", wid, exc)
        self._lanzar_worker(wid)

    async def _worker(self, wid: int) -> None:
        while True:
            tid = await self.cola.get()
            try:
                tarea = self.tareas.get(tid)
                if tarea is None or tarea.estado is not Estado.PENDIENTE:
                    continue  # fue cancelada mientras esperaba en la cola
                await self._ejecutar(tarea, wid)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - el worker no debe morir
                log.exception("Error interno del worker-%d con %s", wid, tid)
            finally:
                self.cola.task_done()

    async def _ejecutar(self, tarea: Tarea, wid: int) -> None:
        tarea.estado = Estado.EJECUTANDO
        tarea.iniciada = time.time()
        tarea.worker = wid
        self.ocupados += 1
        self._emitir(tarea)

        # La tarea corre en su PROPIA asyncio.Task. Así DELETE puede cancelar
        # solo el trabajo (handle.cancel()) sin matar al worker.
        tarea.handle = asyncio.create_task(
            self._correr_con_timeout(tarea), name=f"tarea-{tarea.id}"
        )
        try:
            # asyncio.wait NO propaga la excepción ni la cancelación de la
            # tarea hija: solo espera a que termine. Después la inspeccionamos.
            await asyncio.wait({tarea.handle})
        except asyncio.CancelledError:
            # Cancelaron al WORKER (apagado): cancelamos el trabajo, esperamos
            # a que libere sus recursos y recién ahí propagamos.
            tarea.motivo_cancelacion = tarea.motivo_cancelacion or "apagado"
            tarea.handle.cancel()
            await asyncio.wait({tarea.handle})
            self._cerrar(tarea)
            raise
        finally:
            self.ocupados -= 1
        self._cerrar(tarea)

    async def _correr_con_timeout(self, tarea: Tarea) -> Any:
        ejecutor = EJECUTORES[tarea.tipo]
        # asyncio.timeout (3.11): al vencer, cancela lo que está adentro y lo
        # convierte en TimeoutError. La cancelación interna libera recursos
        # igual que un DELETE.
        async with asyncio.timeout(tarea.timeout):
            return await ejecutor(tarea.params, self)

    def _cerrar(self, tarea: Tarea) -> None:
        """Pasa la tarea a su estado final según cómo terminó su handle.
        Es idempotente: lo pueden llamar el worker, DELETE o el apagado."""
        h = tarea.handle
        if tarea.estado in ESTADOS_FINALES or h is None or not h.done():
            return
        tarea.finalizada = time.time()
        if h.cancelled():
            tarea.estado = Estado.CANCELADA
            tarea.error = f"cancelada: {tarea.motivo_cancelacion or 'sin motivo'}"
        elif isinstance(h.exception(), TimeoutError):
            tarea.estado = Estado.FALLIDA
            tarea.error = f"timeout: superó los {tarea.timeout} s"
        elif h.exception() is not None:
            e = h.exception()
            tarea.estado = Estado.FALLIDA
            tarea.error = f"{type(e).__name__}: {e}"
        else:
            tarea.estado = Estado.COMPLETADA
            tarea.resultado = h.result()
        tarea.handle = None  # soltamos la referencia a la Task
        self._emitir(tarea)

    # ------------------------------------------------------- API pública

    def encolar(self, tipo: str, params: dict, timeout: float | None) -> Tarea:
        if not self.aceptando:
            raise NoAcepta()
        tarea = Tarea(tipo=tipo, params=params,
                      timeout=timeout or self.timeout_default)
        try:
            # put_nowait: el POST no espera nunca. Si la cola está llena,
            # rechazamos en vez de bloquear (backpressure explícito).
            self.cola.put_nowait(tarea.id)
        except asyncio.QueueFull:
            raise ColaLlena() from None
        self.tareas[tarea.id] = tarea
        self._emitir(tarea)
        return tarea

    async def cancelar(self, tid: str) -> Tarea:
        tarea = self.tareas[tid]  # KeyError -> 404 en la API
        if tarea.estado is Estado.PENDIENTE:
            tarea.estado = Estado.CANCELADA
            tarea.finalizada = time.time()
            tarea.error = "cancelada: por el usuario (estaba en cola)"
            self._emitir(tarea)
            return tarea
        if tarea.estado is Estado.EJECUTANDO and tarea.handle is not None:
            tarea.motivo_cancelacion = "por el usuario (en ejecución)"
            tarea.handle.cancel()
            # Esperamos (con límite) a que la tarea procese la cancelación y
            # cierre sus recursos, para responder con el estado ya final.
            await asyncio.wait({tarea.handle}, timeout=5)
            self._cerrar(tarea)
            return tarea
        raise NoCancelable(tarea.estado.value)

    def listar(self, estado: Estado | None = None,
               tipo: str | None = None) -> list[Tarea]:
        return [
            t for t in self.tareas.values()
            if (estado is None or t.estado is estado)
            and (tipo is None or t.tipo == tipo)
        ]

    def estadisticas(self) -> dict[str, Any]:
        por_estado = {e.value: 0 for e in Estado}
        por_estado.update(Counter(t.estado.value for t in self.tareas.values()))

        duraciones: dict[str, list[float]] = defaultdict(list)
        esperas: list[float] = []
        for t in self.tareas.values():
            if t.estado is Estado.COMPLETADA and t.duracion is not None:
                duraciones[t.tipo].append(t.duracion)
            if t.espera_en_cola is not None:
                esperas.append(t.espera_en_cola)

        def prom(xs: list[float]) -> float | None:
            return round(sum(xs) / len(xs), 4) if xs else None

        todas = [d for xs in duraciones.values() for d in xs]
        return {
            "total": len(self.tareas),
            "por_estado": por_estado,
            "tiempo_promedio_ejecucion": prom(todas),
            "tiempo_promedio_por_tipo": {k: prom(v) for k, v in duraciones.items()},
            "tiempo_promedio_en_cola": prom(esperas),
            "workers": {
                "total": self.n_workers,
                "ocupados": self.ocupados,
                "libres": self.n_workers - self.ocupados,
                "procesos_cpu": self.cpu_workers,
            },
            "en_cola": self.cola.qsize(),
            "aceptando": self.aceptando,
            "uptime_segundos": round(time.time() - self.inicio, 1),
        }

    # ------------------------------------------------- eventos (WebSocket)

    def suscribir(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.suscriptores.add(q)
        return q

    def desuscribir(self, q: asyncio.Queue) -> None:
        self.suscriptores.discard(q)

    def _emitir(self, tarea: Tarea) -> None:
        evento = {"id": tarea.id, "tipo": tarea.tipo,
                  "estado": tarea.estado.value, "ts": time.time()}
        for q in list(self.suscriptores):
            try:
                q.put_nowait(evento)
            except asyncio.QueueFull:
                pass  # cliente lento: pierde eventos, pero no frena a nadie

    # ------------------------------------------------------------ apagado

    async def apagar(self, limite: float = 10.0) -> str:
        """Apagado ordenado: no aceptar más, descartar la cola, esperar (con
        límite) a las que corren, cancelar las rezagadas, volcar a JSON."""
        self.aceptando = False
        log.info("Apagado: no se aceptan tareas nuevas")

        # 1. Las pendientes no llegan a ejecutarse.
        descartadas = 0
        while not self.cola.empty():
            tid = self.cola.get_nowait()
            self.cola.task_done()
            t = self.tareas.get(tid)
            if t and t.estado is Estado.PENDIENTE:
                t.estado = Estado.CANCELADA
                t.finalizada = time.time()
                t.error = "cancelada: apagado antes de ejecutarse"
                descartadas += 1

        # 2. Esperar a las que están en curso, con límite.
        en_curso = [t for t in self.tareas.values()
                    if t.estado is Estado.EJECUTANDO and t.handle is not None]
        log.info("Apagado: %d pendientes descartadas, esperando %d en curso "
                 "(límite %.1f s)", descartadas, len(en_curso), limite)
        if en_curso:
            _, rezagadas = await asyncio.wait(
                [t.handle for t in en_curso], timeout=limite
            )
            # 3. Las que no terminaron a tiempo se cancelan.
            for t in en_curso:
                if t.handle in rezagadas:
                    t.motivo_cancelacion = "apagado: superó el límite de espera"
                    t.handle.cancel()
            if rezagadas:
                await asyncio.wait(rezagadas, timeout=5)
            for t in en_curso:
                self._cerrar(t)

        # 4. Detener workers (están bloqueados en cola.get(): cancelarlos es
        #    limpio) y esperar a que terminen de verdad.
        for w in self.workers.values():
            w.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)

        # 5. Cerrar el pool de procesos sin bloquear el loop.
        if self.executor is not None:
            await asyncio.to_thread(
                self.executor.shutdown, wait=True, cancel_futures=True
            )

        # 6. Avisar a los clientes WebSocket que se cierra.
        for q in list(self.suscriptores):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

        return await self.volcar()

    async def volcar(self) -> str:
        nombre = os.path.join(
            self.dir_datos,
            f"estado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        contenido = {
            "volcado": datetime.now().isoformat(),
            "estadisticas": self.estadisticas(),
            "tareas": [t.a_dict() for t in self.tareas.values()],
        }
        texto = json.dumps(contenido, ensure_ascii=False, indent=2, default=str)

        def _escribir() -> None:
            with open(nombre, "w", encoding="utf-8") as f:
                f.write(texto)

        # Escribir a disco es I/O bloqueante: va a un hilo.
        await asyncio.to_thread(_escribir)
        log.info("Estado volcado en %s (%d tareas)", nombre, len(self.tareas))
        return nombre
