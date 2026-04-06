import os
import argparse

def parse_size(size_str):
    unidad = size_str[-1]
    valor = float(size_str[:-1])

    if unidad == "K":
        return valor * 1024
    elif unidad == "M":
        return valor * 1024**2
    elif unidad == "G":
        return valor * 1024**3
    else:
        return float(size_str)

parser = argparse.ArgumentParser()
parser.add_argument("directorio")
parser.add_argument("--min-size", required=True)
parser.add_argument("--type")
parser.add_argument("--top", type=int)

args = parser.parse_args()

min_size = parse_size(args.min_size)

resultados = []

for root, dirs, files in os.walk(args.directorio):
    for name in files:
        path = os.path.join(root, name)

        try:
            size = os.path.getsize(path)
        except:
            continue

        if size < min_size:
            continue

        if args.type == "f":
            if not os.path.isfile(path):
                continue

        resultados.append((path, size))

# Ordenar
resultados.sort(key=lambda x: x[1], reverse=True)

# Top N
if args.top:
    resultados = resultados[:args.top]

total = 0
count = 0

for path, size in resultados:
    print(f"{path} ({size} bytes)")
    total += size
    count += 1

print(f"Total: {count} archivos, {total} bytes")