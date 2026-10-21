from .base import SolveResult
from .bnb import BBResult, solve_bnb
from .greedy import solve_greedy


def solve_greedy_sticky(problem):
    return solve_greedy(problem, sticky=True)


__all__ = ["SolveResult", "solve_greedy", "solve_greedy_sticky", "solve_bnb", "BBResult"]
