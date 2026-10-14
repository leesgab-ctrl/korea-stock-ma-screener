"""Completed-bar recovery colors and the first MA10/MA40 cross per pullback."""
import math


def recovery_states(bars):
    result = []
    previous = None
    phase = "before"
    armed = False
    finish_pending = False
    crossed40 = False
    cycle = None
    failed = False
    for index, row in enumerate(bars):
        state = {"phase": phase, "recoveryActive": phase == "fast",
                 "recoveryReached60": False, "recoveryMa10Breakout": False,
                 "recoveryCycle": cycle, "recoveryFailed": failed, "recoveryStrengthened": crossed40}
        if row.get("complete") is False:
            result.append(state)
            continue
        valid = all(isinstance(row.get(key), (int, float)) and math.isfinite(row[key]) and row[key] > 0
                    for key in ("m3", "m10", "m20", "m40", "m60"))
        if not valid:
            previous, phase, armed, finish_pending, crossed40, cycle = None, "before", False, False, False, None
            failed = False
        else:
            if finish_pending:
                phase, finish_pending = "confirmed", False
            if previous:
                if previous["m20"] >= previous["m40"] and row["m20"] < row["m40"]:
                    phase, armed, finish_pending, crossed40 = "pullback", True, False, False
                    cycle = row.get("t", str(index))
                    failed = False
                elif armed and phase == "confirmed" and row["m20"] < row["m40"] and row["m3"] <= row["m20"]:
                    # A second pullback can begin without a new MA20/MA40 cross.
                    phase, finish_pending, crossed40 = "pullback", False, False
                    cycle = row.get("t", str(index))
                    failed = row["m3"] <= row["m10"]
                if armed:
                    if phase == "pullback" and previous["m3"] <= previous["m20"] and row["m3"] > row["m20"]:
                        phase = "fast"
                        failed = False
                    if phase in ("fast", "confirmed") and row["m3"] <= row["m10"]:
                        phase, finish_pending, crossed40, failed = "pullback", False, False, True
                        cycle = row.get("t", str(index))
                    # Include the completed MA60 breakout candle in the dark interval.
                    if phase == "fast" and row["m3"] > row["m60"]:
                        state["recoveryReached60"] = True
                        finish_pending = True
                    if not crossed40 and previous["m10"] <= previous["m40"] and row["m10"] > row["m40"]:
                        state["recoveryMa10Breakout"] = True
                        crossed40 = True
            previous = row
        state.update(phase=phase, recoveryActive=phase == "fast", recoveryCycle=cycle,
                     recoveryFailed=failed, recoveryStrengthened=crossed40)
        result.append(state)
    return result
