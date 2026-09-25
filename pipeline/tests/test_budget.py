from aptlake_pipeline.budget import ceilings


def test_priority_ceilings_reserve_room_for_higher_priorities():
    c = ceilings(cap=8000, n_regions=256, recheck_pending=True)
    assert c.incremental == 8000
    assert c.recheck == 8000 - 256 * 3
    assert c.backfill == 8000 - 256 * 3 - 256 * 12
    assert c.backfill < c.recheck < c.incremental


def test_no_recheck_reserve_when_nothing_due():
    c = ceilings(cap=8000, n_regions=256, recheck_pending=False)
    assert c.backfill == c.recheck == 8000 - 768


def test_ceiling_never_negative():
    c = ceilings(cap=100, n_regions=256, recheck_pending=True)
    assert c.backfill == 0 and c.recheck == 0 and c.incremental == 100
