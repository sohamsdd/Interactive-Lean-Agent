# LLM-Based Agentic AI for Iterative Reasoning and Proof Verification

> An agentic AI pipeline that combines **OpenAI o4-mini** with **Lean 4 + Mathlib** to automatically generate, verify, and iteratively correct formal mathematical proofs from natural language.

**BITS Pilani, K.K. Birla Goa Campus — Master's Research Project 2026**  
Prem Adhiya · Soham Deshpande · Junaid

---

## What It Does

You type a math theorem in plain English. The system:

1. Extracts assumptions and generates a Lean 4 theorem signature
2. Writes an informal proof sketch
3. Generates a complete Lean 4 tactic proof
4. Compiles it against Mathlib via `lake build`
5. If it fails, classifies the error and corrects — up to 8 times automatically
6. Stores successful strategies in memory for reuse on similar theorems

```
Query: "square root of 2 is irrational"
  → Lean: theorem ... : Irrational (Real.sqrt 2) := irrational_sqrt_two
  → lake build: ✔ Build completed successfully
  → ✓ Proof verified by Lean 4.
```

---

## Results

| Difficulty | Theorems | Verified | Success Rate | Avg. Iterations |
|---|---|---|---|---|
| Basic | 5 | 5 | 100% | 1.0 |
| Medium | 4 | 4 | 100% | 1.0 |
| Medium+ | 4 | 4 | 100% | 1.25 |
| Medium-Hard | 3 | 3 | 100% | 1.67 |
| Advanced | 4 | 3 | 75% | 2.0 |
| **Overall** | **20** | **19** | **95%** | **1.28** |

---

## Project Structure

```
lean_proof_agent/
├── main.py               # CLI entry point
├── requirements.txt      # Python dependencies
├── core/
│   ├── agent.py          # ProofAssistantAgent — orchestrates all stages
│   ├── prompts.py        # LLM system prompts + Mathlib4 lemma lookup table
│   └── memory.py         # Strategy memory — persists successful proofs to JSON
└── lean/
    └── repl.py           # Lean interface — lake build, error classification
```

---

## Setup

### Prerequisites

- Python 3.10+
- OpenAI API key
- Lean 4 + Mathlib (for real verification)

### 1. Clone and install

```bash
git clone https://github.com/your-username/lean-proof-agent.git
cd lean-proof-agent
pip install openai
```

### 2. Add your API key

Open `main.py` and set:

```python
os.environ["OPENAI_API_KEY"] = "sk-your-key-here"
```

### 3. Install Lean 4 (Ubuntu / WSL)

```bash
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh
source ~/.profile
lean --version
```

### 4. Create a Mathlib project

```bash
cd ~
lake new my_proofs math
cd my_proofs
lake exe cache get      # downloads precompiled Mathlib (~10 min, do once)
```

Configure `lakefile.toml` — make sure it has:

```toml
[[lean_lib]]
name = "ProofCheck"
```

---

## Usage

```bash
cd lean_proof_agent

# Real Lean + Mathlib verification
python3 main.py --lake-project ~/my_proofs --query "square root of 2 is irrational"

# Test pipeline without Lean installed
python3 main.py --mock --query "for all n, n + 0 = n"

# Interactive mode (prompts for input)
python3 main.py --lake-project ~/my_proofs

# Check what strategies are stored in memory
python3 main.py --memory-stats
```

---

## Example Outputs

**Basic arithmetic** (0 retries):
```
Query: n * 1 = n
Lean: theorem ... := by ring
✓ Proof verified by Lean 4.   Retries: 0
```

**Distributivity** (1 retry — ring fails in non-commutative Semiring):
```
Query: n*(m+k) + n*p = n*(m+k+p)
Attempt 1: by ring  →  Error: ring_nf made no progress
Attempt 2: by simp [left_distrib]  →  ✓ Verified.   Retries: 1
```

**Irrationality** (1 retry — timeout on first attempt):
```
Query: square root of 2 is irrational
Attempt 2: exact irrational_sqrt_two  →  ✓ Verified.   Retries: 1
```

---

## Key Architecture Features

**7-category error classifier** — each error type triggers a targeted correction prompt:

| Error Type | Example | Fix |
|---|---|---|
| `AMBIGUOUS_TERM` | bare `Prime` | Use `Nat.Prime` explicitly |
| `NAME_COLLISION` | theorem name exists in Mathlib | Use `exact <mathlib_lemma>` |
| `UNKNOWN_IDENT` | hallucinated lemma name | Lookup table + tactic fallback |
| `TYPE_MISMATCH` | ℕ/ℤ/ℝ coercion | `norm_cast`, `push_cast` |
| `UNSOLVED_GOALS` | incomplete proof | `omega` / `linarith` / `simp` |
| `TACTIC_FAILED` | `ring` in non-commutative context | `simp [left_distrib]` |
| `SYNTAX_ERROR` | stray `import` in proof body | Strip and retry |

**Strategy memory** — `proof_memory.json` stores successful proofs by theorem category (algebraic_identity, irrationality, divisibility, etc.) and injects them as worked examples for future similar theorems.

**Session failure tracking** — the last 4 failed proof attempts are injected into each correction prompt to prevent the agent from cycling through the same wrong attempts.

---

## Theorems Successfully Verified

| Category | Examples |
|---|---|
| Basic arithmetic | `n+0=n`, `n*1=n`, `n*0=0` |
| Algebraic identities | `(a+b)²=a²+2ab+b²`, `n²+2n+1=(n+1)²` |
| Commutativity / Associativity | `n+m=m+n`, `(n*m)*k=n*(m*k)` |
| Distributivity | `n*(m+k+p)=nm+nk+np` |
| Irrationality | `√2 irrational`, `√3 irrational` |
| Primality / Divisibility | `p prime, p∣ab → p∣a or p∣b` |

---

## Limitations

- Relies on a static Mathlib lemma lookup table — novel theorems outside covered categories may require user hints
- No runtime Mathlib search (retrieval-based lemma lookup is a planned extension)
- Complex multi-step proofs (5+ coordinated steps) may exhaust retries and escalate to user input
- Tested on Lean 4 v4.30.0-rc2 / Mathlib v4.30.0-rc2

---

## Reference Paper

This project is inspired by:

> **DeepSeek-Prover-V2: Advancing Formal Mathematical Reasoning via Reinforcement Learning for Subgoal Decomposition**  
> arXiv:2504.21801 — https://arxiv.org/abs/2504.21801

---

