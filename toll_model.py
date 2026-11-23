"""
Simplified bridge-toll model: equilibrium, economics and toll optimization.

Matches Bridge_Toll_Simplified_Model.docx and solver/simple_model.py (same equations and defaults).

  * Traffic equilibrium (Steps 3-4): elastic-demand Wardrop equilibrium on two routes
    (tolled bridge + free route, BPR travel times), solved in Python by bisection on the trip cost mu.
  * Economics (Step 5): crossings, revenue, collection cost, profit, consumer surplus, total travel
    cost TC, cost of trips given up X, services returned S, net toll account F.
  * Scenarios (Step 6): 1 operator profit, 2 social welfare, 3 total-cost minimization with the
    finance constraint (23).
  * Present bias (Steps 7-8): weight 1 on the deciding administration's own term, eps afterwards.
    eps = 1 -> one plan for the whole horizon (24); eps < 1 -> naive re-planning each term (25).

The toll choice is a small mixed-integer program: binary x[k, j] = 1 if term k charges toll level j,
with "once tolling starts it does not stop" and the capital cost charged in the start year.
It can be solved with Gurobi (gurobipy), Pyomo + HiGHS, or IBM CPLEX (docplex); an exhaustive
enumeration is also provided as a check.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field, asdict

import pandas as pd


# ----------------------------------------------------------------------------------------- inputs
@dataclass
class Params:
    H: int = 16                 # planning horizon (years)
    eta: int = 4                # length of one administration term (years)
    r: float = 0.05             # interest rate
    Q0_peak: float = 7500.0     # potential trips per hour in year 0, peak
    Q0_off: float = 2500.0      # potential trips per hour in year 0, off-peak
    n_peak: float = 4.0         # peak hours per day
    n_off: float = 12.0         # off-peak hours per day
    Nd: float = 250.0           # weekdays per year
    g: float = 0.06             # yearly demand growth
    VOT: float = 15.0           # value of travel time ($/hour)
    omega: float = 0.08         # demand sensitivity (per $)
    t01: float = 0.25           # bridge free-flow time (hours)
    t02: float = 0.45           # free route free-flow time (hours)
    kap1: float = 3000.0        # bridge capacity (veh/hour)
    kap2: float = 3000.0        # free route capacity (veh/hour)
    o1: float = 2.0             # bridge operating cost ($/trip)
    o2: float = 3.0             # free route operating cost ($/trip)
    bpr_a: float = 0.15         # BPR alpha
    bpr_b: float = 4.0          # BPR beta
    CP: float = 10e6            # capital cost of the toll system ($, paid in start year)
    CA0: float = 1.0e6          # fixed yearly collection cost ($)
    cA: float = 0.10            # collection cost per crossing ($)
    nuS: float = 0.9            # Scenario 3: share of net revenue returned as services
    theta: float = 1.0          # Scenario 3: value to travelers of $1 of services
    tolls: tuple = (0.0, 1.0, 2.0, 3.0, 4.0)

    @property
    def K(self) -> int:
        return math.ceil(self.H / self.eta)

    @property
    def periods(self):
        return (("peak", self.Q0_peak, self.n_peak), ("off-peak", self.Q0_off, self.n_off))


SCENARIOS = {1: "1 Operator profit", 2: "2 Social welfare", 3: "3 Total cost (agency)"}


# ------------------------------------------------------------------------------ traffic equilibrium
class Model:
    def __init__(self, p: Params):
        self.p = p
        self._ue = {}
        self._year = {}
        self.base = {y: self.year(y, 0.0, start=False) for y in range(p.H)}

    def ue(self, Qbar: float, tau: float):
        """Elastic-demand Wardrop equilibrium on two routes (eqs. 6-9). Returns v1, v2, q, mu."""
        key = (round(Qbar, 1), tau)
        if key in self._ue:
            return self._ue[key]
        p = self.p
        Qbar = key[0]

        def flow(mu, t0, kap, o, toll):
            x = (mu - o - toll) / (p.VOT * t0) - 1.0
            return 0.0 if x <= 0 else kap * (x / p.bpr_a) ** (1.0 / p.bpr_b)

        lo, hi = 0.0, 500.0
        for _ in range(80):
            mu = 0.5 * (lo + hi)
            f = (flow(mu, p.t01, p.kap1, p.o1, tau) + flow(mu, p.t02, p.kap2, p.o2, 0.0)
                 - Qbar * math.exp(-p.omega * mu))
            lo, hi = (mu, hi) if f < 0 else (lo, mu)
        mu = 0.5 * (lo + hi)
        v1 = flow(mu, p.t01, p.kap1, p.o1, tau)
        v2 = flow(mu, p.t02, p.kap2, p.o2, 0.0)
        out = (v1, v2, v1 + v2, mu)
        self._ue[key] = out
        return out

    # ------------------------------------------------------------------------------- one year
    def year(self, y: int, tau: float, start: bool) -> dict:
        """One year (eqs. 10-17). Collection cost applies whenever tau > 0; capital cost in start year."""
        key = (y, tau, start)
        if key in self._year:
            return self._year[key]
        p = self.p
        on = tau > 0
        V = R = CS = TC = X = 0.0
        peak = {}
        for name, Q0, n in p.periods:
            Qb = Q0 * (1 + p.g) ** y                                     # (5)
            v1, v2, q, mu = self.ue(Qb, tau)
            k = p.Nd * n
            V += k * v1
            R += k * tau * v1
            CS += k * q / p.omega
            TC += k * q * mu
            X += k * ((Qb - q) / p.omega - q * mu)
            if name == "peak":
                peak = dict(peak_bridge_vph=v1, peak_free_vph=v2, peak_trips_vph=q, peak_trip_cost=mu)
        Cc = (p.CA0 + p.cA * V) if on else 0.0
        Pi = R - Cc - (p.CP if start else 0.0)
        S = p.nuS * max(0.0, R - Cc)
        out = dict(V=V, R=R, CS=CS, TC=TC, X=X, Cc=Cc, Pi=Pi, S=S, F=Pi - S, **peak)
        self._year[key] = out
        return out

    def G(self, sc: int, o: dict, y: int) -> float:
        """Yearly payoff against no toll (eqs. 18-20)."""
        b = self.base[y]
        if sc == 1:
            return o["Pi"]
        if sc == 2:
            return (o["CS"] - b["CS"]) + o["Pi"]
        return -((o["TC"] + o["X"] - self.p.theta * o["S"]) - (b["TC"] + b["X"]))

    # ------------------------------------------------------------------------------- plans
    def term_start(self, k):
        return k * self.p.eta

    def weight(self, y, k, eps):
        """Weight that the administration of term k puts on year y (Step 7)."""
        p = self.p
        t0, t1 = k * p.eta, min((k + 1) * p.eta, p.H)
        if y < t0:
            return 0.0
        return (1.0 if y < t1 else eps) * (1 / (1 + p.r)) ** (y - t0)

    def run(self, plan, sc):
        """Year-by-year outcome of a toll plan (one toll per term)."""
        p = self.p
        rows, started = [], False
        for y in range(p.H):
            tau = plan[y // p.eta]
            start = tau > 0 and not started
            started = started or tau > 0
            o = self.year(y, tau, start)
            rows.append((self.G(sc, o, y), o, tau, start))
        return rows

    def value(self, plan, sc, k=0, eps=1.0):
        return sum(self.weight(y, k, eps) * g for y, (g, *_rest) in enumerate(self.run(plan, sc)))

    def finance(self, plan):
        d = 1 / (1 + self.p.r)
        return sum(d ** y * o["F"] for y, (_, o, *_r) in enumerate(self.run(plan, 3)))

    def table(self, plan, sc):
        rows = []
        b = self.base
        for y, (g, o, tau, start) in enumerate(self.run(plan, sc)):
            rows.append(dict(
                year=y, term=y // self.p.eta + 1, toll=tau, start_year=start,
                payoff_G=g,
                bridge_crossings=o["V"], bridge_crossings_no_toll=b[y]["V"],
                revenue=o["R"], collection_cost=o["Cc"],
                capital_cost=self.p.CP if start else 0.0, profit=o["Pi"],
                consumer_surplus_change=o["CS"] - b[y]["CS"],
                TC=o["TC"], TC_no_toll=b[y]["TC"], X=o["X"], X_no_toll=b[y]["X"],
                services_S=o["S"], toll_account_F=o["F"],
                peak_bridge_vph=o["peak_bridge_vph"], peak_free_route_vph=o["peak_free_vph"],
                peak_trips_vph=o["peak_trips_vph"], peak_trip_cost=o["peak_trip_cost"],
                peak_bridge_vph_no_toll=b[y]["peak_bridge_vph"],
                peak_free_route_vph_no_toll=b[y]["peak_free_vph"],
            ))
        return pd.DataFrame(rows)


# ------------------------------------------------------------------------------ optimization
def _coefficients(m: Model, sc: int):
    """Payoff and toll-account coefficients per (year, toll level), without the capital cost,
    plus the capital-cost coefficient of the payoff in the start year."""
    p = m.p
    g, f = {}, {}
    for y in range(p.H):
        for j, tau in enumerate(p.tolls):
            o = m.year(y, tau, start=False)
            g[y, j] = m.G(sc, o, y)
            f[y, j] = o["F"]
    cap_in_payoff = -p.CP if sc in (1, 2) else 0.0       # Scenario 3's payoff has no capital term
    return g, f, cap_in_payoff


def _build_and_solve(m: Model, sc: int, k_view: int, eps: float, fixed: tuple, backend: str):
    """Best plan from the view of term k_view, with terms < k_view fixed. Returns plan or None."""
    p = m.p
    K, J = p.K, range(len(p.tolls))
    pos = [j for j in J if p.tolls[j] > 0]
    has_zero = any(t == 0 for t in p.tolls)
    g, f, capg = _coefficients(m, sc)
    d = 1 / (1 + p.r)
    w = {y: m.weight(y, k_view, eps) for y in range(p.H)}
    term_years = {k: [y for y in range(p.H) if y // p.eta == k] for k in range(K)}

    obj_x = {(k, j): sum(w[y] * g[y, j] for y in term_years[k]) for k in range(K) for j in J}
    obj_z = {k: w[term_years[k][0]] * capg for k in range(K)}
    fin_x = {(k, j): sum(d ** y * f[y, j] for y in term_years[k]) for k in range(K) for j in J}
    fin_z = {k: -p.CP * d ** term_years[k][0] for k in range(K)}

    if backend == "gurobi":
        import gurobipy as gp
        from gurobipy import GRB
        mdl = gp.Model("toll")
        mdl.Params.OutputFlag = 0
        x = mdl.addVars(K, len(p.tolls), vtype=GRB.BINARY, name="x")
        z = mdl.addVars(K, lb=0.0, ub=1.0, name="z")          # 1 in the term tolling starts
        for k in range(K):
            mdl.addConstr(x.sum(k, "*") == 1)
            on_k = gp.quicksum(x[k, j] for j in pos)
            on_prev = gp.quicksum(x[k - 1, j] for j in pos) if k > 0 else 0
            mdl.addConstr(on_k >= on_prev)                     # once started, stays positive (eq. 3)
            mdl.addConstr(z[k] == on_k - on_prev)
        for k, tau in enumerate(fixed):
            mdl.addConstr(x[k, p.tolls.index(tau)] == 1)
        if sc == 3:
            mdl.addConstr(gp.quicksum(fin_x[k, j] * x[k, j] for k in range(K) for j in J)
                          + gp.quicksum(fin_z[k] * z[k] for k in range(K)) >= 0)   # (23)
        mdl.setObjective(gp.quicksum(obj_x[k, j] * x[k, j] for k in range(K) for j in J)
                         + gp.quicksum(obj_z[k] * z[k] for k in range(K)), GRB.MAXIMIZE)
        mdl.Params.MIPGap = 1e-9
        mdl.optimize()
        if mdl.Status != GRB.OPTIMAL:
            return None
        return tuple(p.tolls[max(J, key=lambda j: x[k, j].X)] for k in range(K))

    if backend == "pyomo":
        import pyomo.environ as pe
        mdl = pe.ConcreteModel()
        mdl.K, mdl.J = pe.Set(initialize=range(K)), pe.Set(initialize=list(J))
        mdl.x = pe.Var(mdl.K, mdl.J, domain=pe.Binary)
        mdl.z = pe.Var(mdl.K, bounds=(0, 1))
        on = lambda k: sum(mdl.x[k, j] for j in pos) if k >= 0 else 0
        mdl.one = pe.Constraint(mdl.K, rule=lambda b, k: sum(b.x[k, j] for j in J) == 1)
        mdl.mono = pe.Constraint(mdl.K, rule=lambda b, k: on(k) >= on(k - 1) if k > 0 else pe.Constraint.Skip)
        mdl.zdef = pe.Constraint(mdl.K, rule=lambda b, k: b.z[k] == on(k) - on(k - 1))
        mdl.fix = pe.ConstraintList()
        for k, tau in enumerate(fixed):
            mdl.fix.add(mdl.x[k, p.tolls.index(tau)] == 1)
        if sc == 3:
            mdl.fin = pe.Constraint(expr=sum(fin_x[k, j] * mdl.x[k, j] for k in range(K) for j in J)
                                    + sum(fin_z[k] * mdl.z[k] for k in range(K)) >= 0)
        mdl.obj = pe.Objective(expr=sum(obj_x[k, j] * mdl.x[k, j] for k in range(K) for j in J)
                               + sum(obj_z[k] * mdl.z[k] for k in range(K)), sense=pe.maximize)
        res = pe.SolverFactory("appsi_highs").solve(mdl, load_solutions=False)
        if res.solver.termination_condition != pe.TerminationCondition.optimal:
            return None
        mdl.solutions.load_from(res)
        return tuple(p.tolls[max(J, key=lambda j: pe.value(mdl.x[k, j]))] for k in range(K))

    if backend == "cplex":
        from docplex.mp.model import Model as CpxModel
        mdl = CpxModel(name="toll")
        x = {(k, j): mdl.binary_var(name=f"x_{k}_{j}") for k in range(K) for j in J}
        z = {k: mdl.continuous_var(lb=0, ub=1, name=f"z_{k}") for k in range(K)}
        on = lambda k: mdl.sum(x[k, j] for j in pos) if k >= 0 else 0
        for k in range(K):
            mdl.add_constraint(mdl.sum(x[k, j] for j in J) == 1)
            if k > 0:
                mdl.add_constraint(on(k) >= on(k - 1))
            mdl.add_constraint(z[k] == on(k) - on(k - 1))
        for k, tau in enumerate(fixed):
            mdl.add_constraint(x[k, p.tolls.index(tau)] == 1)
        if sc == 3:
            mdl.add_constraint(mdl.sum(fin_x[k, j] * x[k, j] for k in range(K) for j in J)
                               + mdl.sum(fin_z[k] * z[k] for k in range(K)) >= 0)
        mdl.maximize(mdl.sum(obj_x[k, j] * x[k, j] for k in range(K) for j in J)
                     + mdl.sum(obj_z[k] * z[k] for k in range(K)))
        mdl.parameters.mip.tolerances.mipgap = 1e-9
        sol = mdl.solve()
        if sol is None:
            return None
        return tuple(p.tolls[max(J, key=lambda j: sol.get_value(x[k, j]))] for k in range(K))

    # exhaustive enumeration (check)
    best, bestv = None, -math.inf
    for pl in itertools.product(p.tolls, repeat=K):
        if pl[:len(fixed)] != tuple(fixed):
            continue
        st, ok = False, True
        for t in pl:
            if st and t == 0:
                ok = False
            st = st or t > 0
        if not ok or (sc == 3 and m.finance(pl) < 0):
            continue
        v = m.value(pl, sc, k_view, eps)
        if v > bestv + 1e-6:
            best, bestv = pl, v
    return best


def available_backends():
    out = []
    try:
        import gurobipy as gp
        e = gp.Env(empty=True); e.setParam("OutputFlag", 0); e.start(); e.dispose()
        out.append("gurobi")
    except Exception:
        pass
    try:
        import pyomo.environ as pe
        if pe.SolverFactory("appsi_highs").available():
            out.append("pyomo")
    except Exception:
        pass
    try:
        from docplex.mp.model import Model as CpxModel
        CpxModel().solve()
        out.append("cplex")
    except Exception:
        pass
    out.append("enumeration")
    return out


def solve(m: Model, sc: int, eps: float, backend: str = "gurobi"):
    """eps = 1: one plan for the whole horizon (24). eps < 1: naive re-planning each term (25)."""
    p = m.p
    if eps >= 1.0:
        return _build_and_solve(m, sc, 0, 1.0, (), backend)
    done = ()
    for k in range(p.K):
        best = _build_and_solve(m, sc, k, eps, done, backend)
        if best is None:
            return None
        done = done + (best[k],)
    return done


def summarize(m: Model, sc: int, eps: float, plan, plan_tc=None) -> dict:
    p = m.p
    if plan is None:
        return dict(scenario=SCENARIOS[sc], eps=eps, toll_path="infeasible", start_year=None,
                    value_eps1=None, toll_account_pv=None, cost_of_present_bias=None)
    start = next((k * p.eta for k, t in enumerate(plan) if t > 0), None)
    v = m.value(plan, sc, 0, 1.0)
    cpb = (m.value(plan_tc, sc, 0, 1.0) - v) if plan_tc is not None else None
    return dict(scenario=SCENARIOS[sc], eps=eps,
                toll_path=", ".join("none" if t == 0 else f"${t:g}" for t in plan),
                start_year=start, value_eps1=v, toll_account_pv=m.finance(plan),
                cost_of_present_bias=cpb, plan=plan)


if __name__ == "__main__":
    import sys
    be = sys.argv[1] if len(sys.argv) > 1 else available_backends()[0]
    m = Model(Params())
    print("backend:", be)
    for sc in (1, 2, 3):
        tc = solve(m, sc, 1.0, be)
        for eps in (1.0, 0.5, 0.2):
            pl = tc if eps == 1.0 else solve(m, sc, eps, be)
            s = summarize(m, sc, eps, pl, tc)
            print(f"Scenario {sc}, eps={eps}: toll per term {pl}, start year {s['start_year']}, "
                  f"value (eps=1) ${s['value_eps1']/1e6:,.1f}M, toll account ${s['toll_account_pv']/1e6:,.1f}M")
