"""Role-based access control.

Permissions are declared per capability, not per endpoint. A tool that returns
row-level members and a tool that returns an aggregate are different risks even
though both are "read".
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# capability -> roles allowed
PERMISSIONS: dict[str, set[str]] = {
    "segment_summary": {"viewer", "analyst", "admin"},
    "list_campaigns": {"viewer", "analyst", "admin"},
    "get_member_profile": {"analyst", "admin"},  # row-level, needs masking
    "create_campaign_task": {"admin"},  # write
}

ROLES = ("viewer", "analyst", "admin")


@dataclass
class Principal:
    role: str
    name: str = "anonymous"

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}; choose from {ROLES}")

    def can(self, capability: str) -> bool:
        allowed = PERMISSIONS.get(capability)
        if allowed is None:
            return False  # unknown capability is denied, not allowed
        return self.role in allowed

    def require(self, capability: str) -> None:
        if not self.can(capability):
            raise PermissionError(f"role {self.role!r} 无权调用 {capability}")


def principal_from_env() -> Principal:
    role = os.getenv("MCPOPS_ROLE", "viewer").strip().lower()
    name = os.getenv("MCPOPS_USER", "anonymous")
    return Principal(role=role, name=name)
