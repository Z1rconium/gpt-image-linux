import asyncio

from backend.app.core import settings as config
from backend.app.services import job_executor


def test_claimed_image_unit_renews_lease_without_progress_callbacks(monkeypatch):
    renewals: list[tuple[str, str, int]] = []

    async def run_scenario():
        parent = {
            "job_id": "heartbeat-parent",
            "status": "running",
            "n": 1,
            "api_path": "/v1/images/generations",
        }

        async def fake_run_db_operation(callback, *args, metric_name=None, **kwargs):
            return callback(*args, **kwargs)

        def fake_get_generate_job(_job_id):
            return parent

        def fake_get_preset_for_unit(_unit):
            return {
                "api_url": "https://api.example.com",
                "api_key": "unused",
                "name": "Default",
            }

        def fake_update_progress(*_args, **_kwargs):
            return {"status": "running"}

        def fake_renew(unit_id, *, claimed_by, claim_epoch, claim_expires_at):
            renewals.append((unit_id, claimed_by, claim_epoch))
            return True

        def fake_fail(*_args, **_kwargs):
            return {"status": "upstream_error"}

        async def slow_generation(*_args, **_kwargs):
            await asyncio.sleep(0.25)
            return []

        async def no_op_async(*_args, **_kwargs):
            return None

        monkeypatch.setattr(config, "IMAGE_JOB_UNIT_LEASE_SECONDS", 0.3)
        monkeypatch.setattr(job_executor, "run_db_operation", fake_run_db_operation)
        monkeypatch.setattr(job_executor, "get_generate_job", fake_get_generate_job)
        monkeypatch.setattr(job_executor, "get_preset_for_unit", fake_get_preset_for_unit)
        monkeypatch.setattr(job_executor, "get_effective_preset_api_key", lambda _preset: "key")
        monkeypatch.setattr(job_executor, "get_upstream_socks5_proxy", lambda: None)
        monkeypatch.setattr(job_executor, "update_image_job_unit_progress", fake_update_progress)
        monkeypatch.setattr(job_executor, "renew_image_job_unit_lease", fake_renew)
        monkeypatch.setattr(job_executor, "fail_image_job_unit", fake_fail)
        monkeypatch.setattr(job_executor.proxy, "call_image_generation_api", slow_generation)
        monkeypatch.setattr(job_executor, "store_generate_job_async", no_op_async)
        monkeypatch.setattr(job_executor, "aggregate_parent_image_job", no_op_async)

        await job_executor.run_claimed_image_unit(
            {
                "unit_id": "heartbeat-unit",
                "parent_job_id": "heartbeat-parent",
                "operation": "generation",
                "claim_epoch": 7,
                "request": {"prompt": "heartbeat", "n": 1},
                "api_path": "/v1/images/generations",
                "api_preset_name": "Default",
            },
            "worker-a",
        )

    asyncio.run(run_scenario())

    assert len(renewals) >= 2
    assert set(renewals) == {("heartbeat-unit", "worker-a", 7)}
