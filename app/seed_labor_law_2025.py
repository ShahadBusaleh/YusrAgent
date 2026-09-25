"""
Add the 2025 contract-termination / end-of-service labor-law articles.

IMPORTANT:
- Adds 11 new rows (LAW071-LAW081) to `saudi_labor_law`, matching the
  files of the same name under `policy_texts/saudi_labor_law/`.
- Source: the HRSD "work relations" page, with the Labor Law amendments
  in force from 18-02-2025 (Articles 53 and 75). Summarised in English
  for the demo; not an official translation and not legal advice.
- Existing articles (LAW001-LAW070) are never edited or deleted.
- Idempotent: INSERT OR IGNORE on the primary key, so re-running only
  adds rows that are still missing.
- Re-index these files in Qdrant separately with the existing
  `app/rag/ingest.py` helpers once QDRANT_URL/QDRANT_API_KEY are set.
"""

from __future__ import annotations

from app.db.connection import get_connection


# (id, category, article, title, rule, conditions, exceptions)
LABOR_LAW_2025 = [
    (
        'LAW071',
        'Contract Termination',
        '74',
        'Cases Ending the Employment Contract',
        'The employment contract ends by: (1) agreement of both parties, provided the worker consents in writing; (2) expiry of the contract term, unless it was expressly or implicitly renewed; (3) the wish of either party in an indefinite-term contract, subject to Article 75; (4) the worker reaching retirement age; (5) force majeure; (6) permanent closure of the establishment; (7) termination of the activity in which the worker is employed.',
        'Applies to all employment contracts governed by the Labor Law.',
        "Termination by agreement is valid only with the worker's written consent.",
    ),
    (
        'LAW072',
        'Contract Termination',
        '75',
        'Notice Period for Indefinite-Term Contracts (amended 2025)',
        'When an indefinite-term contract is terminated by either party for a legitimate reason, written notice is required. For a worker paid monthly, the notice period is at least 30 days if the worker terminates and at least 60 days if the employer terminates. For other workers, the notice period is at least 30 days for both parties.',
        'Applies to indefinite-term contracts; amended provisions effective 18-02-2025.',
        'Does not apply to fixed-term contracts, which end on expiry under Article 74.',
    ),
    (
        'LAW073',
        'Contract Termination',
        '76',
        'Compensation for Not Observing Notice',
        "A party that does not observe the notice period must pay the other party compensation equal to the worker's wage for the notice period or the remainder of it.",
        'Applies when an indefinite-term contract is ended without the full notice required by Article 75.',
        'The parties may agree otherwise.',
    ),
    (
        'LAW074',
        'Contract Termination',
        '77',
        'Compensation for Termination Without a Legitimate Reason',
        "If a contract is terminated for an illegitimate reason, the injured party is entitled to compensation: 15 days' wage for each year of service in an indefinite-term contract, or the wage for the remaining term in a fixed-term contract. The compensation may not be less than two months' wage.",
        'Applies when the contract does not specify a compensation amount for illegitimate termination.',
        'A contractually agreed compensation amount applies if specified.',
    ),
    (
        'LAW075',
        'Contract Termination',
        '80',
        'Dismissal Without End-of-Service Award, Notice or Compensation',
        "The employer may terminate the contract without end-of-service award, notice or compensation if the worker: (1) assaults the employer, the manager or a superior during or because of work; (2) fails to perform essential contractual obligations or obey legitimate orders, or deliberately disregards written safety instructions, despite written warning; (3) commits misconduct or an act breaching honor or integrity; (4) deliberately causes material loss to the employer, provided the incident is reported to the authorities within 24 hours of learning of it; (5) obtained the job through forgery; (6) is still in the probation period; (7) is absent without legitimate reason for more than 30 non-consecutive days or more than 15 consecutive days in one contractual year; (8) unlawfully exploits their position for personal gain; (9) discloses the employer's industrial or commercial secrets.",
        'The employer must give the worker an opportunity to state the reasons for their objection to the termination.',
        'For absence, dismissal must be preceded by a written warning as required by the law.',
    ),
    (
        'LAW076',
        'Contract Termination',
        '81',
        'Worker Leaving Without Notice With Full Rights',
        "The worker may leave work without notice while retaining all statutory rights in the cases set out in Article 81, including the employer's failure to fulfil essential contractual or statutory obligations, fraud by the employer regarding working conditions at contracting, assault or humiliating treatment by the employer or their representative, or a serious danger to the worker's safety or health known to the employer.",
        'Applies only to the cases listed in Article 81.',
        'Rights retained include the end-of-service award and any compensation due.',
    ),
    (
        'LAW077',
        'End of Service',
        '84',
        'End-of-Service Award',
        "When the employment relationship ends, the employer must pay the worker an end-of-service award equal to half a month's wage for each of the first five years of service and one month's wage for each year after that. The award is calculated on the last wage received. The worker is entitled to a proportional award for parts of a year.",
        'Applies when the employment relationship ends, subject to Articles 80, 85 and 87.',
        'Not due where Article 80 dismissal applies; reduced on resignation under Article 85.',
    ),
    (
        'LAW078',
        'End of Service',
        '85',
        'End-of-Service Award on Resignation',
        "If the contract ends by the worker's resignation, the worker is entitled to: no award for service of less than two consecutive years; one third of the award for service of two to less than five years; two thirds of the award for service of five to less than ten years; and the full award for ten years or more.",
        'Applies when the worker resigns.',
        'Article 87 cases grant the full award regardless of service length.',
    ),
    (
        'LAW079',
        'End of Service',
        '87',
        'Full End-of-Service Award Cases',
        'Notwithstanding Article 85, the worker is entitled to the full end-of-service award if they leave work due to force majeure beyond their control. A female worker is also entitled to the full award if she terminates the contract within six months of marriage or within three months of giving birth.',
        'Applies to the force majeure, marriage and childbirth cases stated in the article.',
        'Overrides the resignation reductions in Article 85.',
    ),
    (
        'LAW080',
        'End of Service',
        '88',
        'Settlement of Entitlements at End of Service',
        "When the employment relationship ends, the employer must pay the worker's wages and entitlements within a maximum of one week from the end of the relationship. If the worker terminated the contract, the employer must settle all entitlements within two weeks.",
        'Applies to all contract endings.',
        'The employer may deduct any debt owed by the worker from the entitlements within the legal limits.',
    ),
    (
        'LAW081',
        'Probation',
        '53',
        'Probation Period (amended 2025)',
        'A worker may be placed on probation if this is clearly stated in the employment contract. The probation period may be extended by written agreement between the parties, provided the total probation period does not exceed 180 days.',
        'Applies to contracts that expressly include a probation period; amended provisions effective 18-02-2025.',
        'Eid al-Fitr and Eid al-Adha holidays and sick leave are not counted in the probation period.',
    ),
]


def seed_labor_law_2025(conn) -> int:
    """Insert the new articles that are not already present."""

    added = 0
    for row in LABOR_LAW_2025:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO saudi_labor_law (
                id,
                category,
                article,
                title,
                rule,
                conditions,
                exceptions
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        added += cur.rowcount
    return added


def main() -> None:
    conn = get_connection()

    try:
        added = seed_labor_law_2025(conn)
        conn.commit()

        print("2025 labor-law articles seed completed.")
        print(f" - {added} new row(s) added to saudi_labor_law")
        print(f" - {len(LABOR_LAW_2025) - added} row(s) already present")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
