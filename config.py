"""
Single shared config for Exodus. Both the solver side and the platform side
import this — nobody hardcodes a tuning constant anywhere else.
"""

CONFIG = {
    "sigma": 0.8,               # per-person cost of a group of 2
    "upsilon": 2.5,             # per-person cost of being ungrouped
    "beta": 1.0,                # fairness weight on max penalty
    "p_cap": 0.9,               # no member's penalty may exceed this
    "grid_minutes": 5,          # departure time grid resolution
    "max_group": 3,             # cab capacity
    "accept_window_minutes": 20,  # time to accept/decline a proposal
    "decline_budget": 2,        # no-state-change declines before sit-out
}

ROUTES = [
    "COLLEGE_AIRPORT",
    "COLLEGE_STATION",
    "AIRPORT_COLLEGE",
    "STATION_COLLEGE",
]

STATUSES = ["PENDING", "GROUPED", "CONFIRMED", "SAT_OUT"]

STATES = ["FORMED", "CONFIRMED", "DISSOLVED"]

REASONS = ["PERSON", "TIME", "TOO_FEW", "PLANS_CHANGED", "TIMEOUT"]
