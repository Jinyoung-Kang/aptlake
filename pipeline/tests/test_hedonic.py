import numpy as np

from aptlake_pipeline.hedonic import AREA_EDGES, FLOOR_EDGES, MIN_OBS_PER_MONTH, Z95, bins, fit, mom, validate


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


def _dense_reference(c, p, fl, ar, pr):
    """같은 추정량을 정의 그대로: 거른 뒤 밀집 원핫 X·y 를 단지 평균 차감해 OLS, 단지 군집 강건 분산."""
    ok = np.isfinite(pr) & (pr > 0) & np.isfinite(ar) & (ar > 0) & np.isfinite(fl)
    c, p, fl, ar, pr = c[ok], p[ok], fl[ok], ar[ok], pr[ok]
    months, cnt = np.unique(p, return_counts=True)
    keep = np.isin(p, months[cnt >= MIN_OBS_PER_MONTH])
    c, p, fl, ar, pr = c[keep], p[keep], fl[keep], ar[keep], pr[keep]
    groups, cnt = np.unique(c, return_counts=True)
    keep = np.isin(c, groups[cnt > 1])
    c, p, fl, ar, pr = c[keep], p[keep], fl[keep], ar[keep], pr[keep]
    months = np.unique(p)
    fb, ab = bins(fl, FLOOR_EDGES, upper_inclusive=True), bins(ar, AREA_EDGES, upper_inclusive=False)
    X = np.column_stack(
        [p == m for m in months[1:]]
        + [fb == f for f in range(1, len(FLOOR_EDGES) + 1)]
        + [ab == a for a in range(1, len(AREA_EDGES) + 1)]
    ).astype(float)
    y = np.log(pr)
    _, g = np.unique(c, return_inverse=True)
    n_g = g.max() + 1
    members = [g == j for j in range(n_g)]
    Xw = X - np.vstack([X[m].mean(0) for m in members])[g]
    yw = y - np.array([y[m].mean() for m in members])[g]
    ident = (Xw**2).sum(0) > 1e-9
    Xi = Xw[:, ident]
    bread = np.linalg.inv(Xi.T @ Xi)
    beta = bread @ Xi.T @ yw
    e = yw - Xi @ beta
    meat = sum(np.outer(Xi[m].T @ e[m], Xi[m].T @ e[m]) for m in members)
    adj = (n_g / (n_g - 1)) * ((len(y) - 1) / (len(y) - n_g - ident.sum()))
    b, s = np.zeros(X.shape[1]), np.zeros(X.shape[1])
    b[ident], s[ident] = beta, np.sqrt(np.diag(adj * bread @ meat @ bread))
    j = np.arange(len(months) - 1)
    return months, 100 * np.exp(b[j]), 100 * np.exp(b[j] - Z95 * s[j]), 100 * np.exp(b[j] + Z95 * s[j])


def test_matches_dense_definition():
    """합계(bincount)로 푼 정규방정식·군집 점수가 정의대로 만든 밀집 계산과 같다 — 거르기(결측·표본 적은 달·
    단일 관측 단지)와 비식별 열(빈 층·면적 구간)까지 포함."""
    rng = np.random.default_rng(11)
    n = 3000
    c = np.array([f"c{v}" for v in rng.integers(0, 250, n)] + [f"solo{i}" for i in range(15)])
    p = np.array([f"2024{m:02d}" for m in rng.integers(1, 13, n)] + ["202501"] * 15)  # 202501 은 15건뿐
    fl = rng.integers(1, 11, n + 15).astype(float)  # 층 구간 11–20·21+ 없음
    ar = rng.uniform(30, 85, n + 15)  # 면적 구간 85–135·≥135 없음
    pr = np.exp(7 + 0.03 * (fl > 3) - 0.04 * (ar >= 60) + rng.normal(0, 0.1, n + 15))
    pr[::97], fl[::89] = np.nan, np.nan
    months, value, lo, hi = _dense_reference(c, p, fl, ar, pr)
    pts = fit(c, p, fl, ar, pr)
    assert [x.period for x in pts] == list(months) and "202501" not in months
    np.testing.assert_allclose([x.value for x in pts[1:]], value, rtol=1e-9)
    np.testing.assert_allclose([x.ci_low for x in pts[1:]], lo, rtol=1e-9)
    np.testing.assert_allclose([x.ci_high for x in pts[1:]], hi, rtol=1e-9)


def test_validation_metrics():
    ours = {"202101": 100, "202102": 101, "202103": 100.5, "202104": 102, "202105": 103, "202106": 102.5, "202107": 104}
    ref = {k: v * 1.1 for k, v in ours.items()}
    v = validate(ours, ref)
    assert v["n_months"] == 6 and abs(v["corr_mom"] - 1) < 1e-9 and v["direction_match"] == 1.0
    assert mom({"202101": 100, "202103": 110}) == {}
