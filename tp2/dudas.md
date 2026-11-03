# Dudas



1. **Cancelación de CPU-bound.** Partí el hash en bloques para poder cancelarlo
   entre bloque y bloque. ¿Hay una forma más directa de cortar un trabajo que ya
   está corriendo en un `ProcessPoolExecutor` sin matar el proceso (y sin
   perder el pool entero)?
2. **Tamaño del bloque.** Bloques más chicos hacen la cancelación más rápida
   pero agregan overhead de IPC (pickle ida y vuelta por bloque). ¿Hay una regla
   práctica para elegirlo, o siempre hay que medir?
3. **Señales y uvicorn.** Reemplacé el handler de uvicorn con
   `loop.add_signal_handler`. ¿Es preferible eso, o confiar en el handler de
   uvicorn y hacer todo en el shutdown del lifespan?
4. **`TimeoutError` ambiguo.** `asyncio.timeout()` y una librería interna pueden
   lanzar el mismo `TimeoutError`. ¿Conviene usar `timeout.expired()` del
   context manager para distinguirlos?
5. **Escalar a varias réplicas.** Si hubiera más de un proceso de API, ¿el
   paso natural es Redis como cola, o directamente Celery?
