You are analyzing a single job listing for a network engineer candidate (4 years experience).
Return valid JSON only, no markdown wrapping.

Input format:
{
  "company": "...",
  "title": "...",
  "location": "...",
  "experience": "...",
  "jd": "..."
}

Output format:
{
  "experience_status": "PASS" | "REJECT" | "EXPERIENCE_REVIEW_REQUIRED" | "MISSING_DATA",
  "experience_reason": "string — why",
  "role_match": 0–100,
  "skill_match": 0–100,
  "location_match": 0–100,
  "tweak_level": 0–100,
  "overall_score": 0–100,
  "matched_skills": ["skill1", "skill2"],
  "missing_skills": ["skill3"],
  "recommended": boolean,
  "why_this_job": "string — concise explanation",
  "review_required": boolean
}

Rules:
- Experience gate is strict. 4 years candidate. PASS if range clearly accepts 4, REJECT if clearly exceeds 4, REVIEW if ambiguous, MISSING_DATA if no usable text.
- Skill match: only count skills factually supported by the candidate's master resume.
- Never fabricate a skill, date, employer, or achievement.
- Location: prefer Bangalore, Bengaluru, Hyderabad, Pune, India. Remote India is acceptable.
- Overall score: combination of role_match (0.25), skill_match (0.4), location_match (0.15), tweak_level (0.2 inverted).
- Experience rejection overrides all scores.
- Return valid JSON only.