"""Lean 4 verifier — lake mode for Mathlib projects."""

import subprocess
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum


class ErrorType(Enum):
    NAME_COLLISION = "name_collision"
    UNKNOWN_IDENT  = "unknown_identifier"
    TYPE_MISMATCH  = "type_mismatch"
    UNSOLVED_GOALS = "unsolved_goals"
    TACTIC_FAILED  = "tactic_failed"
    SYNTAX_ERROR   = "syntax_error"
    AMBIGUOUS_TERM = "ambiguous_term"   # NEW: open Nat/Int causes Prime/Dvd ambiguity
    TIMEOUT        = "timeout"
    UNKNOWN        = "unknown"


@dataclass
class LeanResult:
    success: bool
    error: str = ""
    error_type: ErrorType = ErrorType.UNKNOWN
    error_detail: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    raw_output: str = ""


class LeanREPL:
    def __init__(self, mode="lake", project_dir=None, lean_binary="lean", timeout=120):
        self.mode = mode
        self.project_dir = project_dir
        self.lean_binary = lean_binary
        self.timeout = timeout
        # NOTE: We do NOT open Nat or Int here anymore to avoid Prime/dvd ambiguity.
        # Proofs should use fully-qualified names: Nat.Prime, Int.dvd, etc.
        self._header = "import Mathlib\nopen Real\n\n"

    def check(self, lean_code: str) -> LeanResult:
        lean_code = self._strip_imports(lean_code)
        lean_code = self._uniquify_theorem_name(lean_code)
        if self.mode == "mock":
            return self._mock_check(lean_code)
        elif self.mode == "lake":
            return self._check_lake(lean_code)
        else:
            return self._check_lean_binary(lean_code)

    def _strip_imports(self, lean_code: str) -> str:
        """
        Remove any 'import' or 'open' lines the model puts in the proof body.
        The header already handles all imports and opens.
        """
        lines = lean_code.splitlines()
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import "):
                continue
            if stripped.startswith("open "):
                continue
            cleaned.append(line)
        return "\n".join(cleaned).strip()

    def _uniquify_theorem_name(self, lean_code: str) -> str:
        """Replace theorem/lemma name with unique timestamped name to avoid Mathlib collisions."""
        unique_name = f"proof_check_{int(time.time() * 1000) % 1_000_000}"
        lean_code = re.sub(
            r'\b(theorem|lemma)\s+\w+',
            lambda m: f"{m.group(1)} {unique_name}",
            lean_code,
            count=1
        )
        return lean_code

    def _check_lake(self, lean_code: str) -> LeanResult:
        if not self.project_dir:
            raise ValueError("project_dir required for lake mode")

        proof_file = os.path.join(self.project_dir, "ProofCheck.lean")
        full_code = self._header + lean_code

        with open(proof_file, "w") as f:
            f.write(full_code)

        print(f"\n[LEAN CODE]:\n{full_code}\n")

        try:
            result = subprocess.run(
                ["lake", "build", "ProofCheck"],
                capture_output=True, text=True,
                timeout=self.timeout, cwd=self.project_dir,
            )
            combined = result.stdout + result.stderr
            print(f"[LEAN RAW OUTPUT]:\n{combined}\n")
            return self._parse_output(combined, result.returncode)
        except subprocess.TimeoutExpired:
            return LeanResult(success=False, error="Lean timed out.", error_type=ErrorType.TIMEOUT)

    def _check_lean_binary(self, lean_code: str) -> LeanResult:
        import tempfile
        full_code = self._header + lean_code
        with tempfile.NamedTemporaryFile(suffix=".lean", mode="w", delete=False) as f:
            f.write(full_code)
            tmp = f.name
        try:
            result = subprocess.run(
                [self.lean_binary, tmp],
                capture_output=True, text=True, timeout=self.timeout
            )
            combined = result.stdout + result.stderr
            print(f"[LEAN RAW OUTPUT]:\n{combined}\n")
            return self._parse_output(combined, result.returncode)
        except FileNotFoundError:
            return LeanResult(success=False, error=f"lean binary not found: {self.lean_binary}",
                              error_type=ErrorType.UNKNOWN)
        finally:
            os.unlink(tmp)

    def _parse_output(self, output: str, returncode: int) -> LeanResult:
        error_lines = [l.strip() for l in output.splitlines() if "error:" in l.lower()]
        warnings    = [l.strip() for l in output.splitlines() if "warning:" in l.lower()]
        has_errors  = len(error_lines) > 0 or returncode != 0

        if not has_errors:
            return LeanResult(success=True, warnings=warnings, raw_output=output)

        error_text = "\n".join(error_lines)
        error_type, error_detail = self._classify_error(error_text, output)

        return LeanResult(
            success=False,
            error=error_text,
            error_type=error_type,
            error_detail=error_detail,
            warnings=warnings,
            raw_output=output,
        )

    def _classify_error(self, error_text: str, full_output: str) -> tuple[ErrorType, dict]:
        text = error_text.lower()

        # Ambiguous term (caused by open Nat/Int bringing duplicate names into scope)
        if "ambiguous term" in text or "ambiguous" in text:
            match = re.search(r'Ambiguous term\s+(\S+)', error_text)
            term = match.group(1) if match else "unknown"
            return ErrorType.AMBIGUOUS_TERM, {
                "ambiguous_term": term,
                "suggestion": (
                    f"'{term}' is ambiguous. Use fully-qualified names: "
                    f"Nat.Prime instead of Prime, _root_.Prime for the general one, "
                    f"Int.dvd instead of dvd, etc."
                ),
            }

        # Name collision
        if "has already been declared" in text:
            match = re.search(r'`([^`]+)`\s+has already been declared', error_text)
            name = match.group(1) if match else "unknown"
            return ErrorType.NAME_COLLISION, {
                "colliding_name": name,
                "suggestion": f"'{name}' exists in Mathlib — use 'exact {name}' directly.",
            }

        # Rogue import inside proof body
        if "invalid 'import' command" in text or "invalid import" in text:
            return ErrorType.SYNTAX_ERROR, {
                "suggestion": "NEVER include 'import' or 'open' lines in the proof. Start with 'theorem ...'.",
            }

        # Unknown identifier / constant
        if "unknown identifier" in text or "unknown constant" in text:
            match = re.search(r"unknown (?:identifier|constant) [`']([^'`]+)[`']", error_text)
            name = match.group(1) if match else "unknown"
            return ErrorType.UNKNOWN_IDENT, {
                "unknown_name": name,
                "suggestion": f"'{name}' does not exist in Mathlib 4. Use the verified lemma lookup table.",
            }

        # Type mismatch
        if "type mismatch" in text or "application type mismatch" in text:
            return ErrorType.TYPE_MISMATCH, {
                "suggestion": "Use norm_cast, exact_mod_cast, or push_cast to fix type coercions.",
                "raw": error_text[:300],
            }

        # Unsolved goals
        if "unsolved goals" in text:
            goal_match = re.search(r'unsolved goals\n(.*?)(?:\nerror:|$)', full_output, re.DOTALL)
            goal_state = goal_match.group(1).strip()[:300] if goal_match else ""
            return ErrorType.UNSOLVED_GOALS, {
                "remaining_goal": goal_state,
                "suggestion": "Proof incomplete. Add more tactic steps.",
            }

        # ring_nf no progress
        if "ring_nf" in text and "no progress" in text:
            return ErrorType.TACTIC_FAILED, {
                "failed_tactic": "ring",
                "suggestion": "ring failed in non-commutative context. Try simp [left_distrib, right_distrib].",
            }

        # Other tactic failures
        tactic_match = re.search(
            r'(ring|simp|norm_num|omega|linarith|nlinarith|decide|aesop)\s+(?:failed|did not|made no progress)',
            text
        )
        if tactic_match:
            return ErrorType.TACTIC_FAILED, {
                "failed_tactic": tactic_match.group(1),
                "suggestion": f"'{tactic_match.group(1)}' failed. Try an alternative tactic.",
            }

        # Syntax errors
        if "expected" in text or "parse error" in text or "syntax" in text:
            return ErrorType.SYNTAX_ERROR, {
                "suggestion": "Fix Lean 4 syntax: brackets, keywords, indentation.",
                "raw": error_text[:300],
            }

        return ErrorType.UNKNOWN, {"raw": error_text[:300]}

    def _mock_check(self, lean_code: str) -> LeanResult:
        if "irrational_sqrt_two" in lean_code and "exact irrational_sqrt_two" in lean_code:
            return LeanResult(success=True, raw_output="mock: ok")
        if re.search(r'\bsorry\b', lean_code):
            return LeanResult(success=True, warnings=["warning: uses sorry"], raw_output="mock: sorry")
        return LeanResult(
            success=False,
            error="error: unsolved goals",
            error_type=ErrorType.UNSOLVED_GOALS,
            error_detail={"suggestion": "Mock: proof incomplete."},
            raw_output="mock: failed"
        )