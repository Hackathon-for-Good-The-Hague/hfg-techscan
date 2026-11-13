"""Example custom check: flag teams with fewer than N contributors."""
from techscan.checks import Check, CheckContext

MIN_CONTRIBUTORS = 2


class MinContributorsCheck(Check):
    code = "few_contributors"
    label = "Minimum contributors check"

    def run(self, ctx: CheckContext) -> list[dict]:
        contributors = ctx.facts.get("timeline", {}).get("contributors", 0)
        if contributors < MIN_CONTRIBUTORS:
            return [self.flag("info",
                              f"Only {contributors} contributor(s) in the git history",
                              f"Expected at least {MIN_CONTRIBUTORS}. "
                              "Could be normal for small teams, or the team may be committing from one account.")]
        return []
