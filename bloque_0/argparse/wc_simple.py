import sys

if len(sys.argv) < 2:
    print("Error: Debe especificar un archivo")
    sys.exit(1)

archivo = sys.argv[1]

try:
    with open(archivo, "r", encoding="utf-8") as f:
        lineas = f.readlines()
        print(f"{len(lineas)} líneas")
except FileNotFoundError:
    print(f"Error: No se puede leer '{archivo}'")
    sys.exit(1)