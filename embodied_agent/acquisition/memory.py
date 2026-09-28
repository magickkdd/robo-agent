"""SPEC-v0.2 §5.6's last box: the 可复用 Skill Library, which a program enters only if it validated.

The pipeline ends at `Skill Memory`, and the sentence above it is the whole content of this module:
只有通过未见对象/布局/参数的验证，才能进入可复用 Skill Library. A memory that could be written to
directly would make that sentence a comment, so the checks below are arranged so that the *only*
path from a candidate to a stored skill is one that carries a report with it:

* `admit` takes the `SkillCandidate` **and** its `SkillValidationReport`, and refuses without either;
* a report must be for this candidate (`candidate_id`), for this program (`skill_name`), and must be
  `passed` under the frozen rule *and* clean under `validation.strict_findings` — the frozen
  `_derive_passed` reads neither an unmeasurable verdict nor an audit `violations` row, so a store
  that asked only for `passed` would admit a skill that was never measured at all;
* the report must carry the matrix fingerprint and the axis list, because a report that cannot say
  which of 对象/布局/参数 it varied is a count of runs, not a transfer claim;
* and a stored line is re-checked at load: an entry with no `admission` provenance block, or with an
  admission that says `passed_frozen_rule: false`, is refused there rather than repaired. That is why
  hand-writing the JSONL is not a way in.

What the store holds is a `SkillSpec(level="acquired")` — the same model the executor is handed by
`program.as_plan` — so an admitted skill needs no new interpreter (§5.6's "程序技能" limit), and §11's
`skill reuse success` row is about the same program that was validated, not a re-encoding of it.

`applicable()` deliberately returns *every* skill whose declared effects cover the asked-for one, and
never one. Which to try is a decision, and §9 says a decision belongs to the model-facing loop; a
store that ranked would be a policy wearing a dict.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from pydantic import ValidationError

from ..core.v02 import SCHEMA_VERSION, SkillCandidate, SkillSpec, SkillValidationReport
from . import program
from .gap import covers_effect
from .validation import AUXILIARY_KINDS, UNSEEN_KINDS, strict_findings

#: The provenance key an admitted spec carries. A stored line without it is not a skill; it is a bug.
ADMISSION_KEY = "admission"


class SkillMemoryError(ValueError):
    """An admission the gate refused. `reasons` are the store's words, verbatim, for the record."""

    def __init__(self, reasons: Sequence[str]):
        super().__init__("; ".join(reasons) or "refused")
        self.reasons = list(reasons)


@dataclass
class MemoryEntry:
    """One admitted skill and the evidence that admitted it.

    Kept next to the spec rather than inside a new field because `SkillSpec` is frozen (§6): the
    admission goes into `provenance`, which is where a v0.2 record is allowed to say where it came
    from, and the same dict is what `load` re-checks.
    """

    spec: SkillSpec
    admission: dict[str, Any] = field(default_factory=dict)

    @property
    def skill_name(self) -> str:
        return self.spec.name

    @property
    def skill_id(self) -> str:
        return self.spec.skill_id

    def as_dict(self) -> dict[str, Any]:
        return self.spec.model_dump(mode="json")

    @classmethod
    def from_dict(cls, doc: dict[str, Any]) -> "MemoryEntry":
        spec = SkillSpec.model_validate(doc)
        return cls(spec=spec, admission=dict((spec.provenance or {}).get(ADMISSION_KEY) or {}))


def admission_findings(candidate: SkillCandidate, report: SkillValidationReport,
                       *, existing: Iterable[str] = ()) -> list[str]:
    """Every reason this pair may not enter the library, in the order the gate asks them.

    Split out from `SkillMemory.admit` so a refusal can be *reported* — §11's
    `unsafe/invalid skill rate` needs the attempts as well as the admissions, and an exception that
    only travels upward would leave the denominator unmeasured.
    """
    spec = candidate.spec
    reasons: list[str] = []
    if report.candidate_id != candidate.candidate_id:
        reasons.append(f"the report is for candidate {report.candidate_id!r}, not "
                       f"{candidate.candidate_id!r}: a passed validation of somebody else's program "
                       f"is not evidence about this one")
    if report.skill_name != spec.name:
        reasons.append(f"the report validates {report.skill_name!r}; the candidate is {spec.name!r}")
    if candidate.status == "rejected":
        reasons.append("the candidate is already rejected; a rejected proposal needs a new candidate, "
                       "not a second attempt at the same row")
    if candidate.status == "library":
        reasons.append("the candidate is already in a library")
    if not report.passed:
        reasons.append("the frozen gate did not pass: "
                       + ("; ".join(report.reasons) or "no reasons recorded"))
    strict = strict_findings(report.instances, min_instances=report.min_instances)
    if strict:
        reasons.append("the strict reading did not pass either: " + "; ".join(strict))
    reasons += [f"the program is not executable as proposed: {r}" for r in program.check(spec)]
    unresolved = spec.unresolved_parameters()
    if unresolved:
        reasons.append(f"the procedure uses parameters the declaration does not: {unresolved}")
    if spec.level != "acquired":
        reasons.append(f"level {spec.level!r} is not an acquired skill; the primitives already ship "
                       f"and a composite is a different claim to validate")
    cost = dict(report.cost or {})
    if not cost.get("matrix_fingerprint"):
        reasons.append("the report carries no matrix fingerprint, so it cannot name the configuration "
                       "set it claims to have varied — a count of runs is not a transfer claim")
    axes = [a for a in (cost.get("axes_covered") or []) if a in UNSEEN_KINDS]
    missing = [a for a in UNSEEN_KINDS if a not in axes]
    if axes and missing:
        reasons.append(f"§5.6 axes never varied on a ran instance: {missing}")
    if existing:
        reasons.append(f"the name is already in the library: {sorted(set(existing))}")
    return reasons


def promote(candidate: SkillCandidate, report: SkillValidationReport) -> SkillCandidate:
    """The candidate as it reads after admission.

    Returns a new row rather than mutating: `status="library"` without a `validation_report_id` is
    unrepresentable (`SkillCandidate._gate` raises), and that frozen check is the reason this function
    has to take the report at all.
    """
    return SkillCandidate(**{**candidate.model_dump(mode="json"),
                             "status": "library",
                             "validation_report_id": report.report_id})


class SkillMemory:
    """An append-only set of validated acquired skills, backed by one JSONL file."""

    def __init__(self, path: Optional[str] = None,
                 entries: Optional[Iterable[MemoryEntry]] = None):
        self.path = path
        self.entries: list[MemoryEntry] = []
        self.refusals: list[dict[str, Any]] = []
        for entry in entries or ():
            self._store(entry)

    # ---------------------------------------------------------------- storage ----
    def _store(self, entry: MemoryEntry) -> None:
        problems: list[str] = []
        admission = entry.admission or {}
        if entry.spec.level != "acquired":
            problems.append(f"level {entry.spec.level!r}")
        if not admission:
            problems.append("no admission block: a stored skill must carry the report that admitted it")
        elif admission.get("passed_frozen_rule") is not True or admission.get("strict_findings"):
            problems.append(f"the admission itself records a gate that did not pass: "
                            f"{admission.get('passed_frozen_rule')!r}/"
                            f"{admission.get('strict_findings')!r}")
        if problems:
            raise SkillMemoryError([f"{entry.skill_name} cannot be held by the skill memory: {p}"
                                    for p in problems])
        named = {e.skill_name for e in self.entries}
        if entry.skill_name in named:
            raise SkillMemoryError([f"{entry.skill_name} is already stored; the memory is append-only "
                                    f"because an acquired skill that was overwritten would silently "
                                    f"change what an earlier episode could have reused"])
        self.entries.append(entry)

    @classmethod
    def load(cls, path: str) -> "SkillMemory":
        """Read every line of a store. A missing file is a cold start, not a failure.

        Each line goes through `SkillSpec` validation and then `_store`, so the append-time rules are
        also load-time rules: a hand-written line with no admission block, or one whose admission says
        the gate did not pass, is refused here rather than repaired. A line from another schema version
        is refused too, because `enabled`, `level` and `expected_effects` all mean something specific
        in the version that wrote them.
        """
        memory = cls(path)
        if not os.path.exists(path):
            return memory
        with open(path, encoding="utf-8") as fh:
            for number, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                except json.JSONDecodeError as e:
                    raise SkillMemoryError([f"{path}:{number} is not JSON: {e}"]) from e
                if doc.get("schema_version") != SCHEMA_VERSION:
                    raise SkillMemoryError([f"{path}:{number} was written by schema "
                                            f"{doc.get('schema_version')!r}, not {SCHEMA_VERSION!r}"])
                try:
                    memory._store(MemoryEntry.from_dict(doc))
                except (ValidationError, SkillMemoryError) as e:
                    reasons = getattr(e, "reasons", None) or [f"{type(e).__name__}: {e}"]
                    raise SkillMemoryError([f"{path}:{number}: {r}" for r in reasons]) from e
        return memory

    def admit(self, candidate: SkillCandidate, report: SkillValidationReport) -> MemoryEntry:
        """The only public way anything enters the library."""
        spec = candidate.spec
        existing = [e.skill_name for e in self.entries if e.skill_name == spec.name]
        reasons = admission_findings(candidate, report, existing=existing)
        if reasons:
            row = {"at": round(time.time(), 3), "skill_name": spec.name,
                   "skill_id": spec.skill_id, "candidate_id": candidate.candidate_id,
                   "report_id": report.report_id, "reasons": reasons}
            self.refusals.append(row)
            self._append_refusal(row)
            raise SkillMemoryError(reasons)
        admission = {
            "admitted_at": round(time.time(), 3),
            "candidate_id": candidate.candidate_id,
            "report_id": report.report_id,
            "min_instances": report.min_instances,
            "passed_frozen_rule": bool(report.passed),
            "frozen_reasons": list(report.reasons),
            "strict_findings": strict_findings(report.instances, min_instances=report.min_instances),
            "instances": len(report.instances),
            "kinds_covered": report.kinds_covered(),
            "axes_covered": [a for a in (dict(report.cost or {}).get("axes_covered") or [])],
            "axes_unavailable": dict(report.cost or {}).get("axes_unavailable") or {},
            "matrix_fingerprint": dict(report.cost or {}).get("matrix_fingerprint") or "",
            "proposal": dict(report.cost or {}).get("proposal"),
            "seeds": sorted({i.seed for i in report.instances if i.seed is not None}),
            "cost": {k: v for k, v in dict(report.cost or {}).items()
                     if k in ("wall_s", "sim_s", "calls", "model_calls", "sandbox_runs",
                              "fully_measured_true")},
            "schema_version_read": SCHEMA_VERSION,
        }
        stamped = spec.model_copy(deep=True)
        stamped.provenance = {**(spec.provenance or {}), ADMISSION_KEY: admission}
        entry = MemoryEntry(spec=stamped, admission=admission)
        self._store(entry)
        if self.path:
            self._append_line(entry)
        return entry

    def refuse(self, reasons: Sequence[str], *, spec: Optional[SkillSpec] = None,
               candidate_id: str = "", report_id: str = "") -> dict[str, Any]:
        """Record a refusal that a caller found elsewhere (representation gate, sandbox report).

        The gate raises; this is how the *count* survives the raise, since §11's invalid-skill rate
        needs every candidate that was tried and not merely the ones that were admitted.
        """
        row = {"at": round(time.time(), 3), "skill_name": spec.name if spec else "",
               "skill_id": spec.skill_id if spec else "", "candidate_id": candidate_id,
               "report_id": report_id, "reasons": list(reasons)}
        self.refusals.append(row)
        if self.path:
            self._append_refusal(row)
        return row

    # ---------------------------------------------------------------- reading ----
    def names(self) -> list[str]:
        return sorted({e.skill_name for e in self.entries})

    def specs(self) -> list[SkillSpec]:
        return [e.spec for e in self.entries]

    def get(self, name: str) -> Optional[SkillSpec]:
        for entry in self.entries:
            if entry.skill_name == name and entry.spec.enabled:
                return entry.spec
        return None

    def applicable(self, effect: str) -> list[SkillSpec]:
        """Every enabled stored skill that claims this effect. Not the best one.

        Matching is on the declared `expected_effects` (the same relation `gap.detect_gaps` uses, so
        "the library already covers this" and "the library has a skill for this subgoal" cannot drift
        apart mid-experiment), and ordering is alphabetical because it must not depend on results.
        """
        return [e.spec for e in sorted(self.entries, key=lambda e: e.skill_name)
                if e.spec.enabled and covers_effect(e.spec, effect)]

    def entry_for(self, name: str) -> Optional[MemoryEntry]:
        return next((e for e in self.entries if e.skill_name == name), None)

    # ---------------------------------------------------------------- the audit ----
    def audit(self) -> dict[str, Any]:
        """What the store can say about itself, for §11's `validation success` and cost rows."""
        admitted = [e for e in self.entries]
        axes = {a for e in admitted for a in (e.admission.get("axes_covered") or [])}
        return {
            "admitted": len(admitted),
            "refused": len(self.refusals),
            "refusal_reasons": [r for row in self.refusals for r in row["reasons"]],
            "names": sorted(e.skill_name for e in admitted),
            "levels": sorted({e.spec.level for e in admitted}),
            "kinds_covered": sorted({k for e in admitted for k in (e.admission.get("kinds_covered")
                                                                   or [])}),
            "axes_covered": sorted(axes),
            "axes_missing": [a for a in UNSEEN_KINDS if a not in axes],
            "auxiliary_kinds": list(AUXILIARY_KINDS),
            "min_instances": sorted({int(e.admission.get("min_instances") or 0) for e in admitted}),
            "validation_wall_s": round(sum(float((e.admission.get("cost") or {}).get("wall_s") or 0.0)
                                           for e in admitted), 3),
            "validation_sim_s": round(sum(float((e.admission.get("cost") or {}).get("sim_s") or 0.0)
                                          for e in admitted), 3),
            "validation_calls": sum(int((e.admission.get("cost") or {}).get("calls") or 0)
                                    for e in admitted),
            "model_calls": sum(int((e.admission.get("cost") or {}).get("model_calls") or 0)
                               for e in admitted),
            "fingerprint": self.path or "in-memory",
        }

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e.as_dict(), sort_keys=True, default=str)
                         for e in self.entries) + ("\n" if self.entries else "")

    # ---------------------------------------------------------------- the file ----
    @property
    def _refusal_path(self) -> str:
        return f"{self.path}.refusals.jsonl" if self.path else ""

    def _append_line(self, entry: MemoryEntry) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path or "")), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.as_dict(), sort_keys=True, default=str) + "\n")

    def _append_refusal(self, row: dict[str, Any]) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self._refusal_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def flush(self) -> str:
        """Write the whole store, in name order, over `path`. A cold-start store has no file."""
        if not self.path:
            raise SkillMemoryError(["this skill memory has no path; nothing to flush"])
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(self.to_jsonl())
        return self.path
