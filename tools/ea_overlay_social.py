"""Return types checked by hand in the game's bytecode (1.129 game), for the
companion bridge, phone, travel and hub modules. Same format as
tools/ea_overlay.py; docs/dev/EA-API.md records what was found."""

RETURNS = {
    # TimeSpan(int.__sub__(self, other)): a date minus a date is a span.
    'date_and_time.DateAndTime.__sub__': 'TimeSpan',
    # self.in_seconds() / SECONDS_PER_MINUTE: true division.
    'date_and_time.TimeSpan.in_minutes': 'float',
    # (time until the shift, its start, its end); (None, None, None) without
    # a work schedule.
    'careers.career_base.CareerBase.get_next_work_time': 'tuple[Any, Any, Any]',
}
