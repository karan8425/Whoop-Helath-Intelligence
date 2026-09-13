"""Program Intelligence V2 Phase 7 - explainability.

Turns the decision's structured reason codes into a human-readable
paragraph. No medical/physiological certainty language; every claim is
traceable to a specific reason code."""
from __future__ import annotations

EXPLANATION_VERSION = 1


def _muscle_state_phrase(reason_codes):
    fatigued, recovering, ready = [], [], []
    for code in reason_codes:
        if code.endswith("_FATIGUED") or code.endswith("_SUPPRESSED"):
            fatigued.append(code.rsplit("_", 1)[0].replace("_", " ").title())
        elif code.endswith("_RECOVERING"):
            recovering.append(code.rsplit("_", 1)[0].replace("_", " ").title())
        elif code.endswith("_READY") or code.endswith("_FRESH"):
            ready.append(code.rsplit("_", 1)[0].replace("_", " ").title())
    return fatigued, recovering, ready


def build_explanation(action, nominal_session, chosen_session, reason_codes, program_progress_summary=None,
                       systemic_band=None, time_context=None):
    """Returns {"reason_codes": [...], "summary": "..."} - a structured
    list plus one human-readable paragraph, matching the example shape
    in the milestone spec exactly."""
    fatigued, recovering, ready = _muscle_state_phrase(reason_codes)
    nominal_name = nominal_session["session_name"] if nominal_session else "No nominal session"
    chosen_name = chosen_session["session"]["session_name"] if chosen_session else None

    parts = []
    if nominal_session:
        parts.append(f"{nominal_name} was next in sequence")
    if fatigued:
        parts.append(f"but {', '.join(fatigued)} remain{'s' if len(fatigued) == 1 else ''} fatigued or suppressed")
    elif recovering:
        parts.append(f"while {', '.join(recovering)} remain{'s' if len(recovering) == 1 else ''} recovering")
    if ready and chosen_name and chosen_name != nominal_name:
        parts.append(f"and the {chosen_name} muscle groups are ready")
    if chosen_name and chosen_name != nominal_name:
        parts.append(f"{chosen_name} is also outstanding in the current program cycle, so advancing it today "
                      f"preserves program intent with minimal schedule disruption")
    elif action == "REDUCE":
        parts.append(f"systemic recovery ({systemic_band}) supports training but not the full prescribed dose, "
                      f"so today's session is reduced rather than swapped")
    elif action in ("RECOVERY", "REST"):
        parts.append("systemic recovery and/or local readiness do not support a meaningful training session today")
    elif action == "SUBSTITUTE":
        parts.append("the affected muscle group's slot is substituted toward a lighter resolution while the "
                      "rest of the session proceeds as scheduled")
    elif chosen_name:
        parts.append(f"{chosen_name} proceeds as scheduled")

    if time_context and time_context.get("fit_status") == "EXCEEDS":
        parts.append("available time was shorter than the full session, so optional/accessory work was reduced first")

    summary = ". ".join(p[0].upper() + p[1:] for p in parts if p) + "."
    return {"explanation_version": EXPLANATION_VERSION, "reason_codes": list(reason_codes), "summary": summary}
