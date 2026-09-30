"""Tests for the SEMS+ active alarms sensor."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sems.const import CONF_STATION_ID, DOMAIN
from custom_components.sems.sems_api import OutOfRetries, SemsApi

from .test_sensor_entities import MOCK_GET_DATA_RESULT_MINIMAL, _mock_no_battery_api

STATION_ID = "12345678-1234-5678-9abc-123456789abc"


def _api() -> SemsApi:
    return SemsApi(Mock(), "user", "pass")


@pytest.mark.parametrize(
    ("response", "count"),
    [
        (3, 3),
        ("2", 2),
        ({"count": 4, "level1": 9}, 4),
        ({"total": "1"}, 1),
        ({"error": 1, "warning": 2, "name": "x"}, 3),
        ({"count": None, "warning": 2}, 2),
        ({"name": "x"}, None),
        (True, None),
        (None, None),
        ([], None),
    ],
)
def test_alarm_count_parsing(response, count):
    """Test the count is read from a number or an object of counts."""
    assert SemsApi._alarm_count(response) == count


def test_get_web_alarm_count_uses_web_alarm_endpoint():
    """Test the alarm count request and result."""
    with patch.object(
        SemsApi, "_make_api_call", return_value={"error": 1, "warning": 1}
    ) as api_call:
        assert _api().getWebAlarmCount() == {
            "count": 2,
            "details": {"error": 1, "warning": 1},
        }

    assert api_call.call_args.args == ("/sems-alarm/api/alarm/count",)
    assert api_call.call_args.kwargs["method"] == "GET"
    assert api_call.call_args.kwargs["is_web"] is True


def test_get_web_alarm_count_plain_number_has_no_details():
    """Test a bare number response gives empty details."""
    with patch.object(SemsApi, "_make_api_call", return_value=5):
        assert _api().getWebAlarmCount() == {"count": 5, "details": {}}


@pytest.mark.parametrize(
    "api_call",
    [
        {"side_effect": OutOfRetries("denied")},
        {"return_value": None},
        {"return_value": {"unknown": "shape"}},
    ],
)
def test_get_web_alarm_count_unavailable(api_call):
    """Test failed or unrecognised responses report no alarm data."""
    with patch.object(SemsApi, "_make_api_call", **api_call):
        assert _api().getWebAlarmCount() is None


async def _setup(hass: HomeAssistant, **alarm_patch) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        data={CONF_USERNAME: "u", CONF_PASSWORD: "p", CONF_STATION_ID: STATION_ID},
    )
    entry.add_to_hass(hass)
    with (
        _mock_no_battery_api(MOCK_GET_DATA_RESULT_MINIMAL),
        patch.object(SemsApi, "getWebAlarmCount", **alarm_patch),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _alarm_entity_id(hass: HomeAssistant) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        Platform.SENSOR, DOMAIN, f"{STATION_ID}-active-alarms"
    )


async def test_alarm_sensor(hass: HomeAssistant) -> None:
    """Test the sensor shows the count and the per-level counts."""
    await _setup(hass, return_value={"count": 2, "details": {"error": 1, "warning": 1}})

    entity_id = _alarm_entity_id(hass)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state.state == "2"
    assert state.attributes["error"] == 1
    assert state.attributes["warning"] == 1
    assert state.attributes["friendly_name"] == "SEMS Station Active Alarms"


async def test_alarm_sensor_unavailable_after_failed_refresh(
    hass: HomeAssistant,
) -> None:
    """Test a later alarm failure marks the sensor unavailable, not zero."""
    entry = await _setup(hass, return_value={"count": 0, "details": {}})
    entity_id = _alarm_entity_id(hass)
    assert hass.states.get(entity_id).state == "0"

    coordinator = entry.runtime_data.coordinator
    with (
        _mock_no_battery_api(MOCK_GET_DATA_RESULT_MINIMAL),
        patch.object(SemsApi, "getWebAlarmCount", side_effect=RuntimeError("boom")),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert hass.states.get(entity_id).state == "unavailable"


async def test_no_alarm_sensor_without_alarm_data(hass: HomeAssistant) -> None:
    """Test accounts without alarm data get no alarm sensor."""
    await _setup(hass, return_value=None)

    assert _alarm_entity_id(hass) is None
