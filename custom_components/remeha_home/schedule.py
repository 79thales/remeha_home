"""Týdenní program pro jednu zónu; hodnoty teplot jsou ve °C."""

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def week(raw, zone, activities, dhw):
    """API čísluje neděli nulou; panel řadí od pondělí a uvnitř dne podle času."""
    if not isinstance(raw, dict) or not isinstance(raw.get("switchPoints"), list):
        return None
    names = {
        a["activityNumber"]: a
        for a in activities or []
        if isinstance(a, dict) and a.get("type") == "Heating" and "activityNumber" in a
    }
    result = {day: [] for day in DAYS}
    for point in raw["switchPoints"]:
        if not isinstance(point, dict):
            continue
        day, time = point.get("day"), point.get("time")
        if type(day) is not int or not 0 <= day < 7 or not isinstance(time, str):
            continue
        if dhw:
            comfort = point.get("comfortActive")
            if type(comfort) is not bool:
                continue
            mode = "Comfort" if comfort else "Eco"
            temp = (
                zone.get("comfortSetPoint")
                if comfort
                else zone.get("reducedSetpoint")
                if zone.get("dhwType") != "Combi"
                else None
            )
        else:
            activity = names.get(point.get("activity"), {})
            mode = activity.get("name")
            temp = point.get("customValue")
            if temp is None:
                temp = activity.get("temperature")
        result[DAYS[(day - 1) % 7]].append(dict(time=time, mode=mode, setpoint=temp))
    for points in result.values():
        points.sort(key=lambda point: point["time"])
    return result
