import re
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    repo_url: str = Field(max_length=500)
    default_branch: str = Field(default="main", min_length=1, max_length=150)

    @field_validator("repo_url")
    @classmethod
    def github_https_only(cls, v: str) -> str:
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", v):
            raise ValueError("Only HTTPS GitHub repository URLs are supported")
        return v

class TaskIn(BaseModel):
    key: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,79}$")
    kind: Literal["command", "agent"] = "command"
    argv: list[str] = Field(default_factory=list, max_length=40)
    prompt: str = Field(default="", max_length=12000)
    model: str = Field(default="", max_length=60)
    depends_on: list[str] = Field(default_factory=list, max_length=50)
    required_tags: list[str] = Field(default_factory=list, max_length=20)
    risk: Literal["normal", "elevated", "merge", "deploy"] = "normal"
    max_attempts: int = Field(default=2, ge=1, le=5)
    timeout_seconds: int = Field(default=900, ge=30, le=7200)

    @model_validator(mode="after")
    def check_kind(self):
        if self.kind == "agent" and (not self.model or not self.prompt):
            raise ValueError("agent requires model and prompt")
        if self.kind == "command" and not self.argv:
            raise ValueError("command requires nonempty argv")
        if any(not x or len(x) > 2000 or "\x00" in x for x in self.argv):
            raise ValueError("invalid argv")
        if self.kind == "command" and not self.argv[0].replace("_","").replace("-","").isalnum():
            raise ValueError("executable must be a simple name")
        return self

class RunIn(BaseModel):
    project_id: str
    title: str = Field(min_length=2, max_length=200)
    tasks: list[TaskIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def check_dag(self):
        keys = [t.key for t in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate task keys")
        keyset = set(keys)
        deps = {t.key: t.depends_on for t in self.tasks}
        for k, entries in deps.items():
            if len(entries) != len(set(entries)) or not set(entries).issubset(keyset):
                raise ValueError(f"invalid dependencies for {k}")
        seen, visiting = set(), set()
        def visit(k):
            if k in visiting:
                raise ValueError("dependency cycle")
            if k in seen:
                return
            visiting.add(k)
            for dep in deps[k]:
                visit(dep)
            visiting.remove(k)
            seen.add(k)
        for k in keys:
            visit(k)
        return self

class EnrollIn(BaseModel):
    name: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    tags: list[str] = Field(default_factory=list, max_length=20)
    models: list[str] = Field(default_factory=list, max_length=20)
    platform: str = Field(default="", max_length=100)

class HeartbeatIn(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=20)
    models: list[str] = Field(default_factory=list, max_length=20)
    platform: str = Field(default="", max_length=100)

class LeaseIn(BaseModel):
    lease_token: str

class CompleteIn(LeaseIn):
    success: bool
    exit_code: int | None = None
    output: str = Field(default="", max_length=40000)
