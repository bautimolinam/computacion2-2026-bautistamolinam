import sys

suma = 0

for arg in sys.argv[1:]:
    try:
        suma += float(arg)
    except ValueError:
        print(f"Error: '{arg}' no es un número")
        sys.exit(1)

print(f"Suma: {suma}")