# RepoWise Pilot

RepoWise provides advisory local code-health, Git-history, dependency, and MCP
context for the Bifrost platform. It does not replace tests, review, or runtime
verification, and it is not a merge gate during calibration.

```bash
pipx install repowise==0.44.0
repowise init --no-prose --no-editor-setup --no-agents -y
repowise update --no-agents
repowise health --refactoring-targets
repowise risk main..HEAD
```

The generated index is ignored and the lean MCP configuration is reviewed
source. Treat complexity, churn, prior-defect, and centrality findings as
prioritization evidence. Test association, dead-code classification, and
absolute scores remain advisory. Family scorecards and dispositions live in
`MTG-Thomas/bifrost-ops`.
