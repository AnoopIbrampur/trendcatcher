import math

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from trendcatcher.web.server import app, clean, norm_location


def test_clean_makes_json_safe():
    out = clean({"a": np.float64("nan"), "b": np.int64(3), "c": [np.float32(1.5), math.inf], "d": np.bool_(True)})
    assert out == {"a": None, "b": 3, "c": [1.5, None], "d": True}


def test_norm_location():
    assert norm_location(None) is None and norm_location("global") is None
    assert norm_location("US-NY") == "US-NY" and norm_location("IN") == "IN"
    with pytest.raises(HTTPException):
        norm_location("US-XX")
    with pytest.raises(HTTPException):
        norm_location("../etc")


def test_locations_and_index():
    c = TestClient(app)
    groups = c.get("/api/locations").json()["groups"]
    assert [g["name"] for g in groups] == ["Worldwide", "Countries", "US states"]
    assert len(groups[2]["items"]) == 51
    r = c.get("/")
    assert r.status_code == 200 and "Trend Catcher" in r.text
