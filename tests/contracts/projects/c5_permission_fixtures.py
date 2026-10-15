"""Reusable C5 permission-contract fixtures and HTTP helpers.

The helpers encode C5-AC1/AC7/AC8/AC9 lifecycle and authorization contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from fastapi.testclient import TestClient


TOKENS = {
    "owner": "c5-owner-token",
    "admin": "c5-admin-token",
    "governor": "c5-governor-token",
    "editor": "c5-editor-token",
    "cross": "c5-cross-project-token",
    "revoked_editor": "c5-revoked-editor-token",
    "cross_project_editor": "c5-cross-project-token",
    "unknown": "c5-unknown-token",
}


def local_subject(token: str) -> str:
    return "local:" + hashlib.sha256(token.encode()).hexdigest()[:32]


def headers(principal: str) -> dict[str, str]:
    role = "admin" if principal == "admin" else "governor" if principal == "governor" else "editor"
    return {"Authorization": f"Bearer {TOKENS[principal]}", "X-User-Role": role}


@dataclass(frozen=True)
class PermissionFixture:
    client: TestClient

    def create_project(self, name: str = "C5 project") -> str:
        response = self.client.post(
            "/api/asset-registry/projects",
            headers=headers("owner"),
            json={"name": name, "project_type": "film"},
        )
        assert response.status_code == 201, response.text
        project_id = response.json()["project"]["project_id"]
        self.grant_read(project_id, TOKENS["revoked_editor"])
        self.revoke_read(project_id, TOKENS["revoked_editor"])
        cross = self.client.post(
            "/api/asset-registry/projects",
            headers=headers("cross_project_editor"),
            json={"name": "C5 cross project", "project_type": "film"},
        )
        assert cross.status_code == 201, cross.text
        return project_id

    def project(self, project_id: str) -> dict:
        response = self.client.get(f"/api/projects/{project_id}", headers=headers("owner"))
        assert response.status_code == 200, response.text
        return response.json()["project"]

    def archive(self, project_id: str) -> None:
        """Archive a project through the C5-AC1 governance lifecycle endpoint."""
        version = self.project(project_id)["version"]
        response = self.client.request(
            "DELETE",
            f"/api/asset-registry/projects/{project_id}",
            headers=headers("governor"),
            json={"expected_version": version},
        )
        assert response.status_code == 200, response.text

    def trash(self, project_id: str) -> None:
        """Move an already archived project to trash (C5-AC1/AC9)."""
        self.archive(project_id)
        version = self.project(project_id)["version"]
        response = self.client.post(
            f"/api/asset-registry/projects/{project_id}/trash",
            headers=headers("governor"),
            json={"expected_version": version},
        )
        assert response.status_code == 200, response.text

    def grant_read(self, project_id: str, token: str) -> None:
        response = self.client.post(f"/api/asset-registry/projects/{project_id}/read-grants",
                                    headers=headers("owner"), json={"subject": local_subject(token)})
        assert response.status_code == 200, response.text

    def revoke_read(self, project_id: str, token: str) -> None:
        response = self.client.post(f"/api/asset-registry/projects/{project_id}/read-grants/revoke",
                                    headers=headers("owner"), json={"subject": local_subject(token)})
        assert response.status_code == 200, response.text


def make_fixture(client: TestClient) -> PermissionFixture:
    return PermissionFixture(client)
