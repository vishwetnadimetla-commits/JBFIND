from fastapi import FastAPI, Query
from jobspy import scrape_jobs
import json

app = FastAPI(title="JBFind Scraper")

@app.get("/scrape")
def scrape(
    q: str = Query(..., min_length=1),
    location: str = Query("Bangalore, India"),
    sites: str = Query("indeed,linkedin,naukri,google"),
    count: int = Query(50, le=200),
):
    site_list = [s.strip() for s in sites.split(",")]
    jobs = scrape_jobs(
        site_name=site_list,
        search_term=q,
        google_search_term=f"{q} jobs near {location}",
        location=location,
        results_wanted=count,
        hours_old=72,
        country_indeed="India",
        verbose=0,
    )
    result = []
    for _, job in jobs.iterrows():
        result.append({
            "source": job.get("site", ""),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "company_url": job.get("company_url", ""),
            "location": f"{job.get('city', '')}, {job.get('state', '')}".strip(", "),
            "country": job.get("country", ""),
            "is_remote": bool(job.get("is_remote")),
            "description": (job.get("description") or "")[:3000],
            "url": job.get("job_url", ""),
            "date_posted": str(job.get("date_posted", "")),
            "job_type": job.get("job_type", ""),
            "salary_min": job.get("min_amount", 0),
            "salary_max": job.get("max_amount", 0),
            "salary_currency": job.get("currency", ""),
            "experience": job.get("experience", ""),
        })
    return result


@app.get("/health")
def health():
    return {"status": "ok"}