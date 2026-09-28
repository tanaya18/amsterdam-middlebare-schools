"""Checks that every school in schools.js is complete, located in the Amsterdam area, and sourced."""
import json
import re
from datetime import date
from pathlib import Path

REQUIRED_FIELDS = {"name", "address", "area", "lat", "lng", "levels", "groups", "website", "dates_url",
                   "image", "open_days", "highlights", "pros", "cons", "facts", "sources"}


def load_schools():
    """Read schools.js and return the list after 'window.SCHOOLS =' as Python dicts."""
    text = (Path(__file__).parent / "school-map" / "schools.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"window\.SCHOOLS\s*=\s*(\[.*\]);", text, re.S).group(1))


def test_there_are_schools():
    """The data file is not empty."""
    assert len(load_schools()) > 50


def test_every_school_is_complete_and_sourced():
    """Each school has all fields, a location inside the Amsterdam area, and at least one source link."""
    for school in load_schools():
        assert REQUIRED_FIELDS <= school.keys(), school.get("name")
        assert 52.27 < school["lat"] < 52.44 and 4.72 < school["lng"] < 5.08, school["name"]
        assert school["sources"], f"{school['name']} has no sources"


def test_no_two_pins_on_the_same_spot():
    """Schools sharing a building get slightly different pins so both can be clicked."""
    spots = [(s["lat"], s["lng"]) for s in load_schools()]
    assert len(spots) == len(set(spots))


def test_dates_are_valid_and_sorted():
    """Every open day has a type and a real YYYY-MM-DD date, listed in date order."""
    for school in load_schools():
        days = [date.fromisoformat(d["date"]) for d in school["open_days"]]
        assert all(d["type"] for d in school["open_days"]), school["name"]
        assert days == sorted(days), school["name"]
