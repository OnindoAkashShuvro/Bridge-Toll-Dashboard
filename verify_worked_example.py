"""Check that every solver reproduces the worked example of Bridge_Toll_Simplified_Model.docx (Section 9).
Run:  python verify_worked_example.py"""
import toll_model as tm

EXPECTED = {  # (scenario, eps): (toll per term, start year, value with eps=1 in $M)
    (1, 1.0): ((3, 3, 3, 3), 0, 281.9), (1, 0.5): ((3, 3, 3, 3), 0, 281.9), (1, 0.2): ((3, 3, 3, 3), 0, 281.9),
    (2, 1.0): ((3, 3, 3, 3), 0, 41.7), (2, 0.5): ((0, 3, 3, 3), 4, 40.9), (2, 0.2): ((0, 3, 3, 3), 4, 40.9),
    (3, 1.0): ((0, 3, 3, 3), 4, 27.7), (3, 0.5): ((0, 3, 3, 3), 4, 27.7), (3, 0.2): ((0, 3, 3, 3), 4, 27.7),
}

m = tm.Model(tm.Params())
o, b = m.year(0, 3.0, start=False), m.base[0]
print(f"Year 0, $3 toll: crossings {o['V']/1e6:.2f}M (no toll {b['V']/1e6:.2f}M), revenue ${o['R']/1e6:.1f}M, "
      f"collection ${o['Cc']/1e6:.1f}M, CS change ${(o['CS']-b['CS'])/1e6:.1f}M, "
      f"TC ${o['TC']/1e6:.1f}M (no toll {b['TC']/1e6:.1f}M), X ${o['X']/1e6:.1f}M (no toll {b['X']/1e6:.1f}M)")
ok_all = True
for be in tm.available_backends():
    ok = True
    for sc in (1, 2, 3):
        tc = tm.solve(m, sc, 1.0, be)
        for eps in (1.0, 0.5, 0.2):
            pl = tc if eps == 1.0 else tm.solve(m, sc, eps, be)
            s = tm.summarize(m, sc, eps, pl, tc)
            exp = EXPECTED[sc, eps]
            good = tuple(int(t) for t in pl) == exp[0] and s["start_year"] == exp[1] and round(s["value_eps1"] / 1e6, 1) == exp[2]
            ok &= good
    print(f"{be:12s}: {'all 9 cases match' if ok else 'MISMATCH'}")
    ok_all &= ok
raise SystemExit(0 if ok_all else 1)
