# Supporting-module regression checks

Run from the repository root after installing requirements:

```powershell
python -m unittest discover -s eval -p "test_*.py" -v
```

These offline cases mock model responses and do not query or update the database.
They cover invalid leave dates, inclusive date-based duration, submission wording,
Arabic/Persian digits, lost/duplicated translation placeholders, altered numbers,
CV injection filtering, PII masking, refused/incomplete plans, and Arabic PDF output.

Payroll uses installed Arial on Windows or DejaVu Sans on Linux, with HarfBuzz
shaping. Otherwise set `PAYROLL_FONT_REGULAR` and `PAYROLL_FONT_BOLD` in the process
environment to Arabic-capable TrueType fonts. Do not commit proprietary font files.

CV filtering and pattern-based PII masking reduce exposure but do not detect every
possible instruction or unlabeled personal detail. Refused or malformed model
responses raise before the caller saves a plan. API cases verify that the same
employee-aware sanitized CV text is used for generation and persistence, failures
return actionable errors without saving, and instruction-only CVs are rejected.
Translation falls back to the
digit-normalised original if placeholders or numbers fail validation.

Live model quality and a visual review of Arabic PDF layout are separate checks.
