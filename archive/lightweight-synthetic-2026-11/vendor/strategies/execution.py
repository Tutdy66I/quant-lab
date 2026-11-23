"""Shared causal signals and modeled fills for research and paper accounts."""
import math

FEE = 0.001
SLIPPAGE = 0.0005


def crossover(prices, fast, slow, index):
    if not 1 <= fast < slow or not slow <= index < len(prices):
        raise ValueError("Insufficient warm-up or invalid MA windows")

    def difference(at):
        return sum(prices[at - fast + 1:at + 1]) / fast - sum(prices[at - slow + 1:at + 1]) / slow

    previous, current = difference(index - 1), difference(index)
    if previous <= 0 < current:
        return "buy"
    if previous >= 0 > current:
        return "sell"
    return "hold"


def _validate(amount, price, fee, slippage):
    if not all(math.isfinite(value) and value > 0 for value in (amount, price)):
        raise ValueError("Amount and price must be finite and positive")
    if not all(math.isfinite(value) and 0 <= value < 1 for value in (fee, slippage)):
        raise ValueError("Costs must be finite values in [0, 1)")


def buy_fill(capital, opening, fee=FEE, slippage=SLIPPAGE):
    _validate(capital, opening, fee, slippage)
    price = opening * (1 + slippage)
    commission = capital * fee
    return price, (capital - commission) / price, commission


def sell_fill(quantity, opening, fee=FEE, slippage=SLIPPAGE):
    _validate(quantity, opening, fee, slippage)
    price = opening * (1 - slippage)
    gross = quantity * price
    commission = gross * fee
    return price, gross - commission, commission
