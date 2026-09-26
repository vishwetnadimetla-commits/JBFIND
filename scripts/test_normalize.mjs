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
