"""Example custom check: verify every team has a LICENSE file."""
from techscan.checks import Check, CheckContext


class LicenseCheck(Check):
    code = "missing_license"
    label = "License file check"

    def run(self, ctx: CheckContext) -> list[dict]:
        has_license = any(
            f["path"].lower().startswith("license")
            for f in ctx.inventory.files
        )
        if not has_license:
            return [self.flag("low", "No LICENSE file in the repository")]
        return []
