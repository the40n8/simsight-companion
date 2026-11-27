"""Return types checked by hand in the game's bytecode, for the SimSight Plus
modules (same format as tools/ea_overlay.py)."""

RETURNS = {
    # int(self.absolute_seconds() / SECONDS_PER_... % ...): whole numbers of the clock.
    'date_and_time.DateAndTime.hour': 'int',
    'date_and_time.DateAndTime.minute': 'int',
    'date_and_time.DateAndTime.day': 'int',  # day of the week, 0 to 6
    'date_and_time.DateAndTime.absolute_ticks': 'int',  # int(self)
    # self.in_seconds() / SECONDS_PER_MINUTE; in_seconds() is self._delta / ...
    'date_and_time.TimeSpan.in_minutes': 'float',
    'date_and_time.TimeSpan.in_seconds': 'float',
    'sims.household.Household.household_size': 'int',  # len(self._sim_infos)
}
