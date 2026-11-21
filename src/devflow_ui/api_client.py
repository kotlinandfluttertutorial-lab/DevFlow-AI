"""HTTP boundary for Streamlit's backend requests."""

import logging

import httpx

logger = logging.getLogger(__name__)


class APIClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.last_error_code: str | None = None

    async def get_health(self) -> dict[str, str] | None:
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                response = await client.get(f"{self.base_url}/health")
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            logger.warning("Backend health request failed")
            return None

        if not isinstance(data, dict):
            logger.warning("Backend health response is invalid")
            return None
        status = data.get("status")
        version = data.get("version")
        if not isinstance(status, str) or not isinstance(version, str):
            logger.warning("Backend health response is invalid")
            return None
        return {"status": status, "version": version}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        payload: dict[str, object] | None = None,
        timeout: float | None = 3.0,
    ) -> object:
        self.last_error_code = None
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                response = await client.request(
                    method, f"{self.base_url}/api/v1{path}", params=params, json=payload
                )
                response.raise_for_status()
                if method == "DELETE":
                    return response.status_code == 204
                data = response.json()
                if data is None:
                    logger.warning("Backend response is null")
                return data
        except httpx.HTTPStatusError as exc:
            try:
                error = exc.response.json()
            except ValueError:
                error = {}
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                self.last_error_code = error["code"]
            logger.warning("Backend request was rejected")
            return None
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            logger.warning("Backend request failed")
            return None

    async def list_projects(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, object]] | None:
        params: dict[str, str | int] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        data = await self._request("GET", "/projects", params=params)
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            return None
        return list(data)

    async def create_project(
        self, name: str, description: str | None = None, tags: list[str] | None = None
    ) -> dict[str, object] | None:
        data = await self._request(
            "POST",
            "/projects",
            payload={"name": name, "description": description, "tags": tags or []},
        )
        return data if isinstance(data, dict) else None

    async def update_project(self, project_id: int, **fields: object) -> dict[str, object] | None:
        data = await self._request("PUT", f"/projects/{project_id}", payload=fields)
        return data if isinstance(data, dict) else None

    async def delete_project(self, project_id: int) -> bool:
        return await self._request("DELETE", f"/projects/{project_id}") is True

    async def _list(
        self, path: str, params: dict[str, str | int] | None = None
    ) -> list[dict[str, object]] | None:
        data = await self._request("GET", path, params=params)
        if isinstance(data, list) and all(isinstance(item, dict) for item in data):
            return list(data)
        if data is not None:
            logger.warning("Backend list response is invalid")
        return None

    async def _record(
        self, method: str, path: str, payload: dict[str, object] | None = None
    ) -> dict[str, object] | None:
        data = await self._request(method, path, payload=payload)
        if isinstance(data, dict):
            return data
        if data is not None:
            logger.warning("Backend record response is invalid")
        return None

    async def list_workflows(
        self, project_id: int | None = None, status: str | None = None
    ) -> list[dict[str, object]] | None:
        params: dict[str, str | int] = {}
        if project_id is not None:
            params["project_id"] = project_id
        if status is not None:
            params["status"] = status
        return await self._list("/workflows", params)

    async def create_workflow(
        self, name: str, project_id: int | None = None
    ) -> dict[str, object] | None:
        return await self._record("POST", "/workflows", {"name": name, "project_id": project_id})

    async def get_workflow(self, workflow_id: int) -> dict[str, object] | None:
        return await self._record("GET", f"/workflows/{workflow_id}")

    async def update_workflow(self, workflow_id: int, **fields: object) -> dict[str, object] | None:
        return await self._record("PUT", f"/workflows/{workflow_id}", fields)

    async def delete_workflow(self, workflow_id: int) -> bool | None:
        return True if await self._request("DELETE", f"/workflows/{workflow_id}") is True else None

    async def create_run(self, workflow_id: int) -> dict[str, object] | None:
        return await self._record("POST", f"/workflows/{workflow_id}/runs")

    async def list_runs(self, workflow_id: int) -> list[dict[str, object]] | None:
        return await self._list(f"/workflows/{workflow_id}/runs")

    async def get_run(self, run_id: int) -> dict[str, object] | None:
        return await self._record("GET", f"/workflow-runs/{run_id}")

    async def start_run(self, run_id: int) -> dict[str, object] | None:
        data = await self._request("POST", f"/workflow-runs/{run_id}/start", timeout=None)
        return dict(data) if isinstance(data, dict) else None

    async def cancel_run(self, run_id: int) -> dict[str, object] | None:
        return await self._record("POST", f"/workflow-runs/{run_id}/cancel")

    async def list_steps(self, run_id: int) -> list[dict[str, object]] | None:
        return await self._list(f"/workflow-runs/{run_id}/steps")

    async def get_step(self, run_id: int, step_id: int) -> dict[str, object] | None:
        return await self._record("GET", f"/workflow-runs/{run_id}/steps/{step_id}")

    async def list_run_audit(self, run_id: int) -> list[dict[str, object]] | None:
        return await self._list(f"/workflow-runs/{run_id}/audit")

    async def list_approvals(self, status: str = "pending") -> list[dict[str, object]] | None:
        return await self._list("/approvals", {"status": status})

    async def get_approval(self, approval_id: int) -> dict[str, object] | None:
        return await self._record("GET", f"/approvals/{approval_id}")

    async def list_integrations(self) -> list[dict[str, object]] | None:
        return await self._list("/integrations")

    async def list_repositories(
        self, project_id: int | None = None
    ) -> list[dict[str, object]] | None:
        params: dict[str, str | int] | None = (
            None if project_id is None else {"project_id": project_id}
        )
        return await self._list("/repositories", params)

    async def get_integration_health(self, name: str) -> dict[str, object] | None:
        from urllib.parse import quote

        return await self._record("GET", f"/integrations/{quote(name, safe='')}/health")

    async def resolve_approval(
        self, approval_id: int, decision: str, resolved_by: str | None = None
    ) -> dict[str, object] | None:
        return await self._record(
            "POST",
            f"/approvals/{approval_id}/resolve",
            {"decision": decision, "resolved_by": resolved_by},
        )
