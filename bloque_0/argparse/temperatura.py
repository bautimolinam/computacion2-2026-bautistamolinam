import argparse

parser = argparse.ArgumentParser(description="Convierte temperaturas")

parser.add_argument("valor", type=float, help="Temperatura a convertir")
parser.add_argument(
    "-t", "--to",
    choices=["celsius", "fahrenheit"],
    required=True,
    help="Unidad de destino"
)

args = parser.parse_args()

if args.to == "celsius":
    resultado = (args.valor - 32) * 5 / 9
    print(f"{args.valor}°F = {round(resultado, 2)}°C")
else:
    resultado = (args.valor * 9 / 5) + 32
    print(f"{args.valor}°C = {round(resultado, 2)}°F")