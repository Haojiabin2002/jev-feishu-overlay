# Excerpt from tools/jev/questions.py in open-aiee/jev-chat-JARVIS.
# Git commit: 37753bef127b6cd16983b401a6701033a58c585d
# File blob SHA: 61af19acc627deb6c5d20b01196208a135f3639a
# Copyright (c) 2026 Jev 聊天助手 contributors. MIT License; see adjacent LICENSE.
# Reference only. Not imported by the application.

def build_rank_question(candidates: list[str]) -> dict:
    """Build the best_reply choice question. criteria values stay in original Chinese."""
    if len(candidates) != 3:
        raise ValueError("build_rank_question expects exactly 3 candidate replies")
    keys = ("reply_a", "reply_b", "reply_c")
    return {
        "best_reply": {
            "type": "choice",
            "instructions": (
                "Which candidate reply is the most appropriate next message, "
                "given the conversation and the other person's true need? "
                "Prefer a reply that matches the best action type. "
                "Penalize dismissive, over-promising, or off-topic replies. "
                "If the facts are not yet confirmed, prefer the candidate that looks them up "
                "instead of faking memory or a vague apology."
            ),
            "criteria": {key: text for key, text in zip(keys, candidates)},
        }
    }
