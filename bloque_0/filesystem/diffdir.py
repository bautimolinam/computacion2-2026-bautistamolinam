import os
import argparse

def listar(directorio):
    archivos = {}

    for root, dirs, files in os.walk(directorio):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, directorio)
            archivos[rel] = os.stat(path)

    return archivos

parser = argparse.ArgumentParser()
parser.add_argument("dir1")
parser.add_argument("dir2")

args = parser.parse_args()

a = listar(args.dir1)
b = listar(args.dir2)

solo_a = set(a) - set(b)
solo_b = set(b) - set(a)

print("Solo en dir1:")
for f in solo_a:
    print(" ", f)

print("\nSolo en dir2:")
for f in solo_b:
    print(" ", f)

print("\nArchivos comunes:")
for f in set(a) & set(b):
    if a[f].st_size != b[f].st_size:
        print(f"  {f} (modificado por tamaño)")