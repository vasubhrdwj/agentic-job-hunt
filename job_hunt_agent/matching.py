"""Deterministic resume-to-job fit scoring with evidence from the job description."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from job_hunt_agent.schemas import Role


_TOKEN = re.compile(r"[a-z][a-z0-9+#.-]{1,}")
_SENTENCE = re.compile(r"(?:^|[\n\r]+|(?<=[.!?]))\s*")
_STOP_WORDS = {
    "ability",
    "abilities",
    "about",
    "after",
    "also",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "before",
    "being",
    "but",
    "by",
    "can",
    "candidate",
    "candidates",
    "company",
    "developer",
    "developers",
    "development",
    "do",
    "does",
    "during",
    "each",
    "engineer",
    "engineering",
    "engineers",
    "experience",
    "for",
    "from",
    "has",
    "have",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "job",
    "jobs",
    "knowledge",
    "looking",
    "may",
    "must",
    "more",
    "of",
    "on",
    "one",
    "or",
    "other",
    "our",
    "people",
    "position",
    "required",
    "requirement",
    "requirements",
    "responsibilities",
    "responsibility",
    "role",
    "should",
    "skill",
    "skills",
    "software",
    "strong",
    "team",
    "teams",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "understanding",
    "up",
    "us",
    "using",
    "via",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "will",
    "with",
    "work",
    "working",
    "would",
    "year",
    "years",
    "you",
    "your",
}


@dataclass(frozen=True)
class FitEvidence:
    score: float
    matched_terms: tuple[str, ...]
    requirement: str | None


class ResumeFitScorer:
    """Score and rank roles without network calls or fabricated evidence."""

    def score_role(self, resume_text: str, role: Role) -> Role:
        evidence = self.evaluate(resume_text, role)
        reason = _match_reason(evidence)
        return role.model_copy(
            update={
                "fit_score": evidence.score,
                "match_reason": reason,
            },
            deep=True,
        )

    def rank_roles(self, resume_text: str, roles: Iterable[Role]) -> list[Role]:
        scored = [self.score_role(resume_text, role) for role in roles]
        return sorted(
            scored,
            key=lambda role: (
                role.confidence >= 0.5,
                role.fit_score is not None,
                role.fit_score or 0.0,
                role.confidence,
            ),
            reverse=True,
        )

    def evaluate(self, resume_text: str, role: Role) -> FitEvidence:
        resume_tokens = _tokens(resume_text)
        jd_text = role.raw_description or role.summary
        jd_tokens = _tokens(jd_text)
        title_tokens = _tokens(role.title)
        if not resume_tokens or not jd_tokens:
            return FitEvidence(score=0.0, matched_terms=(), requirement=None)

        resume_set = set(resume_tokens)
        jd_set = set(jd_tokens)
        overlap = resume_set & jd_set
        weighted_overlap = _weighted_overlap(resume_tokens, jd_tokens, overlap)
        title_overlap = len(resume_set & set(title_tokens)) / max(1, len(set(title_tokens)))
        phrase_overlap = _phrase_overlap(resume_tokens, jd_tokens)
        requirement, requirement_score = _best_requirement(jd_text, resume_set)

        score = min(
            1.0,
            0.35 * weighted_overlap
            + 0.20 * title_overlap
            + 0.15 * phrase_overlap
            + 0.30 * requirement_score,
        )
        matched_terms = tuple(
            term
            for term, _ in sorted(
                ((term, _term_weight(term, resume_tokens, jd_tokens)) for term in overlap),
                key=lambda item: (-item[1], item[0]),
            )[:8]
        )
        return FitEvidence(
            score=round(score, 4),
            matched_terms=matched_terms,
            requirement=requirement,
        )


def _tokens(text: str) -> list[str]:
    return [
        token.strip(".-")
        for token in _TOKEN.findall(text.casefold())
        if token.strip(".-") not in _STOP_WORDS
    ]


def _weighted_overlap(
    resume_tokens: list[str],
    jd_tokens: list[str],
    overlap: set[str],
) -> float:
    if not overlap:
        return 0.0
    weights = [_term_weight(term, resume_tokens, jd_tokens) for term in overlap]
    denominator = sum(
        sorted(
            (_term_weight(term, resume_tokens, jd_tokens) for term in set(jd_tokens)),
            reverse=True,
        )[: max(8, min(40, len(set(jd_tokens))))]
    )
    return min(1.0, sum(weights) / max(1.0, denominator))


def _term_weight(term: str, resume_tokens: list[str], jd_tokens: list[str]) -> float:
    frequency = Counter(resume_tokens)[term] + Counter(jd_tokens)[term]
    specificity = min(2.5, 0.6 + len(term) / 7)
    return specificity * (1 + math.log1p(frequency))


def _phrase_overlap(resume_tokens: list[str], jd_tokens: list[str]) -> float:
    resume_phrases = set(zip(resume_tokens, resume_tokens[1:]))
    jd_phrases = set(zip(jd_tokens, jd_tokens[1:]))
    if not jd_phrases:
        return 0.0
    return min(1.0, len(resume_phrases & jd_phrases) / min(12, len(jd_phrases)))


def _best_requirement(jd_text: str, resume_tokens: set[str]) -> tuple[str | None, float]:
    best_sentence: str | None = None
    best_score = 0.0
    for raw_sentence in _SENTENCE.split(jd_text):
        sentence = " ".join(raw_sentence.split()).strip(" -•\t")
        sentence_tokens = set(_tokens(sentence))
        if len(sentence_tokens) < 2:
            continue
        score = len(sentence_tokens & resume_tokens) / len(sentence_tokens)
        if score > best_score:
            best_sentence = sentence
            best_score = score
    return best_sentence, min(1.0, best_score)


def _match_reason(evidence: FitEvidence) -> str:
    if not evidence.matched_terms:
        return "No meaningful resume-to-JD overlap was found; review this role manually."
    terms = ", ".join(evidence.matched_terms[:5])
    if evidence.requirement:
        requirement = evidence.requirement
        if len(requirement) > 220:
            requirement = requirement[:217].rsplit(" ", 1)[0] + "..."
        return f'Resume overlap: {terms}. JD evidence: "{requirement}"'
    return f"Resume overlap: {terms}. No quotable requirement was available."


__all__ = ["FitEvidence", "ResumeFitScorer"]
