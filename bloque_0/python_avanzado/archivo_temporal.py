import os

class archivo_temporal:
    """
    Context manager que crea un archivo temporal y lo elimina al salir.
    """

    def __init__(self, nombre):
        self.nombre = nombre

    def __enter__(self):
        self.file = open(self.nombre, "w+")
        return self.file

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.file.close()
        if os.path.exists(self.nombre):
            os.remove(self.nombre)


if __name__ == "__main__":
    with archivo_temporal("test.txt") as f:
        f.write("Datos de prueba\n")
        f.write("Más datos\n")
        f.seek(0)
        print(f.read())

    import os
    assert not os.path.exists("test.txt")