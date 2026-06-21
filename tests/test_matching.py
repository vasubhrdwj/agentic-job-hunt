import json
from pathlib import Path

from job_hunt_agent.matching import ResumeFitScorer
from job_hunt_agent.schemas import Role


ROOT = Path(__file__).resolve().parents[1]


def _calibration_roles() -> tuple[Role, Role]:
    google_jobs = json.loads(
        (ROOT / "tests/fixtures/google_jobs_sample.json").read_text(encoding="utf-8")
    )
    backend_job = next(
        item for item in google_jobs["jobs_results"] if item["company_name"] == "MongoDB"
    )
    lever_jobs = json.loads(
        (ROOT / "tests/fixtures/adapters/lever.json").read_text(encoding="utf-8")
    )
    irrelevant_job = lever_jobs[0]
    backend = Role(
        company=backend_job["company_name"],
        title=backend_job["title"],
        url=backend_job["apply_options"][0]["link"],
        location=backend_job["location"],
        summary=backend_job["description"][:300],
        match_reason="Unscored.",
        raw_description=backend_job["description"],
    )
    irrelevant = Role(
        company="Palantir",
        title=irrelevant_job["text"],
        url=irrelevant_job["applyUrl"],
        location=irrelevant_job["categories"]["location"],
        summary=irrelevant_job["descriptionPlain"][:300],
        match_reason="Unscored.",
        raw_description=irrelevant_job["descriptionPlain"],
    )
    return backend, irrelevant


def test_backend_role_scores_above_irrelevant_role_by_documented_margin():
    resume = (ROOT / "fixtures/sample_resume.txt").read_text(encoding="utf-8")
    backend, irrelevant = _calibration_roles()
    scorer = ResumeFitScorer()

    backend_score = scorer.evaluate(resume, backend).score
    irrelevant_score = scorer.evaluate(resume, irrelevant).score

    assert backend_score > irrelevant_score
    assert backend_score - irrelevant_score >= 0.10


def test_score_role_sets_bounded_score_and_quotes_real_jd_evidence():
    resume = (ROOT / "fixtures/sample_resume.txt").read_text(encoding="utf-8")
    backend, _ = _calibration_roles()

    scored = ResumeFitScorer().score_role(resume, backend)

    assert scored.fit_score is not None
    assert 0 <= scored.fit_score <= 1
    assert "JD evidence:" in scored.match_reason
    quoted = scored.match_reason.split('JD evidence: "', 1)[1].rsplit('"', 1)[0]
    assert quoted.rstrip(".") in backend.raw_description


def test_rank_roles_is_descending_and_does_not_mutate_inputs():
    resume = (ROOT / "fixtures/sample_resume.txt").read_text(encoding="utf-8")
    backend, irrelevant = _calibration_roles()

    ranked = ResumeFitScorer().rank_roles(resume, [irrelevant, backend])

    assert ranked[0].fit_score >= ranked[1].fit_score
    assert ranked[0].company == backend.company
    assert backend.fit_score is None
    assert irrelevant.fit_score is None


def test_missing_description_degrades_to_summary_and_empty_resume_scores_zero():
    backend, _ = _calibration_roles()
    summary_only = backend.model_copy(update={"raw_description": None})
    scorer = ResumeFitScorer()

    assert scorer.evaluate("", summary_only).score == 0
    assert scorer.evaluate("Python backend services", summary_only).score > 0
