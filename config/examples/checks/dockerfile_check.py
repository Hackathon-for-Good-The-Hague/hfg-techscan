"""Example custom check: flag repos that have no Dockerfile."""
from techscan.checks import Check, CheckContext


class DockerfileCheck(Check):
    code = "no_dockerfile"
    label = "Dockerfile check"

    def run(self, ctx: CheckContext) -> list[dict]:
        has_dockerfile = any(
            f["path"].lower().rsplit("/", 1)[-1] in ("dockerfile", "dockerfile.dev", "dockerfile.prod")
            or f["path"].lower().endswith("/dockerfile")
            for f in ctx.inventory.files
        )
        if not has_dockerfile:
            return [self.flag("info", "No Dockerfile found",
                              "The project may not be containerised. Ask the team how they deploy.")]
        return []
