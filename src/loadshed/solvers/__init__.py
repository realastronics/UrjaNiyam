from .base import SolveResult
from .bnb import BBResult, solve_bnb
from .greedy import solve_greedy
from .hybrid import HybridResult, solve_hybrid
from .minconflicts import LSResult, solve_min_conflicts


def solve_greedy_sticky(problem):
    return solve_greedy(problem, sticky=True)


__all__ = ["SolveResult", "solve_greedy", "solve_greedy_sticky", "solve_bnb", "BBResult",
           "solve_min_conflicts", "LSResult", "solve_hybrid", "HybridResult"]
