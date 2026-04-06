def fibonacci(limite=None):
    """
    Generador de Fibonacci.
    Si se pasa limite, genera hasta ese valor.
    """
    a, b = 0, 1

    while True:
        if limite is not None and a > limite:
            break
        yield a
        a, b = b, a + b


if __name__ == "__main__":
    for n in fibonacci():
        if n > 50:
            break
        print(n)

    print(list(fibonacci(100)))