"""Durations must survive timestamps that are not ISO strings."""

from datetime import UTC, datetime

from osop.recorder.transcript import _ms_between


def _epoch_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def test_two_epoch_millisecond_stamps_give_their_difference():
    start = _epoch_ms("2031-03-04T05:06:07+00:00")
    assert _ms_between(start, start + 4250) == 4250
    assert _ms_between(float(start), float(start + 750)) == 750


def test_epoch_millisecond_start_with_iso_end():
    start = _epoch_ms("2031-03-04T05:06:07+00:00")
    assert _ms_between(start, "2031-03-04T05:06:09Z") == 2000


def test_unparseable_stamp_gives_zero_instead_of_raising():
    assert _ms_between(object(), "2031-03-04T05:06:09Z") == 0
    assert _ms_between({"at": 1}, ["later"]) == 0
    assert _ms_between(datetime(2031, 3, 4, tzinfo=UTC), "not a time") == 0
