import numpy as np

from aptlake_pipeline.hedonic import AREA_EDGES, FLOOR_EDGES, bins, fit, mom, validate


def test_bins_boundaries():
    assert bins(np.array([-1, 3, 4, 10, 11, 20, 21]), FLOOR_EDGES, upper_inclusive=True).tolist() == [
        0,
        0,
        1,
        1,
        2,
        2,
        3,
    ]
    assert bins(np.array([39.9, 40.0, 59.99, 60.0, 84.9, 85.0, 135.0]), AREA_EDGES, upper_inclusive=False).tolist() == [
        0,
        1,
        1,
        2,
        2,
        3,
        4,
    ]


def _synthetic(seed=7, n_complex=400, months=24, per_month=60):
    rng = np.random.default_rng(seed)
    true = np.cumsum(rng.normal(0.004, 0.01, months))
    true -= true[0]
    fe = rng.normal(7.0, 0.5, n_complex)
    rows = []
    for t in range(months):
        c = rng.integers(0, n_complex, per_month * 3)
        floor = rng.integers(1, 30, len(c))
        area = rng.uniform(30, 150, len(c))
        y = fe[c] + true[t] + 0.02 * (floor > 10) - 0.05 * (area > 85) + rng.normal(0, 0.05, len(c))
        rows.append((c, np.full(len(c), f"{2021 + t // 12}{t % 12 + 1:02d}"), floor, area, np.exp(y)))
    cat = [np.concatenate(x) for x in zip(*rows, strict=True)]
    return true, [np.array([f"c{v}" for v in cat[0]]), cat[1], cat[2].astype(float), cat[3], cat[4]]


def test_recovers_known_month_effects():
    true, args = _synthetic()
    pts = fit(*args)
    assert len(pts) == len(true) and pts[0].value == 100.0
    got = np.log(np.array([p.value for p in pts]) / 100)
    assert np.max(np.abs(got - true)) < 0.01
    covered = np.mean([(p.ci_low <= 100 * np.exp(t) <= p.ci_high) for p, t in zip(pts[1:], true[1:], strict=True)])
    assert covered >= 0.8


def test_complex_fixed_effect_removes_composition_bias():
    """비싼 단지 거래가 특정 월에 몰려도 지수가 튀지 않아야 한다 (단순 중위수와의 차이)."""
    rng = np.random.default_rng(1)
    c, p, fl, ar, pr = [], [], [], [], []
    for t, ym in enumerate(["202101", "202102", "202103"]):
        for i in range(200):
            expensive = i < (150 if t == 1 else 50)  # 2월에 비싼 단지 비중 급증
            cid = f"e{i % 20}" if expensive else f"c{i % 40}"
            base = 8.0 if expensive else 6.5
            c.append(cid)
            p.append(ym)
            fl.append(5.0)
            ar.append(84.0)
            pr.append(np.exp(base + rng.normal(0, 0.01)))
    pts = fit(np.array(c), np.array(p), np.array(fl), np.array(ar), np.array(pr))
    assert all(abs(x.value - 100) < 1.0 for x in pts)


def test_validation_metrics():
    ours = {"202101": 100, "202102": 101, "202103": 100.5, "202104": 102, "202105": 103, "202106": 102.5, "202107": 104}
    ref = {k: v * 1.1 for k, v in ours.items()}
    v = validate(ours, ref)
    assert v["n_months"] == 6 and abs(v["corr_mom"] - 1) < 1e-9 and v["direction_match"] == 1.0
    assert mom({"202101": 100, "202103": 110}) == {}
