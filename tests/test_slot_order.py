"""Slots sort by weekday, then by time (the schedule and the certificates read in week order)."""

from enrollment.domain.model.slot import Slot


def test_slots_sort_by_weekday_not_alphabetically():
    week = [
        Slot.parse("THU", "08:00", "10:00"),
        Slot.parse("TUE", "08:00", "10:00"),
        Slot.parse("MON", "10:00", "12:00"),
        Slot.parse("MON", "08:00", "10:00"),
    ]
    assert [(s.day.value, s.start.strftime("%H:%M")) for s in sorted(week)] == [
        ("MON", "08:00"),
        ("MON", "10:00"),
        ("TUE", "08:00"),
        ("THU", "08:00"),
    ]
