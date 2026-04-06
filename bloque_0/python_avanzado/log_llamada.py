from functools import wraps
from datetime import datetime

def log_llamada(func):
    """
    Decorador que loguea llamadas a funciones con timestamp.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"[{ahora}] Llamando a {func.__name__}{args, kwargs}")

        resultado = func(*args, **kwargs)

        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ahora}] {func.__name__} retornó {resultado}")

        return resultado

    return wrapper


if __name__ == "__main__":
    @log_llamada
    def sumar(a, b):
        return a + b

    sumar(3, 5)