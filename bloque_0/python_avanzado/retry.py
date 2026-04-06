import time
from functools import wraps

def retry(max_attempts=3, delay=1, exceptions=(Exception,)):
    """
    Reintenta una función si falla.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for intento in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    print(f"Intento {intento}/{max_attempts} falló: {e}")
                    if intento < max_attempts:
                        time.sleep(delay)
                    else:
                        raise
        return wrapper
    return decorator


if __name__ == "__main__":
    import random

    @retry(max_attempts=3, delay=1)
    def test():
        if random.random() < 0.7:
            raise ConnectionError("Falló")
        return "OK"

    try:
        print(test())
    except Exception:
        print("Falló después de reintentos")