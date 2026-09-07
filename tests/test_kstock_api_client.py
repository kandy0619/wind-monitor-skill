import io
import json
import urllib.error

import pytest

from scripts.kstock_api_client import KStockAPIError, KStockClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_capabilities_and_claim_send_versioned_contract(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/capabilities"):
            return FakeResponse({"contract_versions": ["1.0"], "database_ready": True})
        return FakeResponse({"created": True, "run": {"id": 1}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = KStockClient("http://127.0.0.1:8002/api/v1/internal/wind-monitor", "secret")
    client.capabilities()
    result = client.claim_run({
        "task_id": 1,
        "trade_date": "2026-08-13",
        "planned_time": "15:00",
        "mode": "intraday",
        "report_type": "intraday",
        "triggered_at": "2026-08-13T07:00:00Z",
        "lease_owner": "pytest",
        "skill_version": "test",
        "adapter_version": "test",
    })
    assert result["run"]["id"] == 1
    assert requests[-1].headers["Authorization"] == "Bearer secret"
    assert requests[-1].headers["Idempotency-key"].startswith("claim:1:2026-08-13:15:00")
    body = json.loads(requests[-1].data)
    assert body["contract_version"] == "1.0"


def test_client_classifies_service_unavailable_without_leaking_detail(monkeypatch):
    def fail(*_args, **_kwargs):
        raise urllib.error.URLError("contains-local-network-detail")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(KStockAPIError) as caught:
        KStockClient("http://127.0.0.1").capabilities()
    assert caught.value.retryable is True
    assert str(caught.value) == "KStock服务不可用"


def test_client_rejects_incompatible_contract(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse({"contract_versions": ["2.0"], "database_ready": True}),
    )
    with pytest.raises(KStockAPIError) as caught:
        KStockClient("http://127.0.0.1").capabilities()
    assert caught.value.retryable is False


def test_feishu_dispatch_uses_delivery_specific_timeout(monkeypatch):
    observed = []

    def fake_urlopen(_request, timeout):
        observed.append(timeout)
        return FakeResponse({"success": True})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = KStockClient("http://127.0.0.1", timeout_seconds=20)
    assert client.dispatch_feishu("report-1")["success"] is True
    assert observed == [60.0]
