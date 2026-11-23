"""Streamlit dashboard for the simplified bridge-toll model.  Run:  streamlit run app.py"""
import io

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import toll_model as tm

st.set_page_config(page_title="Bridge Toll Optimization", page_icon="🌉", layout="wide")

# ------------------------------------------------------------------------- black / white styling
st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"], .main, .block-container {
    background-color: #000000 !important; color: #FFFFFF !important; }
[data-testid="stSidebar"], [data-testid="stSidebar"] > div { background-color: #0b0b0b !important; }
h1, h2, h3, h4, h5, h6, p, label, span, li, div, .stMarkdown { color: #FFFFFF; }
[data-testid="stMetricValue"], [data-testid="stMetricLabel"] { color: #FFFFFF !important; }
[data-testid="stMetric"] { background:#111111; border:1px solid #2a2a2a; border-radius:8px; padding:10px 14px; }
input, textarea, [data-baseweb="select"] > div { background-color:#141414 !important; color:#FFFFFF !important; }
.stTabs [data-baseweb="tab"] { color:#FFFFFF; }
.stDownloadButton button, .stButton button { background:#141414; color:#FFFFFF; border:1px solid #4FA3FF; }
.stDownloadButton button:hover, .stButton button:hover { background:#4FA3FF; color:#000000; }
code { color:#9fd0ff; background:#111111; }
</style>
""", unsafe_allow_html=True)

PLOT = dict(template="plotly_dark", paper_bgcolor="#000000", plot_bgcolor="#000000",
            font=dict(color="#FFFFFF"), legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", yanchor="top", y=-0.18, x=0),
            margin=dict(l=10, r=10, t=50, b=40))
COLORS = ["#4FA3FF", "#FFB347", "#7CE38B", "#FF6B8B", "#C49BFF", "#5FE0E0"]
LABEL = {"gurobi": "Gurobi (gurobipy)", "pyomo": "Pyomo + HiGHS (open source)",
         "cplex": "IBM CPLEX (docplex)", "enumeration": "Enumeration (check, no solver)"}


def styled(fig, title=None, h=430):
    fig.update_layout(**PLOT, height=h, title=title)
    fig.update_xaxes(gridcolor="#222222", zerolinecolor="#444444")
    fig.update_yaxes(gridcolor="#222222", zerolinecolor="#444444")
    return fig


def csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


@st.cache_resource(show_spinner=False)
def backends():
    return tm.available_backends()


# ------------------------------------------------------------------------- sidebar: inputs
d = tm.Params()
sb = st.sidebar
sb.title("Inputs")

avail = backends()
missing = [b for b in ("gurobi", "pyomo", "cplex") if b not in avail]
backend = sb.selectbox("Optimization solver", avail, format_func=lambda b: LABEL[b])
if missing:
    sb.caption("Not available here: " + ", ".join(LABEL[b] for b in missing) + ".")

scen = sb.multiselect("Scenarios", [1, 2, 3], default=[1, 2, 3], format_func=lambda s: tm.SCENARIOS[s])
eps_txt = sb.text_input("Present-bias weights ε (comma-separated; 1 = no bias)", "1.0, 0.5, 0.2")
toll_txt = sb.text_input("Allowed toll levels ($, comma-separated; include 0 for 'no toll')", "0, 1, 2, 3, 4")

with sb.expander("Time and money", expanded=False):
    H = st.number_input("Horizon H (years)", 1, 60, d.H)
    eta = st.number_input("Term length η (years)", 1, 20, d.eta)
    r = st.number_input("Interest rate r", 0.0, 0.5, d.r, 0.005, format="%.3f")
with sb.expander("Demand", expanded=False):
    Q0p = st.number_input("Potential trips/hour, peak (year 0)", 0.0, 1e6, d.Q0_peak, 100.0)
    Q0o = st.number_input("Potential trips/hour, off-peak (year 0)", 0.0, 1e6, d.Q0_off, 100.0)
    npk = st.number_input("Peak hours per day", 0.0, 24.0, d.n_peak, 1.0)
    nof = st.number_input("Off-peak hours per day", 0.0, 24.0, d.n_off, 1.0)
    Nd = st.number_input("Weekdays per year", 1.0, 366.0, d.Nd, 1.0)
    g = st.number_input("Yearly demand growth g", -0.2, 0.5, d.g, 0.005, format="%.3f")
    omega = st.number_input("Demand sensitivity ω (per $)", 0.001, 1.0, d.omega, 0.005, format="%.3f")
    VOT = st.number_input("Value of time ($/hour)", 0.0, 500.0, d.VOT, 1.0)
with sb.expander("Routes (BPR)", expanded=False):
    t01 = st.number_input("Bridge free-flow time (min)", 0.1, 600.0, d.t01 * 60, 1.0) / 60
    kap1 = st.number_input("Bridge capacity (veh/hour)", 1.0, 1e6, d.kap1, 100.0)
    o1 = st.number_input("Bridge operating cost ($/trip)", 0.0, 100.0, d.o1, 0.5)
    t02 = st.number_input("Free route free-flow time (min)", 0.1, 600.0, d.t02 * 60, 1.0) / 60
    kap2 = st.number_input("Free route capacity (veh/hour)", 1.0, 1e6, d.kap2, 100.0)
    o2 = st.number_input("Free route operating cost ($/trip)", 0.0, 100.0, d.o2, 0.5)
    a = st.number_input("BPR α", 0.0, 5.0, d.bpr_a, 0.01)
    b = st.number_input("BPR β", 1.0, 10.0, d.bpr_b, 0.5)
with sb.expander("Toll system costs", expanded=False):
    CP = st.number_input("Capital cost ($M, paid in start year)", 0.0, 1e4, d.CP / 1e6, 0.5) * 1e6
    CA0 = st.number_input("Fixed collection cost ($M/year)", 0.0, 1e3, d.CA0 / 1e6, 0.1) * 1e6
    cA = st.number_input("Collection cost per crossing ($)", 0.0, 20.0, d.cA, 0.01)
with sb.expander("Scenario 3 (total cost)", expanded=False):
    nuS = st.number_input("Share of net revenue returned as services ν", 0.0, 1.0, d.nuS, 0.05)
    theta = st.number_input("Value to travelers of $1 of services θ", 0.0, 5.0, d.theta, 0.05)


def parse(txt):
    try:
        return sorted({float(v) for v in txt.replace(";", ",").split(",") if v.strip()})
    except ValueError:
        return None


eps_list, tolls = parse(eps_txt), parse(toll_txt)
if eps_list is None or tolls is None or not eps_list or not tolls:
    st.error("Could not read ε or toll levels. Use numbers separated by commas.")
    st.stop()
eps_list = sorted([e for e in eps_list if 0 <= e <= 1], reverse=True)
if any(t < 0 for t in tolls):
    st.error("Toll levels must be zero or positive.")
    st.stop()
K = -(-int(H) // int(eta))
if len(tolls) ** K > 2_000_000 and backend == "enumeration":
    st.warning("Enumeration would check too many plans; pick a MIP solver.")
    st.stop()

params = tm.Params(H=int(H), eta=int(eta), r=r, Q0_peak=Q0p, Q0_off=Q0o, n_peak=npk, n_off=nof, Nd=Nd,
                   g=g, VOT=VOT, omega=omega, t01=t01, t02=t02, kap1=kap1, kap2=kap2, o1=o1, o2=o2,
                   bpr_a=a, bpr_b=b, CP=CP, CA0=CA0, cA=cA, nuS=nuS, theta=theta, tolls=tuple(tolls))


@st.cache_resource(show_spinner=False, max_entries=8)
def get_model(p: tm.Params):
    return tm.Model(p)


@st.cache_data(show_spinner=False, max_entries=32)
def optimize(p: tm.Params, scen: tuple, eps_list: tuple, backend: str):
    m = get_model(p)
    rows, plans = [], {}
    for sc in scen:
        tc = tm.solve(m, sc, 1.0, backend)
        for e in eps_list:
            pl = tc if e >= 1.0 else tm.solve(m, sc, e, backend)
            s = tm.summarize(m, sc, e, pl, tc)
            rows.append(s)
            if pl is not None:
                plans[(sc, e)] = pl
    return rows, plans


# ------------------------------------------------------------------------- header
st.title("🌉 Bridge Toll Optimization: Simplified Model")
st.caption("Two routes (tolled bridge + free route), steady demand growth, one toll per administration term, "
           "three scenarios, present bias by term. Traffic equilibrium and economics in Python; "
           "the toll path is chosen by a mixed-integer program.")

if not scen:
    st.info("Pick at least one scenario in the sidebar.")
    st.stop()

with st.spinner(f"Solving with {LABEL[backend]} …"):
    try:
        rows, plans = optimize(params, tuple(scen), tuple(eps_list), backend)
    except Exception as ex:                                           # solver/licence problems
        st.error(f"{LABEL[backend]} failed: {ex}. Pick another solver in the sidebar.")
        st.stop()
m = get_model(params)

tab1, tab2, tab3, tab4 = st.tabs(["Optimal toll paths", "Year-by-year detail", "One-year explorer", "Model"])

# ------------------------------------------------------------------------- tab 1: summary
with tab1:
    summ = pd.DataFrame([{k: v for k, v in r.items() if k != "plan"} for r in rows])
    show = summ.copy()
    show["start_year"] = show["start_year"].map(lambda v: "never" if pd.isna(v) else int(v))
    for c in ("value_eps1", "toll_account_pv", "cost_of_present_bias"):
        show[c] = show[c].map(lambda v: "" if pd.isna(v) else f"${v/1e6:,.1f}M")
    show = show.rename(columns={"scenario": "Scenario", "eps": "ε", "toll_path": "Toll per term",
                                "start_year": "Start year", "value_eps1": "Value with ε = 1",
                                "toll_account_pv": "Toll account (PV)",
                                "cost_of_present_bias": "Cost of present bias"})
    c1, c2, c3 = st.columns(3)
    c1.metric("Solver", LABEL[backend].split(" (")[0])
    c2.metric("Terms × toll levels", f"{params.K} × {len(params.tolls)}")
    c3.metric("Cases solved", len(rows))
    st.subheader("Results")
    st.dataframe(show, hide_index=True, width="stretch")
    st.caption("Value with ε = 1: present value of the scenario's payoff against no toll, seen from year 0 "
               "without bias (Scenario 3: reduction in total cost). Cost of present bias: value of the unbiased "
               "plan minus value of the plan the biased administrations actually carry out.")
    st.download_button("⬇ Download results (CSV)", csv_bytes(summ), "toll_results_summary.csv", "text/csv")

    if plans:
        tp = []
        for (sc, e), pl in plans.items():
            for y in range(params.H):
                tp.append(dict(year=y, toll=pl[y // params.eta], case=f"{tm.SCENARIOS[sc]}, ε={e:g}"))
        tp = pd.DataFrame(tp)
        fig = go.Figure()
        dashes = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"]
        eps_idx = {e: i for i, e in enumerate(sorted({e for _, e in plans}, reverse=True))}
        for i, ((sc, e), pl) in enumerate(plans.items()):
            case = f"{tm.SCENARIOS[sc]}, ε={e:g}"
            grp = tp[tp.case == case]
            fig.add_trace(go.Scatter(x=grp.year, y=grp.toll + 0.04 * (i - len(plans) / 2), name=case,
                                     mode="lines", line=dict(shape="hv", width=3, color=COLORS[(sc - 1) % len(COLORS)],
                                                             dash=dashes[eps_idx[e] % len(dashes)]),
                                     customdata=grp.toll, hovertemplate="year %{x}: $%{customdata}<extra>" + case + "</extra>"))
        for k in range(1, params.K):
            fig.add_vline(x=k * params.eta - 0.5, line_dash="dot", line_color="#555555")
        fig.update_yaxes(title="Toll ($)")
        fig.update_xaxes(title="Year (dotted lines: new administration)")
        st.plotly_chart(styled(fig, "Toll path (color = scenario, line style = ε; lines slightly offset so overlaps stay visible)", 500),
                        width="stretch")

        vals = summ.dropna(subset=["value_eps1"]).copy()
        vals["case"] = vals["scenario"]
        vals["ε"] = vals["eps"].map(lambda e: f"ε={e:g}")
        vals["value ($M)"] = vals["value_eps1"] / 1e6
        fig = px.bar(vals, x="case", y="value ($M)", color="ε", barmode="group",
                     color_discrete_sequence=COLORS)
        fig.update_xaxes(title="")
        st.plotly_chart(styled(fig, "Value of the chosen plan, judged without bias (ε = 1)"), width="stretch")

# ------------------------------------------------------------------------- tab 2: detail
with tab2:
    if not plans:
        st.warning("No feasible plan.")
    else:
        keys = list(plans)
        sel = st.selectbox("Case", keys, format_func=lambda k: f"{tm.SCENARIOS[k[0]]}, ε={k[1]:g}  →  "
                           + ", ".join('none' if t == 0 else f'${t:g}' for t in plans[k]))
        df = m.table(plans[sel], sel[0])
        M = 1e6
        c = st.columns(4)
        c[0].metric("Start year", next((int(v) for v in df.year[df.start_year]), "never"))
        c[1].metric("Total revenue", f"${df.revenue.sum()/M:,.1f}M")
        c[2].metric("Total profit", f"${df.profit.sum()/M:,.1f}M")
        c[3].metric("Toll account (PV)", f"${m.finance(plans[sel])/M:,.1f}M")

        l, r_ = st.columns(2)
        f1 = go.Figure()
        f1.add_bar(x=df.year, y=df.revenue / M, name="Revenue", marker_color=COLORS[0])
        f1.add_bar(x=df.year, y=-(df.collection_cost + df.capital_cost) / M, name="Collection + capital cost",
                   marker_color=COLORS[3])
        f1.add_scatter(x=df.year, y=df.profit / M, name="Profit", mode="lines+markers", line_color=COLORS[2])
        f1.update_layout(barmode="relative"); f1.update_yaxes(title="$M per year")
        l.plotly_chart(styled(f1, "Money each year"), width="stretch")

        f2 = go.Figure()
        f2.add_scatter(x=df.year, y=df.bridge_crossings / M, name="Bridge crossings, with toll", line_color=COLORS[0])
        f2.add_scatter(x=df.year, y=df.bridge_crossings_no_toll / M, name="Bridge crossings, no toll",
                       line=dict(color=COLORS[1], dash="dash"))
        f2.update_yaxes(title="Million crossings per year")
        r_.plotly_chart(styled(f2, "Bridge traffic"), width="stretch")

        l, r_ = st.columns(2)
        f3 = go.Figure()
        f3.add_bar(x=df.year, y=df.peak_bridge_vph, name="Bridge", marker_color=COLORS[0])
        f3.add_bar(x=df.year, y=df.peak_free_route_vph, name="Free route", marker_color=COLORS[1])
        f3.add_scatter(x=df.year, y=df.peak_bridge_vph_no_toll + df.peak_free_route_vph_no_toll,
                       name="Total trips, no toll", mode="lines", line=dict(color="#FFFFFF", dash="dot"))
        f3.update_layout(barmode="stack"); f3.update_yaxes(title="Vehicles per peak hour")
        l.plotly_chart(styled(f3, "Peak-hour route split (equilibrium)"), width="stretch")

        f4 = go.Figure()
        f4.add_scatter(x=df.year, y=(df.TC - df.TC_no_toll) / M, name="Δ TC (trips made)", line_color=COLORS[0])
        f4.add_scatter(x=df.year, y=(df.X - df.X_no_toll) / M, name="Δ X (trips given up)", line_color=COLORS[1])
        f4.add_scatter(x=df.year, y=-df.services_S / M, name="− services S", line_color=COLORS[2])
        f4.add_scatter(x=df.year, y=df.consumer_surplus_change / M, name="Δ consumer surplus",
                       line=dict(color=COLORS[4], dash="dash"))
        f4.update_yaxes(title="$M per year, vs no toll")
        r_.plotly_chart(styled(f4, "Traveler costs vs no toll"), width="stretch")

        f5 = go.Figure()
        f5.add_bar(x=df.year, y=df.payoff_G / M, name="Yearly payoff G", marker_color=COLORS[0])
        disc = (1 / (1 + params.r)) ** df.year
        f5.add_scatter(x=df.year, y=(df.payoff_G * disc).cumsum() / M, name="Cumulative PV", line_color="#FFFFFF")
        f5.update_yaxes(title="$M")
        st.plotly_chart(styled(f5, f"Scenario payoff ({tm.SCENARIOS[sel[0]]}) vs no toll"), width="stretch")

        st.dataframe(df, hide_index=True, width="stretch")
        st.download_button("⬇ Download this case, year by year (CSV)", csv_bytes(df),
                           f"toll_detail_scenario{sel[0]}_eps{sel[1]:g}.csv", "text/csv")
        allrows = pd.concat([m.table(pl, sc).assign(scenario=tm.SCENARIOS[sc], eps=e)
                             for (sc, e), pl in plans.items()], ignore_index=True)
        st.download_button("⬇ Download all cases, year by year (CSV)", csv_bytes(allrows),
                           "toll_detail_all_cases.csv", "text/csv")

# ------------------------------------------------------------------------- tab 3: one year
with tab3:
    st.write("Equilibrium and money in a single year for any toll, to see the pieces behind the optimization.")
    c1, c2 = st.columns(2)
    yy = c1.slider("Year", 0, params.H - 1, 0)
    tau = c2.number_input("Toll ($)", 0.0, 100.0, 3.0, 0.25)
    o, b0 = m.year(yy, tau, start=False), m.base[yy]
    M = 1e6
    money = lambda v: ("−" if v < 0 else "") + f"${abs(v)/M:,.1f}M"
    c = st.columns(3)
    c[0].metric("Bridge crossings", f"{o['V']/M:,.2f}M", f"{(o['V']-b0['V'])/M:+,.2f}M vs no toll")
    c[1].metric("Toll revenue", money(o["R"]))
    c[2].metric("Collection cost", money(o["Cc"]))
    c = st.columns(3)
    c[0].metric("Δ consumer surplus", money(o["CS"] - b0["CS"]))
    c[1].metric("Travel cost of trips made (TC)", money(o["TC"]), f"no toll {money(b0['TC'])}", delta_color="off")
    c[2].metric("Cost of trips given up (X)", money(o["X"]), f"no toll {money(b0['X'])}", delta_color="off")

    sweep = []
    hi = max(max(params.tolls), 8.0)
    for i in range(0, 41):
        t = hi * i / 40
        oo = m.year(yy, t, start=False)
        sweep.append(dict(toll=t, revenue=oo["R"] / M, profit=oo["Pi"] / M,
                          welfare=(m.G(2, oo, yy)) / M, total_cost_saving=m.G(3, oo, yy) / M,
                          peak_bridge=oo["peak_bridge_vph"], peak_free=oo["peak_free_vph"]))
    sw = pd.DataFrame(sweep)
    l, r_ = st.columns(2)
    fa = go.Figure()
    for i, (col, nm) in enumerate([("profit", "S1 profit"), ("welfare", "S2 welfare gain"),
                                   ("total_cost_saving", "S3 total-cost saving"), ("revenue", "Revenue")]):
        fa.add_scatter(x=sw.toll, y=sw[col], name=nm, line_color=COLORS[i])
    fa.add_vline(x=tau, line_dash="dot", line_color="#888888")
    fa.update_xaxes(title="Toll ($)"); fa.update_yaxes(title="$M in this year (no capital cost)")
    l.plotly_chart(styled(fa, f"Year {yy}: payoffs vs toll"), width="stretch")
    fb = go.Figure()
    fb.add_scatter(x=sw.toll, y=sw.peak_bridge, name="Bridge", stackgroup="one", line_color=COLORS[0])
    fb.add_scatter(x=sw.toll, y=sw.peak_free, name="Free route", stackgroup="one", line_color=COLORS[1])
    fb.add_vline(x=tau, line_dash="dot", line_color="#888888")
    fb.update_xaxes(title="Toll ($)"); fb.update_yaxes(title="Vehicles per peak hour")
    r_.plotly_chart(styled(fb, f"Year {yy}: peak-hour route split vs toll"), width="stretch")
    st.download_button("⬇ Download toll sweep (CSV)", csv_bytes(sw), f"toll_sweep_year{yy}.csv", "text/csv")

# ------------------------------------------------------------------------- tab 4: model
with tab4:
    st.markdown(r"""
**Demand.** Potential trips grow steadily: $\bar Q_{y,h} = \bar Q_{0,h}(1+g)^y$ for peak and off-peak.

**Traffic split (user equilibrium).** Route cost $C_i = o_i + \text{VOT}\,t_{0i}\,[1+\alpha (v_i/\kappa_i)^\beta] + \tau\,[i=1]$.
Trips made $q = \bar Q\,e^{-\omega\mu}$; every used route costs $\mu$, an unused one at least $\mu$.
Solved in Python by bisection on $\mu$.

**One year.** Crossings $V$, revenue $R=\tau V$, collection $C^c = C^A_0 + c^A V$ while tolling,
profit $\Pi = R - C^c - C^P[\text{start year}]$, consumer surplus $CS = q/\omega$,
travel cost $TC = q\mu$, cost of trips given up $X = (\bar Q - q)/\omega - q\mu$,
services $S = \nu\max(0, R - C^c)$.

**Scenarios** (yearly payoff $G_y$ against no toll):
1. Operator: $G = \Pi$.
2. Welfare: $G = \Delta CS + \Pi$.
3. Total cost: $G = -[(TC + X - \theta S) - (TC^0 + X^0)]$, subject to $\sum_y d^y(\Pi_y - S_y) \ge 0$.

**Present bias.** Term $k$ weighs year $y$ by $d^{\,y-t_k}$ inside its term and $\varepsilon\,d^{\,y-t_k}$ afterwards.
$\varepsilon = 1$: one plan for all terms. $\varepsilon < 1$: each administration re-plans when it takes office and
carries out only its own term (naive).

**Optimization (MIP).** Binary $x_{kj}=1$ if term $k$ charges toll level $j$; $\sum_j x_{kj}=1$;
$s_k=\sum_{j:\tau_j>0}x_{kj}$ with $s_k \ge s_{k-1}$ (once started, tolling continues); $z_k = s_k - s_{k-1}$ marks the
start term, where the capital cost is paid. Objective: maximize $\sum_k\sum_j w_{kj}x_{kj} - C^P\sum_k \hat w_k z_k$
(capital term in Scenarios 1–2). Solved with Gurobi, Pyomo + HiGHS or CPLEX; enumeration is a check.
""")
    st.caption("Source: Bridge_Toll_Simplified_Model.docx; reference solver solver/simple_model.py.")
