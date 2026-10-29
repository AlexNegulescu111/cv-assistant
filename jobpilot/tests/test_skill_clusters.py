"""Offline tests for jobpilot.skill_clusters: local grouping, skill_clusters.json, counting distinct ads.

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from jobpilot import skill_clusters as sc  # noqa: E402
from jobpilot.workspace import Workspace  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def row(skill, times, jobs, priority="medium", status="to learn", first="2026-10-10", last="2026-10-12"):
    return {"skill": skill, "category": "Data", "times_requested": str(times), "jobs": " | ".join(jobs),
            "priority": priority, "status": status, "first_seen": first, "last_seen": last}


def test_keys():
    check("one-term alias", sc.keys("Spark (PySpark)"), ("spark", {"pyspark"}))
    check("acronym alias", sc.keys("Retrieval-Augmented Generation (RAG)"), ("retrieval augmented generation", {"rag"}))
    check("qualifier is not an alias", sc.keys("Playwright (TypeScript)"), ("playwright", set()))
    check("descriptive parentheses dropped", sc.keys("RAG (embeddings, vector databases)"), ("rag", set()))
    check("slash parts", sc.keys("LangChain / LangGraph")[1], {"langchain", "langgraph"})
    check("short slash parts kept whole", sc.keys("CI/CD"), ("ci/cd", set()))
    check("diacritics and case", sc.keys("Învățare Automată")[0], "invatare automata")
    check("synonym", sc.keys("K8s")[0], "kubernetes")


def test_local_groups():
    rows = [
        row("PySpark", 2, ["A - Data Engineer", "B - Data Engineer"]),
        row("Spark (PySpark)", 2, ["B - Data Engineer", "C - ML Engineer"], priority="high", last="2026-10-20"),
        row("Data Quality", 1, ["D - Analyst"]),
        row("quality data", 1, ["E - Analyst"]),
        row("Playwright (TypeScript)", 1, ["F - QA"]),
        row("TypeScript", 3, ["G - Dev", "H - Dev", "I - Dev"]),
        row("Kubernetes", 1, ["J - Dev"], status="learning"),
        row("k8s", 2, ["K - Dev"]),   # one ask without the ad's name
        row("Azure Data Factory", 1, ["L - Data"]),
        row("Azure Data Factory (ADF)", 1, ["L - Data"]),
    ]
    m = {r["skill"]: r for r in sc.merged(rows)}
    check("groups", sorted(m), ["Azure Data Factory", "Data Quality", "Kubernetes", "Playwright (TypeScript)",
                                "PySpark", "TypeScript"])
    check("distinct ads, the shared one once", m["PySpark"]["times_requested"], 3)
    check("variants", m["PySpark"]["variants"], ["Spark (PySpark)"])
    check("highest priority", m["PySpark"]["priority"], "high")
    check("seen range", (m["PySpark"]["first_seen"], m["PySpark"]["last_seen"]), ("2026-10-10", "2026-10-20"))
    check("same words, other order", m["Data Quality"]["times_requested"], 2)
    check("a qualifier keeps its own row", m["TypeScript"]["times_requested"], 3)
    check("synonym + unnamed ask counted", m["Kubernetes"]["times_requested"], 3)
    check("learning wins", m["Kubernetes"]["status"], "learning")
    check("same ad listing both counts once", m["Azure Data Factory"]["times_requested"], 1)
    order = [r["skill"] for r in sc.merged(rows)]
    check("most asked first", order[:3], ["Kubernetes", "PySpark", "TypeScript"])


def test_cluster_file():
    ws = Workspace(tempfile.mkdtemp())
    rows = [
        row("LLM orchestration", 2, ["A - AI", "B - AI"]),
        row("LangChain / LangGraph", 3, ["B - AI", "C - AI", "D - AI"]),
        row("Power BI", 1, ["E - BI"]),
        row("Tableau", 1, ["F - BI"]),
    ]
    check("no file", sc.load(ws), {})
    check("all ungrouped", sc.ungrouped(rows, {}), [r["skill"] for r in rows])
    with open(ws.path("skill_clusters"), "w", encoding="utf-8") as f:
        json.dump({"LLM frameworks (LangChain / LangGraph)": ["LLM orchestration", "LangChain / LangGraph"],
                   "Power BI": [], "_note": "ignored"}, f)
    clusters = sc.load(ws)
    check("loaded", sorted(clusters), ["LLM frameworks (LangChain / LangGraph)", "Power BI"])
    m = {r["skill"]: r for r in sc.merged(rows, clusters)}
    check("canonical name", sorted(m), ["LLM frameworks (LangChain / LangGraph)", "Power BI", "Tableau"])
    g = m["LLM frameworks (LangChain / LangGraph)"]
    check("semantic group counts distinct ads", g["times_requested"], 4)
    check("both names are variants", sorted(g["variants"]), ["LLM orchestration", "LangChain / LangGraph"])
    check("only the unseen name is left", sc.ungrouped(rows, clusters), ["Tableau"])
    rows = [row("PySpark", 2, ["A - DE", "B - DE"]),
            row("Big data processing at scale (Spark SQL, Delta Lake)", 2, ["B - DE", "C - DE"]),
            row("MLOps & Responsible AI", 1, ["D - ML"]),
            row("ML pipeline deployment & monitoring (MLflow, model serving)", 1, ["E - ML"])]
    clusters = {"Apache Spark": ["PySpark", "Big data processing at scale (Spark SQL, Delta Lake)"],
                "MLOps": ["MLOps & Responsible AI", "ML pipeline deployment & monitoring (MLflow, model serving)"]}
    m = {r["skill"]: r for r in sc.merged(rows, clusters)}
    check("variants with descriptive parentheses join their group",
          {k: (v["times_requested"], len(v["variants"])) for k, v in m.items()}, {"Apache Spark": (3, 2), "MLOps": (2, 2)})
    with open(ws.path("skill_clusters"), "w", encoding="utf-8") as f:
        f.write("{broken")
    check("broken file → {}", sc.load(ws), {})


TESTS = (test_keys, test_local_groups, test_cluster_file)

if __name__ == "__main__":
    for t in TESTS:
        t()
    if FAILS:
        print("\n".join("FAIL " + f for f in FAILS))
        sys.exit(1)
    print("OK all skill_clusters tests passed")
