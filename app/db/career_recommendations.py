"""
Career development recommendations.

Rule-based suggestions based on skill category.
"""

from __future__ import annotations


RECOMMENDATIONS = {
    "Security": {
        "training": [
            "Cyber Security Fundamentals",
            "Threat Detection and Response Training",
        ],
        "certifications": [
            "CompTIA Security+",
            "CompTIA CySA+",
        ],
    },

    "Technology": {
        "training": [
            "Cloud Fundamentals",
            "Software Development Practices",
        ],
        "certifications": [
            "AWS Cloud Practitioner",
            "Microsoft Azure Fundamentals",
        ],
    },

    "Operations": {
        "training": [
            "Operations Management Fundamentals",
            "Process Improvement Workshop",
        ],
        "certifications": [
            "Lean Six Sigma Fundamentals",
        ],
    },

    "Analytics": {
        "training": [
            "Data Analysis Fundamentals",
            "Business Intelligence Training",
        ],
        "certifications": [
            "Microsoft Power BI Certification",
        ],
    },

    "Finance": {
        "training": [
            "Financial Analysis Training",
            "Budget Management Workshop",
        ],
        "certifications": [
            "CFA Foundations",
        ],
    },

    "Human Resources": {
        "training": [
            "Talent Management Training",
            "HR Analytics",
        ],
        "certifications": [
            "CIPD Foundation",
            "SHRM Essentials",
        ],
    },
}


_DEFAULT = {
    "training": [
        "Professional Development Program",
    ],
    "certifications": [],
}


def get_skill_recommendations(
    category: str,
) -> dict:
    """
    Return development recommendations.
    """

    return RECOMMENDATIONS.get(
        category,
        _DEFAULT,
    )