from __future__ import annotations

import pytest

from shipcube_monitor import MonitorUnavailable
from shipcube_monitor.api import MonitorTelemetry, target_for

APP = {"name": "tracker", "region": "asia-south1", "gcp_project": "shipcube-apps-ks",
       "service_name": "tracker-b2ef0b", "app_id": "a1",
       "caps": {"cpu": "2", "memory": "1Gi", "max_instances": 3}}


class Unavailable(Exception):
    pass


class Provider:
    def __init__(self, fail: bool = False) -> None:
        self.fail, self.seen = fail, []

    def metrics(self, target, window="1h"):
        self.seen.append(target)
        if self.fail:
            raise MonitorUnavailable("timeouts", 503)
        return {"app": target.app, "window": window}

    def logs(self, target, limit=50, severity=None, page_token=None):
        self.seen.append(target)
        return {"entries": [], "next_page_token": page_token}


def test_target_from_api_app_view():
    t = target_for(APP)
    assert (t.project_id, t.service, t.region, t.app_id) == ("shipcube-apps-ks", "tracker-b2ef0b", "asia-south1", "a1")
    assert t.cpu == 2 and t.memory_mib == 1024 and t.caps["max_instances"] == 3
    assert target_for({**APP, "caps": {"memory": "512Mi"}}).memory_mib == 512


def test_adapter_passes_through_and_maps_unavailable():
    p = Provider()
    tel = MonitorTelemetry(p, Unavailable)
    assert tel.metrics(APP, "24h") == {"app": "tracker", "window": "24h"}
    assert tel.logs(APP, 10, "ERROR", "tok")["next_page_token"] == "tok"
    with pytest.raises(Unavailable):
        MonitorTelemetry(Provider(fail=True), Unavailable).metrics(APP, "1h")
