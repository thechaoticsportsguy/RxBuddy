"""
Pipeline Step 10 — Failsafe System.

When anything goes wrong (APIs fail, drug extraction fails, logic is
uncertain), the system returns a safe CONSULT_PHARMACIST response
instead of crashing or hallucinating.

Also handles emergency detection — routes overdose/breathing/chest-pain
queries to emergency services immediately.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("rxbuddy.pipeline.failsafe")


def build_failsafe_response(
    query: str,
    error: str = "",
) -> dict:
    """
    Build a safe CONSULT_PHARMACIST response for any failure scenario.

    This is the response of last resort. It NEVER hallucinates, NEVER
    provides specific medical advice, and ALWAYS directs to a pharmacist.

    Returns a dict matching the SearchResponse/StructuredAnswer API contract.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"

    if error:
        logger.warning("[Failsafe] Triggered: %s", error)

    return {
        "query": query,
        "results": [{
            "id": 0,
            "question": query,
            "category": "General",
            "tags": [],
            "score": 0.0,
            "answer": "We could not verify this safely with available data. Please consult a pharmacist.",
            "structured": {
                "verdict": "CONSULT_PHARMACIST",
                "answer": "Unable to verify this safely with available data.",
                "short_answer": "Unable to verify this safely with available data.",
                "warning": "Professional guidance recommended.",
                "details": [
                    "Data could not be verified from official sources",
                    "A pharmacist can provide accurate guidance",
                ],
                "action": [
                    "Consult a licensed pharmacist",
                    "Check DailyMed at dailymed.nlm.nih.gov",
                ],
                "article": "RxBuddy could not retrieve sufficient data to answer this question safely. A licensed pharmacist has access to complete drug information and can provide accurate guidance.",
                "direct": "Unable to verify this safely with available data.",
                "do": ["Consult a licensed pharmacist", "Check DailyMed at dailymed.nlm.nih.gov"],
                "avoid": [],
                "doctor": ["Speak with your pharmacist or prescriber"],
                "raw": "",
                "confidence": "LOW",
                "sources": "Pharmacist consultation recommended",
                "interaction_summary": {"avoid_pairs": [], "caution_pairs": []},
                "citations": [],
                "intent": "general",
                "retrieval_status": "LABEL_NOT_FOUND",
            },
        }],
        "did_you_mean": None,
        "source": "failsafe",
        "saved_to_db": False,
    }


def build_emergency_response(query: str) -> dict:
    """
    Build an EMERGENCY response that directs the user to call 911/Poison Control.

    This is returned when emergency keywords are detected (overdose, can't
    breathe, chest pain, seizure, etc.). NO medical advice is given.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"

    logger.warning("[Failsafe] EMERGENCY detected: %.80s", query)

    return {
        "query": query,
        "results": [{
            "id": 0,
            "question": query,
            "category": "General",
            "tags": [],
            "score": 1.0,
            "answer": "This is a medical emergency. Call 911 or Poison Control (1-800-222-1222) immediately.",
            "structured": {
                "verdict": "EMERGENCY",
                "answer": "This is a medical emergency. Call 911 or Poison Control (1-800-222-1222) immediately.",
                "short_answer": "This is a medical emergency. Call 911 immediately.",
                "warning": "This is a medical emergency. Do not wait.",
                "details": [
                    "Immediate action is required",
                    "Do not induce vomiting unless directed by poison control",
                    "Stay on the line with emergency services",
                ],
                "action": [
                    "Call 911 immediately",
                    "Call Poison Control: 1-800-222-1222",
                    "Do not wait for symptoms to worsen",
                ],
                "article": "If you or someone else is experiencing a medical emergency related to medication, call 911 or Poison Control (1-800-222-1222) immediately. Do not wait for symptoms to worsen.",
                "direct": "This is a medical emergency. Call 911 or Poison Control (1-800-222-1222) immediately.",
                "do": ["Call 911 immediately", "Call Poison Control: 1-800-222-1222"],
                "avoid": ["Do not wait for symptoms to worsen"],
                "doctor": ["Call 911 immediately"],
                "raw": "",
                "confidence": "HIGH",
                "sources": "Emergency Services",
                "interaction_summary": {"avoid_pairs": [], "caution_pairs": []},
                "citations": [],
                "intent": "general",
                "retrieval_status": "REFUSED_NO_SOURCE",
            },
        }],
        "did_you_mean": None,
        "source": "emergency",
        "saved_to_db": False,
    }
