"""Approved read-only context-policy identities; policy text grants no permissions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyArm:
    arm_id: str
    version: str
    approved: bool
    read_only: bool = True
    max_context: int = 8192

    def eligible(self):
        return (self.approved is True and self.read_only is True and isinstance(self.arm_id,str)
                and bool(self.arm_id) and isinstance(self.version,str) and bool(self.version)
                and type(self.max_context) is int and 0 <= self.max_context <= 32768)
