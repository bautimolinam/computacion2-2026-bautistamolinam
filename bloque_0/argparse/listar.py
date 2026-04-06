import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description="Lista archivos de un directorio")

parser.add_argument(
    "directorio",
    nargs="?",
    default=".",
    help="Directorio a listar"
)

parser.add_argument("-a", "--all", action="store_true", help="Incluir archivos ocultos")

parser.add_argument("--extension", help="Filtrar por extensión")

args = parser.parse_args()

path = Path(args.directorio)

if not path.exists():
    print("Error: Directorio no existe")
    exit(1)

for item in path.iterdir():
    nombre = item.name

    if not args.all and nombre.startswith("."):
        continue

    if args.extension and item.is_file():
        if not nombre.endswith(args.extension):
            continue

    if item.is_dir():
        print(nombre + "/")
    else:
        print(nombre)