"""자체 지수 산출·검증·발행 (FR-402). 입력 스냅샷 ID 를 결과에 기록해 재현 가능하게 한다 (NFR-02)."""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from . import hedonic, ops_db, publish
from .lake import catalog, eq, isin
from .rone import SOURCE as REF_SOURCE

MODEL_VER = "v1"
MIN_COVERAGE = 0.9  # 수집 대상 시군구 중 silver 반영(MERGED)된 비율이 이 값 미만인 계약월은 지수에서 제외


def complete_months() -> tuple[list[str], dict[str, float]]:
    """지역 구성이 불완전한 달(백필 중)을 넣으면 지역 간 추세 차이가 월 효과로 섞인다 → 완결된 달만 쓴다."""
    with ops_db.conn() as c:
        rows = c.execute(
            """SELECT p.deal_ym, count(*) FILTER (WHERE p.status = 'MERGED')::float
                      / (SELECT count(*) FROM ops.region WHERE is_leaf AND active) AS coverage
               FROM ops.ingest_partition p GROUP BY p.deal_ym"""
        ).fetchall()
    cov = {r["deal_ym"]: float(r["coverage"]) for r in rows}
    return sorted(m for m, v in cov.items() if v >= MIN_COVERAGE), cov


def build_index(log: logging.Logger | None = None) -> dict[str, object]:
    cat = catalog()
    ts = cat.load_table("gold.trade_serving")
    snap = ts.current_snapshot()
    months, coverage = complete_months()
    if not months:
        return {"points": 0, "note": "no month with coverage >= 90% yet"}
    arrow = ts.scan(
        row_filter=eq("is_cancelled", False) & eq("is_outlier", False) & isin("deal_ym", months),
        selected_fields=("sgg_cd", "complex_key", "deal_ym", "floor", "area_m2", "ppm2"),
    ).to_arrow()

    # 문자열 열은 파이썬 객체 배열로 만들지 않고 사전 인코딩 정수 코드로 쓴다 (전국 수백만 행에서 메모리 수백 MB 절약)
    def codes(values: pa.ChunkedArray) -> tuple[np.ndarray, list[str]]:
        enc = pc.dictionary_encode(values).combine_chunks()
        return enc.indices.to_numpy(zero_copy_only=False).astype(np.int32), enc.dictionary.to_pylist()

    sido_idx, sido_names = codes(pc.utf8_slice_codeunits(arrow["sgg_cd"], 0, 2))
    period_idx, period_names = codes(arrow["deal_ym"])
    complex_idx, _ = codes(arrow["complex_key"])
    cols = {
        "complex": complex_idx,
        "period": np.asarray(period_names)[period_idx],  # 고정폭 '<U6' 배열 (행당 24바이트)
        "floor": pc.fill_null(pc.cast(arrow["floor"], pa.float64()), np.nan).to_numpy(zero_copy_only=False),
        "area": pc.cast(arrow["area_m2"], pa.float64()).to_numpy(zero_copy_only=False),
        "ppm2": pc.fill_null(pc.cast(arrow["ppm2"], pa.float64()), np.nan).to_numpy(zero_copy_only=False),
    }
    del arrow
    ref_rows = cat.load_table("gold.index_reference").scan().to_arrow().to_pylist()
    reference: dict[str, dict[str, float]] = {}
    for r in ref_rows:
        reference.setdefault(r["region_id"], {})[r["period"]] = r["value"]

    now = dt.datetime.now(tz=dt.UTC)
    points, validation = [], []
    for region in ["00", *sorted(sido_names)]:
        mask = np.ones(len(sido_idx), bool) if region == "00" else sido_idx == sido_names.index(region)
        pts = hedonic.fit(
            cols["complex"][mask], cols["period"][mask], cols["floor"][mask], cols["area"][mask], cols["ppm2"][mask]
        )
        if not pts:
            continue
        for p in pts:
            points.append(
                {
                    "region_id": region,
                    "method": hedonic.METHOD,
                    "period": p.period,
                    "index_value": p.value,
                    "ci_low": p.ci_low,
                    "ci_high": p.ci_high,
                    "n_obs": p.n_obs,
                    "model_ver": MODEL_VER,
                    "input_snapshot": snap.snapshot_id if snap else None,
                    "computed_at": now,
                }
            )
        if region in reference:
            v = hedonic.validate({p.period: p.value for p in pts}, reference[region])
            validation.append({"region_id": region, "method": hedonic.METHOD, "reference": REF_SOURCE, **v})
        if log:
            log.info(f"index {region}: {len(pts)} months")

    table = cat.load_table("gold.price_index")
    table.overwrite(
        pa.Table.from_pylist(
            points,
            schema=pa.schema(
                [
                    ("region_id", pa.string()),
                    ("method", pa.string()),
                    ("period", pa.string()),
                    ("index_value", pa.float64()),
                    ("ci_low", pa.float64()),
                    ("ci_high", pa.float64()),
                    ("n_obs", pa.int32()),
                    ("model_ver", pa.string()),
                    ("input_snapshot", pa.int64()),
                    ("computed_at", pa.timestamp("us", tz="UTC")),
                ]
            ),
        ),
        overwrite_filter=eq("method", hedonic.METHOD),
    )
    counts = publish.publish_index(
        points,
        [
            {"region_id": r["region_id"], "period": r["period"], "value": r["value"], "source": r["source"]}
            for r in ref_rows
        ],
        validation,
    )
    excluded = sorted(m for m, v in coverage.items() if 0 < v < MIN_COVERAGE)
    return {
        "months_used": len(months),
        "months_excluded_incomplete": ",".join(excluded[:24]),
        "regions": len({p["region_id"] for p in points}),
        "points": len(points),
        "input_snapshot": str(snap.snapshot_id if snap else None),
        **counts,
        "validation": str([(v["region_id"], v["corr_mom"]) for v in validation][:5]),
    }
