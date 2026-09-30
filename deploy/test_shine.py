import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraper_service import scrape_shine

jobs = scrape_shine("mobile developer", count=5)
assert jobs, "shine returned nothing"
assert all(j["source"] == "shine" and j["title"] for j in jobs), jobs
assert all(j["url"].startswith("https://www.shine.com/jobs/") for j in jobs), jobs
assert any(j["experience"] for j in jobs), "jExp not mapped"
assert any(len(j["description"]) > 100 for j in jobs), "jJD not mapped"
print(f"shine ok: {len(jobs)} jobs; e.g. {jobs[0]['title']} @ "
      f"{jobs[0]['company']} [{jobs[0]['experience']}] {jobs[0]['location']}")
