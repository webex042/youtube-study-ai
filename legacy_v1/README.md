# Legacy v1 (kept for reference)

This folder is a backup of the project as it was before it was rebuilt as a
three-panel notebook (Sources · Chat · Studio). Nothing in here is imported or
served by the current app.

It contains:

- the previous single-page "AI Study Lab" interface (`templates/`, `static/`)
  and its `/api/study` pipeline (`app/routes/study.py`,
  `app/services/study_generator.py`, `app/services/flowchart_generator.py`,
  `app/models/schemas.py`), which were moved here unchanged;
- the original versions of files that the rebuild edited in place
  (`app/main.py`, `app/config.py`, `app/security/middleware.py`,
  `app/security/ssrf.py`, `.env.example`, `.gitignore`, `requirements.txt`),
  copied here before they were changed;
- the original README, saved as `README_v1.md`.

You can delete this folder once you no longer need it.
