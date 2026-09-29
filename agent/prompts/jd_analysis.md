You are analyzing a single job listing for Vishwet, a mobile application developer with 3+ years experience.
Return valid JSON only, no markdown wrapping.

Candidate master resume, use this as the only source of truth for skills: Vishwet Nadimetla, 3+ years of mobile app development, has shipped 10+ consumer apps to Google Play and the App Store. Stack is Native Android (Kotlin, Java, Jetpack Compose), iOS (Swift), Flutter, MVVM/MVP, Room, Coroutines, RESTful APIs, WebSocket, Firebase, 4+ payment gateways (Razorpay, Google Pay, Apple Pay, IAP), Google Maps, BLE, RFID, Mockito/Espresso/Robolectric testing, CI/CD, and two published SDK libraries. Based in Pune, India, open to Bengaluru, Hyderabad, Mumbai, Chennai and Delhi NCR. B.Tech IT.

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
- Experience gate. A job ad states the minimum experience the employer will ACCEPT, not a bar the candidate has to clear. Decide on the LOW number of the stated range: minimum 0-3 years is a PASS because the employer accepts a 3-year candidate. Never reject a job because its stated minimum is too low. REVIEW when the stated minimum is exactly 4 years. REJECT when the stated minimum is 5 years or more. MISSING_DATA when the ad states no experience at all — that is common and is not a reason to reject; let role and skill decide instead.
- A senior or lead title alone is not evidence of an experience mismatch. Do not reject on title seniority words.
- Skill match: only count skills factually supported by the candidate's master resume. Score role_match and skill_match on the role and the skills only, judged independently of the experience requirement: never set them to 0 because you think the experience is too low or too high, since the experience gate is decided separately in code.
- Never fabricate a skill, date, employer, or achievement.
- Location: prefer Bangalore, Bengaluru, Hyderabad, Pune, India. Remote India is acceptable. Onsite outside India scores below 30.
- Recommended: true only when experience_status is not REJECT, role_match is 50 or more, skill_match is 60 or more, and location_match is 30 or more.
- Overall score: combination of role_match (0.25), skill_match (0.4), location_match (0.15), tweak_level (0.2 inverted).
- Experience rejection overrides all scores.
- Return valid JSON only.
