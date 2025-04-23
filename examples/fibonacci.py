def fib(n: int) -> int:
    if n <= 0:
        return 0
    elif n == 1:
        return 1
    else:
        return fib(n - 1) + fib(n - 2)
if __name__ == "__main__":
    n = 12
    print(fib(n)) # 1, 1, 2, 3, 5, 8, 13, 21, 34, 55