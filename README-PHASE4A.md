# WHOOP Health Intelligence — Phase 4A

Phase 4A adds a deterministic daily recommendation engine.

Outputs:
- overall status
- training recommendation:
  - Push
  - Normal
  - Moderate
  - Active Recovery
  - Rest
- key reasons
- recovery priorities
- one or two highest-impact actions
- confidence
- safety note

Important design rule:
The engine uses the validated Phase 3C statistical signals. It does not use an LLM and does not diagnose medical conditions.

Training-context metrics such as cycle strain and workout count do not independently become "good" or "bad". They modify context around the recovery and sleep signals.

Deployment:
1. Upload `recommendations.py`.
2. Replace `main.py`.
3. Upload this README if desired.
4. Commit `Add Phase 4A daily recommendation engine`.
5. Confirm `/health` = phase 4A, version 0.4.0.
6. Open `Validate recommendation engine`.
7. Send the complete JSON result before moving to Phase 4B / LLM intelligence.
