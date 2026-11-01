from s199_v2.feed_health import FeedHealth, bounded_backoff


def healthy_snapshot(feed: FeedHealth, instrument: str, sequence: int = 10) -> None:
    feed.connect(instrument)
    feed.snapshot(instrument, sequence=sequence, ts_ns=100, bid=.4, ask=.6, bid_size=2, ask_size=3, depth=5)


def test_initial_snapshot_required_and_both_outcomes_required() -> None:
    feed=FeedHealth(max_age_ns=20); feed.connect("UP")
    assert feed.tradeable(("UP","DOWN"),now_ns=105)[0] is False
    healthy_snapshot(feed,"UP"); healthy_snapshot(feed,"DOWN")
    assert feed.tradeable(("UP","DOWN"),now_ns=105) == (True,None)


def test_disconnect_fails_closed_until_resnapshot() -> None:
    feed=FeedHealth(max_age_ns=20); healthy_snapshot(feed,"UP")
    feed.disconnect("UP"); feed.connect("UP")
    feed.delta("UP",sequence=11,ts_ns=101,bid=.4,ask=.6,bid_size=2,ask_size=3,depth=5)
    assert feed.tradeable(("UP",),now_ns=102)[0] is False
    feed.snapshot("UP",sequence=20,ts_ns=102,bid=.4,ask=.6,bid_size=2,ask_size=3,depth=5)
    assert feed.tradeable(("UP",),now_ns=103) == (True,None)


def test_sequence_gap_requires_resnapshot() -> None:
    feed=FeedHealth(max_age_ns=20); healthy_snapshot(feed,"UP")
    feed.delta("UP",sequence=12,ts_ns=101,bid=.4,ask=.6,bid_size=2,ask_size=3,depth=5)
    assert feed.books["UP"].fault == "sequence_gap"
    assert feed.tradeable(("UP",),now_ns=102)[0] is False


def test_stale_crossed_and_empty_books_fail_closed() -> None:
    feed=FeedHealth(max_age_ns=20); healthy_snapshot(feed,"UP")
    assert feed.tradeable(("UP",),now_ns=121)[1] == "UP:stale"
    feed.snapshot("UP",sequence=20,ts_ns=200,bid=.7,ask=.6,bid_size=2,ask_size=3,depth=5)
    assert feed.tradeable(("UP",),now_ns=200)[1] == "UP:crossed_book"


def test_reconnect_backoff_is_bounded() -> None:
    assert [bounded_backoff(i) for i in range(4)] == [.5,1,2,4]
    assert bounded_backoff(99) == 30
