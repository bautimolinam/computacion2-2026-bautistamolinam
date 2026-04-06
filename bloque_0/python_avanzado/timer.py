import time

class Timer:
    def __init__(self, nombre=None):
        self.nombre = nombre
        self.elapsed = 0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.elapsed = time.time() - self.start
        if self.nombre:
            print(f"[Timer] {self.nombre}: {self.elapsed:.3f}s")


if __name__ == "__main__":
    with Timer("Procesamiento"):
        time.sleep(1)