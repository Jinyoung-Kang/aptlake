"""자체 가격지수 HEDONIC_TD_v1 (기획서 7장, FR-402).

모형: log(㎡당 가격)_i = α_단지(i) + Σ_t β_t·[월=t] + Σ_f γ_f·[층구간=f] + Σ_a δ_a·[면적구간=a] + ε_i
      지수_t = 100 · exp(β_t)  (기준월 β=0 → 100)

단지 고정효과는 그룹 내 평균 차감(within 변환, FWL 정리)으로 제거한다. 거래 수백만 건에서 평균 차감한
설계행렬을 조밀하게 만들면 메모리가 GB 단위가 되므로, 희소 원핫 X 와 그룹 지시행렬 G 로
    X̃'X̃ = X'X − (G'X)' diag(1/n_g) (G'X),   X̃'ỹ = X'y − (G'X)' diag(1/n_g) (G'y)
를 직접 계산한다 (메모리 O(nnz)). 표준오차는 단지 단위 군집 강건(cluster-robust) 분산으로 낸다.

층·면적 구간 경계는 Claude 제안값이며, 해제·면적 0·이상치 거래는 입력에서 제외된다(gold.trade_serving).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp

METHOD = "HEDONIC_TD_v1"
FLOOR_EDGES = (3, 10, 20)  # ≤3 | 4–10 | 11–20 | 21+
AREA_EDGES = (40.0, 60.0, 85.0, 135.0)  # <40 | 40–60 | 60–85 | 85–135 | ≥135 ㎡
MIN_OBS_PER_MONTH = 30
Z95 = 1.959963984540054


@dataclass(frozen=True)
class IndexPoint:
    period: str
    value: float
    ci_low: float
    ci_high: float
    n_obs: int


def _codes(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    uniq, inv = np.unique(values, return_inverse=True)
    return uniq, inv


def bins(values: np.ndarray, edges: tuple[float, ...], upper_inclusive: bool) -> np.ndarray:
    """upper_inclusive=True: 구간 상한 포함 (층 ≤3), False: 상한 미포함 (면적 <40)."""
    return np.searchsorted(np.asarray(edges, dtype=float), values, side="left" if upper_inclusive else "right")


def fit(
    complex_key: np.ndarray, period: np.ndarray, floor: np.ndarray, area: np.ndarray, ppm2: np.ndarray
) -> list[IndexPoint]:
    """입력은 같은 길이의 1차원 배열. period 는 'YYYYMM' 문자열."""
    ok = np.isfinite(ppm2) & (ppm2 > 0) & np.isfinite(area) & (area > 0) & np.isfinite(floor)
    complex_key, period, floor, area, ppm2 = complex_key[ok], period[ok], floor[ok], area[ok], ppm2[ok]

    # 표본이 너무 적은 월은 추정에서 제외 (지수 불안정)
    months, m_idx = _codes(period)
    counts = np.bincount(m_idx, minlength=len(months))
    keep = counts[m_idx] >= MIN_OBS_PER_MONTH
    complex_key, period, floor, area, ppm2 = complex_key[keep], period[keep], floor[keep], area[keep], ppm2[keep]

    # 단일 관측 단지는 within 변환 후 정보가 없으므로 제외
    groups, g_idx = _codes(complex_key)
    g_n = np.bincount(g_idx)
    multi = g_n[g_idx] > 1
    complex_key, period, floor, area, ppm2 = complex_key[multi], period[multi], floor[multi], area[multi], ppm2[multi]
    if len(ppm2) == 0:
        return []

    months, m_idx = _codes(period)
    groups, g_idx = _codes(complex_key)
    f_idx = bins(floor, FLOOR_EDGES, upper_inclusive=True)
    a_idx = bins(area, AREA_EDGES, upper_inclusive=False)
    n, n_g = len(ppm2), len(groups)
    y = np.log(ppm2)

    # 기준 범주 제거: 첫 월, 첫 층구간, 첫 면적구간
    n_m, n_f, n_a = len(months) - 1, len(FLOOR_EDGES), len(AREA_EDGES)
    k = n_m + n_f + n_a
    rows, cols = [], []
    for idx, offset in ((m_idx, 0), (f_idx, n_m), (a_idx, n_m + n_f)):
        sel = idx > 0
        rows.append(np.nonzero(sel)[0])
        cols.append(offset + idx[sel] - 1)
    r, c = np.concatenate(rows), np.concatenate(cols)
    X = sp.csr_matrix((np.ones(len(r)), (r, c)), shape=(n, k))
    G = sp.csr_matrix((np.ones(n), (np.arange(n), g_idx)), shape=(n, n_g))
    inv_n = sp.diags(1.0 / np.bincount(g_idx, minlength=n_g))

    GX = (G.T @ X).tocsr()
    Gy = G.T @ y
    XtX = (X.T @ X - GX.T @ inv_n @ GX).toarray()
    Xty = X.T @ y - GX.T @ (inv_n @ Gy)

    # 비식별 열(표본 없는 구간) 제거
    ident = np.diag(XtX) > 1e-9
    XtX_i = XtX[np.ix_(ident, ident)]
    beta_i = np.linalg.solve(XtX_i, Xty[ident])
    beta = np.zeros(k)
    beta[ident] = beta_i

    # 군집(단지) 강건 분산: 평균 차감 잔차 ẽ, 점수 S_g = X_g' ẽ_g
    resid = y - X @ beta
    e = resid - (G @ (inv_n @ (G.T @ resid)))
    S = (G.T @ sp.diags(e) @ X).tocsr()[:, np.nonzero(ident)[0]]
    meat = (S.T @ S).toarray()
    bread = np.linalg.inv(XtX_i)
    dof = n - n_g - int(ident.sum())
    adj = (n_g / (n_g - 1)) * ((n - 1) / max(dof, 1))
    cov = adj * bread @ meat @ bread
    se = np.zeros(k)
    se[ident] = np.sqrt(np.clip(np.diag(cov), 0, None))

    m_counts = np.bincount(m_idx, minlength=len(months))
    out = [IndexPoint(str(months[0]), 100.0, 100.0, 100.0, int(m_counts[0]))]
    for j in range(1, len(months)):
        b, s = beta[j - 1], se[j - 1]
        out.append(
            IndexPoint(
                str(months[j]),
                float(100 * np.exp(b)),
                float(100 * np.exp(b - Z95 * s)),
                float(100 * np.exp(b + Z95 * s)),
                int(m_counts[j]),
            )
        )
    return out


def mom(series: dict[str, float]) -> dict[str, float]:
    """월간 변화율 (연속한 두 달이 모두 있을 때만)."""
    keys = sorted(series)
    out = {}
    for a, b in zip(keys, keys[1:], strict=False):
        ya, ma, yb, mb = int(a[:4]), int(a[4:]), int(b[:4]), int(b[4:])
        if (yb * 12 + mb) - (ya * 12 + ma) == 1 and series[a] > 0:
            out[b] = series[b] / series[a] - 1
    return out


def validate(ours: dict[str, float], reference: dict[str, float]) -> dict[str, object]:
    """R-ONE 대비: 월간 변화율 상관계수와 방향 일치율."""
    a, b = mom(ours), mom(reference)
    common = sorted(set(a) & set(b))
    if len(common) < 6:
        return {
            "corr_mom": None,
            "direction_match": None,
            "n_months": len(common),
            "window": [common[0], common[-1]] if common else None,
        }
    x, z = np.array([a[p] for p in common]), np.array([b[p] for p in common])
    corr = float(np.corrcoef(x, z)[0, 1]) if x.std() > 0 and z.std() > 0 else None
    direction = float(np.mean(np.sign(x) == np.sign(z)))
    return {"corr_mom": corr, "direction_match": direction, "n_months": len(common), "window": [common[0], common[-1]]}
