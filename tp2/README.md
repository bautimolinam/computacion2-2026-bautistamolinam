# TP2 — Plataforma de Tareas Distribuidas

**Computación II — Universidad de Mendoza — 2026**

Servicio HTTP al que se le encargan trabajos, los ejecuta de forma concurrente
con un pool de workers asíncronos y permite consultar su estado y resultado.
Todo corre en **un solo hilo con un event loop**; el trabajo CPU-bound se manda
a un pool de **procesos** para que el loop nunca se congele.

---

## 1. Descripción y uso

### Levantar

```bash
git clone <este-repo>
cd <este-repo>
docker compose up --build
```

La API queda en `http://localhost:8000` (documentación interactiva en
`http://localhost:8000/docs`). Para apagar: `Ctrl+C` o `docker compose stop`
(ambos mandan una señal y disparan el apagado ordenado).

Sin Docker (Linux/macOS/WSL, Python 3.11+):

```bash
pip install -r requirements.txt
python -m app.main
```

### Endpoints

| Método | Ruta | Qué hace |
|--------|------|----------|
| `POST` | `/tareas` | Encola una tarea. Responde **202** al instante con `id` y estado |
| `GET` | `/tareas?estado=&tipo=&limite=&con_resultado=` | Lista tareas con filtros opcionales |
| `GET` | `/tareas/{id}` | Estado y resultado de una tarea |
| `DELETE` | `/tareas/{id}` | Cancela una tarea pendiente **o en ejecución** |
| `GET` | `/estadisticas` | Totales por estado, tiempos promedio, workers ocupados, cola |
| `GET` | `/metrics` | *(bonus)* Métricas en formato Prometheus |
| `WS` | `/eventos` | *(bonus)* Cambios de estado en tiempo real |
| `GET` | `/lento?segundos=&kb=` | Auxiliar: servidor lento reproducible para medir |

Estados: `pendiente` → `ejecutando` → `completada` | `fallida` | `cancelada`.

Códigos de error: `422` parámetros inválidos, `404` id inexistente,
`409` cancelar algo ya terminado, `429` cola llena, `503` servidor apagándose.

### Ejemplos con `curl`

```bash
# esperar: duerme N segundos
curl -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
     -d '{"tipo": "esperar", "params": {"segundos": 30}}'
# -> {"id":"0cb87d127934","estado":"pendiente","url":"/tareas/0cb87d127934"}

# ver el estado en vivo
curl localhost:8000/tareas/0cb87d127934

# cancelarla mientras corre
curl -X DELETE localhost:8000/tareas/0cb87d127934
# -> {..."estado":"cancelada","error":"cancelada: por el usuario (en ejecución)"...}

# timeout propio: a los 2 s pasa a "fallida" con el motivo
curl -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
     -d '{"tipo": "esperar", "params": {"segundos": 10}, "timeout": 2}'

# hashear: SHA-256 aplicado N veces (CPU-bound, va al pool de procesos)
curl -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
     -d '{"tipo": "hashear", "params": {"texto": "hola", "iteraciones": 5000000}}'

# descargar: varias URLs concurrentes, devuelve status, bytes y tiempo de cada una
curl -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
     -d '{"tipo": "descargar", "params": {"urls": ["https://www.python.org", "https://example.com"]}}'

# listar solo las completadas, con resultado
curl 'localhost:8000/tareas?estado=completada&con_resultado=true'

# estadísticas
curl localhost:8000/estadisticas
```

Parámetros extra que existen para las mediciones:

- `descargar` acepta `"secuencial": true` (un `for` con `await` en vez de concurrente).
- `hashear` acepta `"modo"`: `"executor"` (default, procesos), `"hilo"`
  (`asyncio.to_thread`) o `"bloqueante"` (corre en el event loop: **el bug a
  propósito** de la medición 2).

### Configuración (variables de entorno en `docker-compose.yml`)

| Variable | Default | Qué controla |
|----------|---------|--------------|
| `WORKERS` | 10 | Corrutinas consumidoras de la cola |
| `CPU_WORKERS` | 0 (= nº de CPUs) | Procesos del `ProcessPoolExecutor` |
| `COLA_MAX` | 10000 | Tamaño máximo de la cola (después responde 429) |
| `TIMEOUT_DEFAULT` | 60 | Timeout en segundos si la tarea no trae uno |
| `LIMITE_APAGADO` | 10 | Cuánto se espera a las tareas en curso al apagar |

### Tests (bonus)

```bash
pip install -r requirements-dev.txt
pytest -v
```

16 tests con `pytest-asyncio`: cancelación de tareas pendientes y en ejecución,
cancelación a mitad de una descarga (verifica que el cliente HTTP se cierra y no
quedan `Task` huérfanas), timeouts, que una excepción no mata al worker, el
supervisor, que el executor no bloquea el loop, cola llena, apagado ordenado y
la API completa con WebSocket.

### Mediciones

```bash
docker compose up --build        # en una terminal
pip install httpx
python mediciones/medir.py       # en otra (~3 minutos)
python mediciones/medir.py --solo 1 --externas   # medición 1 con sitios reales
```

---

## 2. Arquitectura

```
                     ┌──────────────── un solo proceso, un solo hilo, un event loop ─────────────────┐
                     │                                                                                │
 Cliente HTTP ──────►│  uvicorn + FastAPI                     Gestor                                  │
 (curl, script)      │  ┌───────────────────┐   encolar()   ┌──────────────────────────┐              │
                     │  │ POST   /tareas    │──────────────►│ tareas: dict[id, Tarea]  │              │
                     │  │ GET    /tareas    │  put_nowait   │ cola: asyncio.Queue[id]  │              │
 WebSocket ◄─────────│  │ GET    /tareas/id │◄──────────────│                          │              │
 /eventos            │  │ DELETE /tareas/id │   leer estado └────────────┬─────────────┘              │
                     │  │ GET /estadisticas │                            │ await cola.get()           │
                     │  └───────────────────┘                            ▼                            │
                     │          │ cancelar()        ┌──────────────────────────────────────┐          │
                     │          └──────────────────►│ worker-0 … worker-9  (corrutinas)    │          │
                     │            handle.cancel()   │   cada tarea corre en su propia Task │          │
                     │                              │   con asyncio.timeout(t)             │          │
                     │                              └────┬───────────────┬──────────────┬──┘          │
                     │                                   │               │              │             │
                     │                          esperar: │   descargar:  │    hashear:  │             │
                     │                      asyncio.sleep│ httpx async + │ run_in_executor            │
                     │                                   │   TaskGroup   │              │             │
                     └───────────────────────────────────┼───────────────┼──────────────┼─────────────┘
                                                         │               ▼              ▼
                                                         │         sockets (epoll)   ProcessPoolExecutor
                                                         │         internet          (N procesos, spawn)
                                                                                       SHA-256 en bloques
```

### Flujo de una tarea

1. `POST /tareas` valida el cuerpo (unión discriminada de pydantic por `tipo`),
   crea un `Tarea` en estado `pendiente`, mete su **id** en la `asyncio.Queue`
   con `put_nowait` y responde `202`. No hay ningún `await` largo: el POST
   tarda ~2 ms aunque la tarea dure una hora.
2. Algún worker libre que estaba dormido en `await cola.get()` se despierta,
   la marca `ejecutando` y crea una **`asyncio.Task` propia** para el trabajo
   (`tarea.handle`), envuelta en `asyncio.timeout(timeout)`.
3. El worker espera esa Task con `asyncio.wait({handle})`, que no propaga
   excepciones ni cancelaciones: solo espera a que termine.
4. Según cómo terminó la Task, `_cerrar()` decide el estado final:
   resultado → `completada`; `TimeoutError` → `fallida` ("timeout: superó los
   N s"); otra excepción → `fallida` con el mensaje; cancelada → `cancelada`.
5. Cada cambio de estado se publica a los suscriptores de `/eventos`.

### Archivos

| Archivo | Contenido |
|---------|-----------|
| `app/main.py` | API FastAPI, lifespan, señales, arranque con guarda `__main__` |
| `app/gestor.py` | Cola, pool de workers, supervisor, cancelación, timeouts, stats, apagado, volcado |
| `app/tareas.py` | Las tres tareas: `descargar`, `hashear`, `esperar` |
| `app/modelos.py` | Estados, validación de parámetros, registro `Tarea` |
| `mediciones/medir.py` | Script de las tres mediciones; guarda `resultados.json` |
| `tests/` | Tests con `pytest-asyncio` |

---

## 3. Decisiones de diseño

### ¿Por qué `asyncio.Queue` y no `queue.Queue`?

`queue.Queue.get()` es una llamada **bloqueante del sistema operativo**: el
hilo que la llama se duerme en un lock hasta que haya un elemento. Como acá hay
**un solo hilo** (el del event loop), un worker haciendo `queue.Queue.get()`
dormiría al loop entero: ni los otros workers, ni el servidor HTTP podrían
avanzar. Es exactamente el mismo bug que `time.sleep()`.

`asyncio.Queue.get()` es una **corrutina**: si la cola está vacía, el worker
queda suspendido en un `Future` y le devuelve el control al loop, que sigue
atendiendo requests. Cuando el POST hace `put_nowait`, el loop despierta a un
worker en la próxima vuelta. Además `queue.Queue` trae locks internos para
sincronizar threads que acá no hacen falta (ver "¿por qué no hay Lock?"), y
`asyncio.Queue` no es thread-safe, lo cual está bien porque solo la toca el loop.

Detalles de la cola:
- Guarda **ids** y no objetos. Si cancelan una tarea pendiente, se marca
  `cancelada` en el diccionario y el worker que después saque ese id la saltea.
  Así no hay que "sacar algo del medio" de la cola.
- Es **acotada** (`COLA_MAX`) y el POST usa `put_nowait`: si se llena, responde
  `429` en lugar de bloquear el endpoint (que es lo que haría `await put()`).
  Es backpressure explícito.

### ¿Cómo evito que una tarea CPU-bound congele la API?

El event loop es cooperativo: una corrutina solo cede el control en un `await`.
`hash_iterado()` es un `for` de millones de SHA-256 sin ningún `await`, así que
si se llama directo desde una corrutina (modo `bloqueante`) el loop queda
secuestrado hasta que termina. La medición 2 lo muestra: un `GET /tareas` pasa
de ~6 ms a **3374 ms**.

La solución es `loop.run_in_executor(ProcessPoolExecutor, hash_iterado, ...)`:

- El cálculo corre en **otro proceso**. El worker hace `await` sobre el
  `Future` que devuelve el executor, así que se suspende y el loop sigue libre.
- **Procesos y no threads**: por el GIL, un thread que hace SHA-256 sobre 32
  bytes compite por el intérprete con el loop. `hashlib` libera el GIL solo con
  entradas de más de 2047 bytes; con un hash de 32 bytes no lo suelta. Con
  `to_thread` (modo `hilo`) el loop no se congela, pero el sistema le va
  quitando el GIL cada 5 ms (`sys.getswitchinterval()`) y la latencia sube 2.5×
  (5.8 ms → 14.5 ms, máx 23 ms). Con procesos cada uno tiene su propio GIL y la
  API queda igual que sin carga.
- **`spawn` explícito** (`multiprocessing.get_context("spawn")`) en todas las
  plataformas. Hacer `fork()` de un proceso que ya tiene un event loop y
  threads puede heredar locks tomados; Python 3.12+ lo advierte. Con spawn el
  hijo re-importa el módulo, por eso `main.py` tiene la guarda
  `if __name__ == "__main__":` y `hash_iterado` está a nivel de módulo (pickle
  serializa funciones por nombre).
- Los procesos se **precalientan** al arrancar para que la primera tarea no
  pague los ~100 ms de spawn.
- El hash se manda **en bloques de 200.000 iteraciones** (~0.15 s). Esto es lo
  que hace cancelable a una tarea CPU-bound (ver siguiente punto).

### ¿Cómo implementé la cancelación, y qué pasa con los recursos a medio usar?

Cada tarea en ejecución vive en su propia `asyncio.Task` (`tarea.handle`),
separada del worker. `DELETE` hace `tarea.handle.cancel()`, que **inyecta un
`CancelledError` en el `await` donde esa Task esté suspendida**. A partir de
ahí cada tipo de tarea libera lo suyo porque el código usa context managers y
estructuras que reaccionan a la cancelación:

| Tarea | Dónde la agarra la cancelación | Qué se libera |
|-------|--------------------------------|---------------|
| `esperar` | `await asyncio.sleep()` | Nada que liberar; el timer se descarta |
| `descargar` | Dentro del `asyncio.TaskGroup` | El TaskGroup cancela **todas** las descargas hijas y espera a que terminen; después el `async with httpx.AsyncClient` ejecuta `__aexit__` y cierra los sockets. Una descarga a medio leer se corta y su conexión se cierra. |
| `hashear` | En el `await` del `Future` del executor, entre bloques | No se manda el bloque siguiente. El bloque que ya está calculando en el proceso hijo **no se puede interrumpir**: termina (≤ ~0.15 s) y su resultado se descarta. El proceso queda libre para otra tarea. |

Detalles que hacen que sea **limpia**:

- `DELETE` no solo llama a `cancel()`: espera (hasta 5 s) a que la Task
  termine de procesar la cancelación, y recién ahí responde, ya con estado
  `cancelada`. Medido: ~30 ms.
- El worker espera con `asyncio.wait({handle})`, que **no** relanza el
  `CancelledError` de la hija. Si usara `await handle`, la cancelación de la
  tarea le llegaría al worker como si lo hubieran cancelado a él.
- Si el que se cancela es el **worker** (durante el apagado), el `except
  CancelledError` cancela primero la tarea hija, espera a que termine y recién
  después propaga. No queda ninguna Task huérfana: el test
  `test_cancelar_descarga_cierra_cliente` verifica que `asyncio.all_tasks()`
  vuelve a la misma cantidad que antes y que el cliente HTTP se cerró.
- El **timeout** usa el mismo mecanismo: `asyncio.timeout()` cancela lo que
  está adentro al vencer y lo convierte en `TimeoutError`. Los recursos se
  liberan igual que en un DELETE; solo cambia el estado final (`fallida`).
- `_cerrar()` es idempotente: lo pueden llamar el worker, el DELETE o el
  apagado sin pisarse.

### ¿Qué pasa si un worker lanza una excepción no capturada?

Hay tres capas:

1. **La excepción de la tarea** (un bug en un ejecutor, un `ValueError`, etc.)
   queda guardada dentro de su `asyncio.Task`. El worker la lee con
   `handle.exception()` y marca la tarea `fallida` con el mensaje. El worker
   **no se entera** como excepción y sigue consumiendo.
2. **Un error del propio worker** (en el código alrededor de la tarea) lo
   atrapa un `except Exception` en el bucle del worker, que lo loguea con
   traceback y sigue. El `finally` siempre hace `cola.task_done()`.
3. **Si igual un worker muriera**, tiene un `add_done_callback` que hace de
   supervisor: si la Task terminó por excepción (no por cancelación ni por
   apagado), lo loguea y **lanza un worker nuevo** con el mismo número. El pool
   nunca se achica en silencio.

Sin esto, una excepción no capturada en una `Task` simplemente la termina; el
resto del programa sigue andando y asyncio solo muestra "Task exception was
never retrieved" cuando se recolecta. El síntoma sería un pool que va perdiendo
workers hasta que las tareas quedan `pendiente` para siempre.

### Apagado ordenado (SIGTERM / SIGINT)

- En el arranque se instala `loop.add_signal_handler()` para SIGTERM y SIGINT,
  reemplazando el handler de uvicorn. El handler corre **dentro del loop** como
  un callback más (no interrumpe código arbitrario como `signal.signal`): marca
  `aceptando = False` (los POST nuevos reciben `503`) y le pide a uvicorn que
  salga (`server.should_exit = True`).
- uvicorn deja de aceptar conexiones y ejecuta el *shutdown* del lifespan, que
  llama a `Gestor.apagar()`:
  1. las tareas `pendiente` se marcan `cancelada` ("apagado antes de ejecutarse");
  2. espera a las que están `ejecutando`, con límite `LIMITE_APAGADO` (10 s);
  3. las que no terminaron a tiempo se cancelan ("superó el límite de espera");
  4. cancela los workers y hace `gather` para esperar que terminen de verdad;
  5. cierra el `ProcessPoolExecutor` (`shutdown` en un thread para no bloquear);
  6. avisa a los WebSockets y vuelca todo a `datos/estado_<timestamp>.json`.
- Una segunda señal durante el apagado fuerza la salida.
- Al arrancar, si hay volcados en `datos/`, se reporta el último por log con la
  cantidad de tareas por estado.
- Docker: el `CMD` en forma exec deja a Python como PID 1 para que reciba la
  señal, y `stop_grace_period: 30s` es mayor que el límite de apagado (si no,
  Docker mandaría SIGKILL antes del volcado). El volumen `./datos` hace que los
  volcados sobrevivan al contenedor.
- En Windows sin Docker no existe `add_signal_handler`; el código lo detecta y
  deja el handler de uvicorn, que igual dispara el mismo shutdown.

### ¿Por qué `httpx` y no `aiohttp` (y nunca `requests`)?

`requests` es bloqueante: cada `get()` duerme al hilo, o sea al loop. Entre
las dos opciones asíncronas elegí `httpx` porque su API es casi igual a la de
`requests` (curva de aprendizaje mínima), tiene timeouts claros y pool de
conexiones configurable (`httpx.Limits`), y se integra con FastAPI (los tests
usan el mismo ecosistema). `aiohttp` es algo más rápido en throughput puro y
trae servidor propio, que acá no hace falta porque el servidor es FastAPI.

### ¿Por qué no hace falta un `Lock`?

Todo el estado compartido (`tareas`, `cola`, `ocupados`) lo tocan corrutinas
que corren en **un solo hilo**. El event loop solo cambia de corrutina en un
`await`; entre dos `await` el código es atómico respecto de las demás. Por
ejemplo, en `encolar()` el chequeo de `aceptando`, el `put_nowait` y el alta en
el diccionario ocurren sin ningún `await` en el medio, así que nadie puede
"meterse" entre medio. Con threads, el scheduler del SO podría interrumpir en
cualquier bytecode y sí haría falta un lock. El único paralelismo real está en
los procesos del executor, que no comparten memoria: reciben argumentos y
devuelven resultados por pickle.

---

## 4. Mediciones

Entorno: notebook con Windows + WSL2 (Ubuntu), la API corriendo en Docker con
`docker compose up --build` y el script de mediciones en WSL contra
`localhost:8000`. 10 workers async y **8 procesos CPU** (`CPU_WORKERS` = nº de
CPUs que ve el contenedor). Números crudos en
[`mediciones/resultados.json`](mediciones/resultados.json). Se reproducen con
`python mediciones/medir.py`.

> Nota: todas las latencias incluyen el reenvío de puertos de Docker/WSL
> (Windows → WSL → contenedor). Por eso la línea base de un `GET` es ~6 ms y
> no ~1 ms. Lo que importa es la comparación entre filas, no el valor absoluto.

### 4.1 Concurrencia I/O: 20 descargas secuenciales vs concurrentes

Las 20 URLs apuntan al endpoint `/lento`, con demoras de 0.2 a 1.1 s
(suma = 13.0 s, la más lenta = 1.1 s) y 50 KB cada una. Uso un servidor lento
propio para que el resultado sea reproducible; con `--externas` se repite con
20 sitios reales.

| Corrida | Secuencial | Concurrente | Descarga más lenta | Speedup |
|---------|-----------:|------------:|-------------------:|--------:|
| 1 | 13.165 s | 2.523 s | 2.505 s | ×5.22 |
| 2 | 13.115 s | 1.157 s | 1.133 s | ×11.33 |
| 3 | 13.109 s | 1.169 s | 1.134 s | ×11.21 |

**Interpretación.** Secuencial tarda la **suma** (13.0 s + ~5 ms de overhead
por request). Concurrente tarda lo que **la más lenta** (1.13 s) + ~25 ms de
overhead. El speedup no es ×20 sino ×11.3 porque está acotado por
`suma / máximo = 13.0 / 1.1 = 11.8`: no importa cuántas lancemos juntas, nunca
podemos terminar antes que la más lenta.

La **corrida 1 es un outlier** y lo dejo a propósito: la descarga más lenta
tardó 2.5 s en vez de 1.1 s. Fue la primera vez que el contenedor abría 20
conexiones TCP a la vez contra sí mismo. Mi interpretación es que fue un costo
de arranque en frío: el pool de conexiones vacío, el backlog del socket y la
primera vez que se ejecutaba ese código. En las corridas 2 y 3, con todo
"caliente", el resultado vuelve a lo teórico. Igual, incluso esa corrida
concurrente fue 5 veces más rápida que la secuencial.

Es la diferencia entre `for url in urls: await get(url)` (cada `await`
suspende hasta que termina **esa** descarga, y recién después se lanza la
siguiente) y lanzarlas todas como Tasks (`TaskGroup`/`gather`): las 20 quedan
esperando sus sockets a la vez, el loop las multiplexa con `epoll` y despierta
a cada una cuando llega su respuesta. Mientras tanto la CPU está casi ociosa:
esperar no consume CPU, por eso asyncio sirve para I/O.

### 4.2 El event loop bloqueado

Hash de **6.000.000 de iteraciones** de SHA-256 (~3.4 s de CPU). Mientras
corre, un cliente hace `GET /tareas` cada 50 ms y mide cuánto tarda cada uno.

| Modo | GETs respondidos durante el hash | Promedio | Mediana | Máximo | Duración del hash |
|------|---------------------------------:|---------:|--------:|-------:|------:|
| Sin carga (línea base, 2 s) | 36 | 5.8 ms | 6.5 ms | 9.3 ms | — |
| **Sin executor** (`bloqueante`) | **2** | **1690.8 ms** | 1690.8 ms | **3374.1 ms** | 3.37 s |
| `asyncio.to_thread` (`hilo`) | 63 | 14.5 ms | 15.0 ms | 23.0 ms | 4.03 s |
| **`run_in_executor` + procesos** | **76** | **4.6 ms** | 4.5 ms | **6.0 ms** | 4.17 s |

**Interpretación.**

- **Sin executor**, en 3.37 s se respondieron **2** requests: uno se quedó
  esperando **todo** el hash (3374 ms, exactamente la duración del hash) y el
  otro llegó al final. El servidor no estaba "lento": estaba **muerto**. Los
  requests quedaban en el backlog del socket porque el loop no volvía a llamar
  a `epoll` hasta que el `for` del hash terminara. No solo la API: tampoco
  avanzaban los timeouts, las otras descargas ni el `asyncio.sleep` de otras
  tareas. Un timeout de 1 s en otra tarea habría vencido a los 3.4 s.
- **Con procesos**, la latencia es **igual a la línea base** (4.6 vs 5.8 ms;
  el máximo de 6 ms está incluso por debajo del máximo sin carga). El loop
  solo espera un `Future`; el cálculo pasa en otro núcleo. El hash tardó un
  poco más (4.17 vs 3.37 s) porque se manda en 30 bloques, y cada bloque
  cuesta un viaje de ida y vuelta por pickle entre procesos. Ese costo es el
  precio de poder cancelarlo.
- **Con threads** el loop no se congela (63 respuestas), pero la latencia se
  multiplica por 2.5 y el máximo llega a 23 ms: el thread del hash y el del
  loop se disputan el GIL, que se transfiere cada ~5 ms. Esto muestra que el
  problema de fondo no es "asyncio" sino que en CPython el trabajo de CPU en
  Python puro solo escala con **procesos**.

Esta es la diferencia que el enunciado llama "el punto de la materia":
asyncio da concurrencia, no paralelismo. Una corrutina que no hace `await`
bloquea a todas.

### 4.3 Techo del pool

**(a) Tareas I/O (`esperar` de 1 s) con 10 workers:**

| K tareas | Tiempo total | Teórico `ceil(K/10)·1 s` |
|---------:|-------------:|-------------------------:|
| 1 | 1.01 s | 1 s |
| 5 | 1.05 s | 1 s |
| 10 | 1.05 s | 1 s |
| **11** | **2.04 s** | 2 s |
| 20 | 2.09 s | 2 s |
| 40 | 4.11 s | 4 s |
| 80 | 8.11 s | 8 s |
| 160 | 16.20 s | 16 s |

El límite está **exactamente en K = WORKERS = 10**. Hasta 10, todas corren a la
vez y el tiempo es el de una. Con 11, la undécima espera en la cola a que se
libere un worker y el tiempo se duplica: el sistema procesa en "tandas" de 10.
Este techo es **artificial**: lo pone la cantidad de workers, no el hardware.
Esperar no consume CPU, así que se podría subir `WORKERS` a 1000 sin problema.
El verdadero límite para I/O aparece mucho más arriba: descriptores de
archivo (sockets abiertos, `ulimit -n` suele ser 1024), memoria por conexión y
cuánto aguanta el servidor remoto.

**(b) Tareas CPU (`hashear` de 1.5 M iteraciones) con 8 procesos:**

| K tareas | Tiempo total | Relación con K=1 |
|---------:|-------------:|-----------------:|
| 1 | 1.12 s | ×1.0 |
| 2 | 1.23 s | ×1.1 |
| 4 | 1.55 s | ×1.4 |
| 8 | 2.56 s | ×2.3 |
| 16 | 5.30 s | ×4.7 |

Acá el techo es **el número de procesos/núcleos (8)**, no los 10 workers. El
dato más claro es el salto de 8 a 16: el tiempo **se duplica exacto**
(2.56 → 5.30 s), porque con 16 tareas y 8 procesos hay dos tandas. Los 10
workers async pueden tomar 10 tareas, pero las que pasan de 8 quedan esperando
en la cola interna del executor.

Lo que **no** es ideal es el tramo de 1 a 8: en teoría debería ser plano
(8 tareas en 8 núcleos = el tiempo de una), y en cambio sube a ×2.3. Mi
interpretación es que los "8 CPUs" que ve el contenedor no son 8 núcleos
completos. Lo más probable es que sean núcleos con hyperthreading (dos hilos
de hardware comparten las unidades de cálculo de un mismo núcleo), o núcleos
de distinta potencia en los procesadores híbridos. Además, con más núcleos
activos el procesador baja la frecuencia de turbo, y WSL2 corre en una máquina
virtual que comparte la CPU con Windows. Es decir, el paralelismo real con
CPU-bound está acotado por el hardware físico, no por lo que dice `os.cpu_count()`.
Más workers o más procesos que núcleos **no ayudan**: solo agregarían cambios de
contexto.

**(c) ¿Qué se rompe primero con muchas tareas?**

| Tareas en memoria | `GET /estadisticas` | `GET /tareas` (todas) | POST/s |
|------------------:|--------------------:|----------------------:|-------:|
| 367 | 3.5 ms | 16.8 ms | — |
| 1.367 | 5.1 ms | 46.7 ms | 213 |
| 6.367 | 14.0 ms | **234.1 ms** | 211 |

Lo primero que se degrada no es la cola sino **listar**: `GET /tareas` sin
límite serializa a JSON todas las tareas, es O(n) y es **CPU dentro del event
loop**. Con 6.000 tareas ya bloquea el loop casi un cuarto de segundo, y crece
lineal (×14 de 367 a 6.367 tareas): es el mismo problema de la medición 2,
pero escondido en un endpoint. Por eso `/tareas` tiene `limite` (default 1000).
`/estadisticas` también es O(n) pero mucho más barato porque no serializa
cada tarea. El POST se mantiene estable en ~210 por segundo, limitado por el
costo de cada request HTTP a través de Docker/WSL y no por la cola.

Si llegan **10.000 tareas de golpe**: entran hasta `COLA_MAX` (10.000) y las
siguientes reciben `429`; los 10 workers las van procesando en tandas. Lo que
se rompería, en orden: (1) la latencia de listar, (2) la memoria, porque las
tareas terminadas nunca se borran, (3) si fueran 10.000 `descargar`, los
descriptores de archivo, porque el límite de conexiones es por tarea
(`MAX_CONEXIONES` por cliente) y no global.

---

## 5. Conceptos del curso aplicados

| Concepto (clase) | Dónde está en el código |
|------------------|-------------------------|
| **Sockets TCP y servidores concurrentes** (13-14) | uvicorn abre un socket que escucha en `0.0.0.0:8000`. En vez de un thread o un `fork` por cliente (como en las clases 13-14), atiende a todos desde un solo hilo. `/lento` y la medición 1 lo muestran: 20 conexiones abiertas a la vez sin ningún thread. |
| **I/O multiplexing: `select`/`epoll`** (17) | El event loop es un `epoll` en un `while True`. Cuando un worker hace `await cliente.get(url)`, httpx registra el socket en el selector del loop y suspende la corrutina; el loop la despierta cuando `epoll` dice que el socket es legible. En la medición 4.2 se ve el revés: si nadie vuelve a llamar a `epoll`, los requests quedan en el backlog del socket. |
| **De `yield` a asyncio: corrutinas** (18) | Cada worker es un generador con esteroides: `await cola.get()` es como un `yield` que le devuelve el control al loop, y el `put_nowait` del POST es el `send()` que lo reanuda. Por eso "un `await` = un punto donde puedo ser interrumpido" y "sin `await` no hay cambio de contexto", que es todo el problema del modo `bloqueante`. |
| **Productor-consumidor** (11) | El POST es el productor y los workers son los consumidores sobre una `asyncio.Queue` compartida. Es el mismo patrón de la clase 11 con threads y `queue.Queue`, pero con corrutinas: no hace falta lock porque el loop no interrumpe entre `await`s, y la cola acotada da backpressure (429) como el `maxsize` de la clase 11. |
| **HTTP y FastAPI** (19) | Verbos con semántica correcta: `POST` crea (202 Accepted porque el trabajo es asíncrono), `GET` es idempotente, `DELETE` cancela. Validación con pydantic (unión discriminada por `tipo` → 422 automático). Códigos 404/409/429/503 según el caso. `lifespan` para inicializar y apagar recursos. |
| **Asyncio en red** (20) | `httpx.AsyncClient` como context manager asíncrono, pool de conexiones, timeouts de red separados del timeout de la tarea. `TaskGroup` para lanzar N descargas y esperarlas. |
| **Asyncio avanzado: colas, cancelación, executors** (21) | `asyncio.Queue`, `Task.cancel()` y `CancelledError`, `asyncio.timeout()`, `asyncio.wait()` vs `await`, `run_in_executor`, `to_thread`, `add_signal_handler`. |
| **`concurrent.futures`** (22) | `ProcessPoolExecutor` con contexto `spawn`; `run_in_executor` envuelve el `concurrent.futures.Future` en un `asyncio.Future` para poder hacer `await`. `shutdown(cancel_futures=True)` en el apagado. |
| **Procesos, GIL, señales** (cuatrimestre) | Medición 4.2: threads vs procesos para CPU. `spawn` vs `fork` y la guarda `__main__`. SIGTERM/SIGINT, PID 1 en Docker y por qué el `CMD` va en forma exec. |
| **Context managers** (Bloque 0) | `async with httpx.AsyncClient`, `async with asyncio.TaskGroup`, `async with asyncio.timeout`, `@asynccontextmanager` del lifespan. Son los que garantizan la limpieza al cancelar. |

### Preguntas de la corrección

- **"Mostrame dónde tu código podría bloquear el event loop."** (1) El modo
  `bloqueante` de `hashear`, a propósito. (2) `GET /tareas` sin límite con
  decenas de miles de tareas (serializar JSON es CPU en el loop, medición
  4.3c). (3) `estadisticas()` recorre todas las tareas, O(n), aunque es barato.
  (4) La escritura del volcado iría en el loop, por eso va a `to_thread`. (5)
  Cualquier librería bloqueante que se agregue sin pensar (`requests`,
  `time.sleep`, leer un archivo grande).
- **"¿Qué pasa si cancelo una tarea justo a mitad de una descarga?"** El
  `CancelledError` aparece en el `await` donde esté cada descarga hija (leyendo
  el socket, esperando conexión). El TaskGroup cancela a todas las hermanas y
  espera a que terminen; después `AsyncClient.__aexit__` cierra las conexiones.
  Lo que ya se descargó se pierde (no se guardan resultados parciales) y la
  tarea queda `cancelada`. Lo prueba `test_cancelar_descarga_cierra_cliente`.
- **"¿Por qué tu pool no necesita un Lock?"** Un solo hilo; cambio de
  corrutina solo en `await`; las secciones críticas no tienen `await` adentro.
- **"Si te mando 10.000 tareas de golpe, ¿qué se rompe primero?"** Ver 4.3c.
- **"¿Qué diferencia hay entre `gather` y un `for` con `await`?"** El `for`
  espera cada corrutina antes de crear la siguiente: tiempo = suma. `gather`
  (o `TaskGroup`) las convierte en Tasks que corren todas a la vez: tiempo =
  máximo. Medición 4.1: 13.1 s vs 1.16 s con las mismas 20 URLs. Diferencia
  extra: si una falla, `gather` sin `return_exceptions` propaga la primera
  excepción pero **no cancela** a las demás; `TaskGroup` sí las cancela.
- **"¿Por qué `time.sleep()` en una corrutina es un bug y `asyncio.sleep()`
  no?"** `time.sleep` duerme al **hilo**, y el hilo es el loop: se frena todo.
  `asyncio.sleep` es una corrutina que agenda un timer en el loop y hace
  `yield`; el loop sigue con otras cosas y la despierta cuando vence. Es la
  medición 4.2 en chiquito.

---

## 6. Limitaciones conocidas

- **Todo en memoria y sin límite de crecimiento**: las tareas terminadas no se
  borran nunca. Con millones de tareas se agotaría la RAM. Faltaría una
  política de expiración (por ejemplo, borrar terminadas después de N minutos).
- **El volcado no se restaura**: al arrancar se reporta, pero las tareas
  pendientes del volcado anterior no se re-encolan.
- **Cancelar un hash no es instantáneo**: el bloque que ya está en el proceso
  hijo termina igual (~0.15 s). Un proceso no se puede interrumpir sin matarlo.
- **El modo `bloqueante` es un bug expuesto** a propósito para la medición. En
  producción no debería estar en la API.
- **Límite de conexiones por tarea, no global**: muchas tareas `descargar`
  simultáneas pueden abrir muchísimos sockets. Haría falta un semáforo global.
- **`GET /tareas` es O(n) en el loop** (ver 4.3c) y no tiene paginación real.
- **Un solo proceso de API**: no escala horizontalmente. Con varias réplicas
  el estado en memoria no se compartiría (habría que pasar a Redis/Celery).
- **No hay reintentos ni prioridades**: una tarea fallida queda fallida, y la
  cola es FIFO estricta.
- **`/lento`** es un endpoint auxiliar de pruebas y no debería existir en
  producción.
- **`TimeoutError` ambiguo**: si una tarea lanzara internamente su propio
  `TimeoutError`, se reportaría como timeout de la tarea.

---

## 7. Lo que aprendí

Lo que más me sirvió del TP fue la medición del event loop bloqueado. En las
clases había quedado claro que asyncio es cooperativo, pero lo tenía como una
frase de teoría. Cuando corrí el hash en modo bloqueante y vi que un servidor
que respondía en 6 ms pasó a contestar 2 requests en 3.4 segundos, sin tirar
ningún error ni nada en los logs, entendí el problema de verdad: en asyncio un
error de diseño no explota, el servidor simplemente deja de existir mientras
dura. También me llamó la atención la fila de threads. Esperaba que fuera igual
de buena que la de procesos, y ver que la latencia se multiplicaba por 2.5 fue
la primera vez que el GIL dejó de ser algo abstracto para mí y pasó a ser un
número que podía medir.

Lo que más me costó fue la cancelación. Yo pensaba que `cancel()` "mataba" la
tarea en el momento, y en realidad solo mete un `CancelledError` en el próximo
`await`. Eso significa que si la tarea no tiene ningún `await` (como el hash en
el executor) no se puede cortar, y por eso terminé partiendo el hash en
bloques. También tuve que entender por qué el worker no podía hacer
`await handle` directamente: si cancelaba la tarea, la excepción le llegaba al
worker como si lo hubieran cancelado a él. Separar la Task del trabajo de la
del worker y esperarla con `asyncio.wait` fue lo que hizo que todo cerrara
bien, y los context managers (`async with`) resultaron ser lo que garantiza
que no queden conexiones abiertas cuando se cancela una descarga.

Por último, las mediciones me enseñaron a no confiar en lo que "debería" pasar.
La teoría decía que con 8 procesos, 8 hashes tardan lo mismo que uno, y en mi
notebook tardaron más del doble. Lo mismo con la primera corrida de las
descargas, que salió el doble de lenta que las otras dos. Ninguna de las dos
cosas la habría visto si no medía, y me obligaron a pensar en cosas que no
tenía en cuenta, como que los núcleos que ve el contenedor no son todos iguales
o que la primera vez siempre paga un costo de arranque. Me quedo con que para
esperar da igual tener 10 workers o 1000, porque esperar es gratis, pero para
calcular el techo lo pone el hardware y no hay forma de saltearlo con código.
