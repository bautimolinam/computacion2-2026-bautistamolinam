import argparse
import sys
import glob

parser = argparse.ArgumentParser(description="Mini grep")

parser.add_argument("patron", help="Texto a buscar")
parser.add_argument("archivos", nargs="*", help="Archivos")

parser.add_argument("-i", "--ignore-case", action="store_true")
parser.add_argument("-n", "--line-number", action="store_true")
parser.add_argument("-c", "--count", action="store_true")
parser.add_argument("-v", "--invert", action="store_true")

args = parser.parse_args()

def buscar_lineas(lineas, nombre_archivo):
    contador = 0

    for i, linea in enumerate(lineas, start=1):
        texto = linea.rstrip()

        comparar = texto.lower() if args.ignore_case else texto
        patron = args.patron.lower() if args.ignore_case else args.patron

        coincide = patron in comparar

        if args.invert:
            coincide = not coincide

        if coincide:
            contador += 1

            if not args.count:
                prefijo = ""
                if args.line_number or len(args.archivos) > 1:
                    prefijo += f"{nombre_archivo}:{i}: "

                print(prefijo + texto)

    if args.count:
        print(f"{nombre_archivo}: {contador} coincidencias")

    return contador

total = 0

if not args.archivos:
    if not sys.stdin.isatty():
        lineas = sys.stdin.readlines()
        total += buscar_lineas(lineas, "")
    else:
        print("Error: Debe especificar archivos o usar stdin")
        sys.exit(1)
else:
    archivos_expandido = []
    for patron in args.archivos:
        archivos_expandido.extend(glob.glob(patron))

    for archivo in archivos_expandido:
        try:
            with open(archivo, "r", encoding="utf-8") as f:
                lineas = f.readlines()
                total += buscar_lineas(lineas, archivo)
        except:
            print(f"Error: No se puede leer '{archivo}'")
            sys.exit(1)

if args.count and len(args.archivos) > 1:
    print(f"Total: {total} coincidencias")