import os
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("directorio")
parser.add_argument("--delete", action="store_true")
parser.add_argument("--quiet", action="store_true")

args = parser.parse_args()

rotos = []

for root, dirs, files in os.walk(args.directorio):
    for name in files:
        path = os.path.join(root, name)

        if os.path.islink(path):
            if not os.path.exists(path):
                rotos.append(path)

if args.quiet:
    print(len(rotos))
    exit(0)

print("Enlaces rotos encontrados:")

for link in rotos:
    destino = os.readlink(link)
    print(f"  {link} -> {destino} (no existe)")

print(f"\nTotal: {len(rotos)} enlaces rotos")

if args.delete:
    for link in rotos:
        confirm = input(f"¿Eliminar {link}? [s/N]: ")
        if confirm.lower() == "s":
            os.remove(link)
            print("Eliminado")