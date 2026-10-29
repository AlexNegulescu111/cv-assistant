"""Where a user's data lives. The package holds code only; every path comes from a workspace folder.

The workspace root is, in order: --workspace, the JOBPILOT_WORKSPACE environment variable, the current folder.
An optional <root>/workspace.json overrides any of the default relative paths below, e.g.
    {"answers_pool": "ANSWERS_POOL.json", "applications_dir": "output/applications"}
"""
import json
import os

DEFAULTS = {
    "skills_pool": "MASTER_SKILLS_POOL.md",       # every CV claim and every scoring fact comes from here
    "answers_pool": "ANSWERS_POOL.json",          # reusable application-form answers
    "job_sources": "job_sources.json",            # job boards + filters for the collector
    "scoring": "scoring.json",                    # Jev scoring weights / thresholds
    "apply_config": "apply.json",                 # automatic applications: daily cap, top N, CVs
    "db": "output/jobs.sqlite",                   # collected jobs, scores, pipeline events
    "applications_dir": "output/applications",    # one folder per application (job.md, form.json, answers.json)
    "applied_log": "output/applications/applied.csv",
    "browser_profile": "output/applications/.browser-profile",
    "site_templates": "site_templates",           # career-site templates learned in this workspace (structure only)
    "profile": "profile.json",                    # name, CV languages, target fields (written at onboarding)
    "skill_clusters": "skill_clusters.json",      # demanded skills that name the same thing (display grouping)
    "drafts_dir": "drafts",
    "pdf_dir": "output/pdf",
    "env_file": ".env",                           # API keys (TYPESAFE_API_KEY), created by the user
}
CONFIG_NAME = "workspace.json"


class Workspace:
    def __init__(self, root=None):
        self.root = os.path.abspath(root or os.environ.get("JOBPILOT_WORKSPACE") or os.getcwd())
        self.overrides = {}
        cfg = os.path.join(self.root, CONFIG_NAME)
        if os.path.exists(cfg):
            with open(cfg, encoding="utf-8") as f:
                self.overrides = json.load(f)
            unknown = set(self.overrides) - set(DEFAULTS)
            if unknown:
                raise ValueError(f"{cfg}: unknown keys {sorted(unknown)} (known: {sorted(DEFAULTS)})")

    def path(self, key):
        return self.rel(self.overrides.get(key, DEFAULTS[key]))

    def rel(self, path):
        """A path inside the workspace (config values such as scoring.json → reference_draft) → absolute."""
        return os.path.normpath(path if os.path.isabs(path) else os.path.join(self.root, path))

    def env(self, name):
        """An environment variable, else a NAME=value line in the workspace .env (values are never printed)."""
        value = os.environ.get(name)
        env_file = self.path("env_file")
        if not value and os.path.exists(env_file):
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.strip().partition("=")
                    if k.strip() == name:
                        value = v.strip().strip("'\"")
        return value

    def __repr__(self):
        return f"Workspace({self.root!r})"


def add_argument(parser):
    parser.add_argument("--workspace", help="workspace folder (default: $JOBPILOT_WORKSPACE, else the current folder)")
