// Run: node scripts/test_normalize.mjs
// Pulls the live Normalize + Dedupe code out of the workflow JSON and runs it
// against a payload shaped like a real RemoteOK response. Guards the JD cap,
// the HTML strip, the relevance filter and the url dedupe.
import { readFileSync } from "node:fs";

const wf = JSON.parse(readFileSync("deploy/jbfind-daily-workflow.json", "utf8"));
const code = wf.nodes.find((n) => n.name === "Normalize + Dedupe").parameters.jsCode;

const bigHtml =
  "<div class='jdesc'><p>" +
  "We build mobile apps with Flutter and Kotlin. ".repeat(400) +
  "<script>alert(1)</script><p>Apply now</p></div>";

const $input = {
  all: () => [
    {
      json: {
        jobs: [
          { id: "rok1", title: "Senior Flutter Developer", company_name: "Acme Mobile",
            description: bigHtml, url: "https://remoteok.com/remote-jobs/rok1",
            source: "remoteok", date_posted: "2026-09-25", location: "Remote - India" },
          { id: "rok2", title: "Warehouse Picker", company_name: "NightOwl Logistics",
            description: "<p>Pick boxes.</p>", url: "https://remoteok.com/remote-jobs/rok2",
            source: "remoteok", date_posted: "2026-09-25", location: "Remote" },
          { id: "dup1", title: "Senior Flutter Developer", company_name: "Acme Mobile",
            description: "dup", url: "https://remoteok.com/remote-jobs/rok1", source: "remoteok" },
        ],
      },
    },
  ],
};

const results = new Function("$input", code)($input);
const flutter = results.find((j) => /Flutter/i.test(j.title));

const fail = (m) => {
  console.error("FAIL: " + m);
  process.exit(1);
};

console.log("kept:", results.length);
for (const j of results) {
  console.log(`  ${j.title} | jd=${(j.jd || "").length} chars | posted=${j.posted_date || "-"}`);
}
if (!flutter) fail("relevant mobile job was dropped");
if (flutter.jd.length !== 1800) fail(`expected an 1800-char cap, got ${flutter.jd.length}`);
if (/<[a-z]/i.test(flutter.jd)) fail("html not stripped");
if (/alert\(1\)/.test(flutter.jd)) fail("script tag leaked into the LLM prompt");
if (results.length !== 1) fail(`irrelevant job kept or url dedupe broken: ${results.length}`);
if (!flutter.posted_date) fail("posted_date lost");

console.log("\nPASS: 1800-char cap, html+script stripped, relevance filter, url dedupe, posted_date intact");

// ---------------------------------------------------------------------------
// Per-source field mapping. Every board names its fields differently and three
// of them send an epoch instead of a date; getting any of these wrong silently
// blanks posted_date or the source label.
// ---------------------------------------------------------------------------
const boards = {
  // jobspy: explicit source, ISO date
  LinkedIn: { jobs: [{ id: "li1", title: "Android Engineer (Kotlin)", company_name: "Infosys",
    location: "Hyderabad, India", description: "Kotlin, Jetpack, MVVM.",
    url: "https://in.linkedin.com/jobs/view/111", source: "linkedin", date_posted: "2026-09-25" }] },
  // naukri: source arrives as `site`
  Naukri: { jobs: [{ id: "nk1", title: "Senior Android Developer", company_name: "Wipro",
    location: "Bengaluru, India", description: "Android, Kotlin.",
    url: "https://www.naukri.com/jobapi/333", site: "naukri", date_posted: "2026-09-24" }] },
  // remotive: publication_date, not date_posted
  Remotive: { jobs: [{ title: "Flutter Developer", company_name: "Acme", url: "https://remotive.com/r/1",
    publication_date: "2026-09-23", description: "Flutter and Dart." }] },
  // arbeitnow: epoch SECONDS in created_at
  Arbeitnow: { data: [{ title: "iOS Developer", company_name: "Berlin Mobile", created_at: 1790000000,
    url: "https://www.arbeitnow.com/view/1", description: "Swift, iOS." }] },
  // jobicy: jobTitle/companyName/jobGeo + epoch MILLISECONDS in pubDate
  Jobicy: { jobs: [{ jobTitle: "React Native Developer", companyName: "Globex",
    jobGeo: "Remote - worldwide", jobDescription: "React Native, mobile app.",
    url: "https://jobicy.com/j/1", pubDate: 1790000000000 }] },
  // himalayas: no url field at all, and locationRestrictions is an array
  Himalayas: { jobs: [{ title: "Android Engineer", companyName: "Initech",
    applicationLink: "https://himalayas.app/companies/initech/jobs/android",
    pubDate: 1790000000, locationRestrictions: ["India", "Remote"],
    description: "Android, Kotlin." }] },
};

const mapped = new Function("$input", code)({
  all: () => Object.entries(boards).map(([, v]) => ({ json: v })),
});
const one = (host) => mapped.find((j) => j.source === host || j.job_url.includes(host));

console.log("\nper-source mapping:");
for (const j of mapped) console.log(`  ${j.source.padEnd(14)} posted=${j.posted_date || "MISSING"} | ${j.title.slice(0, 34)}`);

if (mapped.some((j) => j.source === "scraped")) fail("a board's source label fell back to 'scraped'");
for (const j of mapped) {
  if (!j.posted_date) fail(`${j.source}: posted_date not parsed from its own date field`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(j.posted_date)) fail(`${j.source}: bad date format ${j.posted_date}`);
  if (!j.jd) fail(`${j.source}: description not mapped`);
  if (!j.job_url) fail(`${j.source}: no job_url`);
}
if (!mapped.some((j) => j.source === "naukri")) fail("naukri dropped");
if (!mapped.some((j) => /himalayas/.test(j.job_url))) fail("himalayas applicationLink not used as job_url");
if (!mapped.some((j) => j.location.includes("India"))) fail("himalayas locationRestrictions array not joined");

// epoch seconds and epoch millis must land on the same calendar day
const arbeitnow = mapped.find((j) => /arbeitnow/.test(j.job_url));
const jobicy = mapped.find((j) => /jobicy/.test(j.job_url));
if (arbeitnow.posted_date !== jobicy.posted_date) {
  fail(`epoch s vs ms disagree: arbeitnow=${arbeitnow.posted_date} jobicy=${jobicy.posted_date}`);
}

// The LLM budget must be shared, not eaten by whichever board is biggest.
const crowded = { Big: { jobs: Array.from({ length: 40 }, (_, i) => ({
  title: `Flutter Developer ${i}`, company_name: `Co${i}`, description: "Flutter, Dart, mobile app.",
  url: `https://arbeitnow.com/view/${i}`, created_at: 1790000000 })) },
  Small: { jobs: [{ title: "Android Engineer", company_name: "Infosys", description: "Kotlin, Android.",
    url: "https://in.linkedin.com/jobs/view/9", source: "linkedin", date_posted: "2026-09-25" }] } };
const shared = new Function("$input", code)({ all: () => Object.entries(crowded).map(([, v]) => ({ json: v })) });
const bigSlots = shared.filter((j) => /arbeitnow/.test(j.job_url)).length;
console.log(`\nbudget split under pressure: arbeitnow=${bigSlots} linkedin=${shared.length - bigSlots}`);
if (!shared.some((j) => j.source === "linkedin")) fail("the big board starved the small one of LLM slots");
if (bigSlots >= shared.length) fail("round-robin is not sharing the budget");

console.log("\nPASS: per-source field mapping, epoch s/ms dates, url fallbacks, shared LLM budget");
