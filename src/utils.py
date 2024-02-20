from decimal import Decimal
from math import ceil


def round_down(n, d=4) -> Decimal:
    return truncate(n, d)


def round_up(n, d=4) -> Decimal:
    f = int("1" + ("0" * d))
    return truncate(ceil(n * f) / f, d)


def truncate(f, n) -> Decimal:
    """Truncates/pads a float f to n decimal places without rounding"""
    s = "{}".format(f)
    if "e" in s or "E" in s:
        return Decimal("{0:.{1}f}".format(f, n))
    i, p, d = s.partition(".")
    return Decimal(".".join([i, (d + "0" * n)[:n]]))
