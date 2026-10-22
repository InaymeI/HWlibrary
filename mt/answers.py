import random

def extract_right_answers(test: dict) -> list[dict]:
    arr = test.get("educationTestAnswers") or test.get("educationTestRightAnswers") or []
    return sorted(
        [a for a in arr if a.get("isRightAnswer") is True],
        key=lambda a: a.get("sort") or 0,
    )

def build_answer(test: dict) -> list:
    right = extract_right_answers(test)
    t = test.get("type") or ""
    if t == "type5":
        return [random.choice(a["variants"]) if a.get("variants") else "" for a in right]
    if t in ("type3", "type4"):
        return [(a.get("variants") or [""])[0] for a in right]
    return [str(a.get("id")) for a in right]
