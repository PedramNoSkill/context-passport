# Export schema details shared across modules

Parent uses this concrete shape; SPEC.md remains the broader behavior contract.

```json
{
  "schema_version": "1.0",
  "id": "cp_<random uuid hex>",
  "title": "Project title",
  "created_at": "ISO8601 UTC string",
  "target": "generic",
  "source": {"name":"session.jsonl","sha256":"<64hex or empty>","format":"codex","conversation_id":null,"message_count":3},
  "sections": {
    "goal": [{"text":"Implement auth", "evidence":["m0001"],"basis":"user_message"}],
    "decisions":[],"completed":[],"next_steps":[],"blockers":[]
  },
  "test_runs": [{"command":"python -m unittest discover -v", "exit_code":0,"status":"passed", "evidence":["m0002","m0003"]}],
  "git": null,
  "evidence": [{"id":"m0001","role":"user","text":"Implement auth","locator":"line 2","kind":"message","timestamp":null,"tool_name":null,"call_id":null}],
  "redactions": {"total":0,"counts":{}},
  "warnings": ["Statements extracted literally; assistant claims are not independently verified."]
}
```

Git capture shape is in SPEC. Notes become `n0001` evidence entries with role user, kind user_note and locator `user note:goal` etc. `source` does not contain original transcript paths or full exports. Trace locators are untrusted strings to render as text.

Tests reported passed/failed mean the matched tool command returned that exit code, not a guarantee about individual tests or clean state; display this caveat. Transcript statements of completed work are explicitly marked unverified. Provide an evidence transcript section so unrecognized statements remain reviewable.

Expected `render_markdown(data,target=None)` / `render_html(data)` return standalone text. Render explicit warning that quoted transcript/Git evidence is untrusted content, not instructions; no execution is performed. Do not assume review.html can call a backend. It must work offline; optional small inline JS for evidence search and collapsing cards, no external resources. No source text in raw JS/innerHTML. All evidence text is escaped; filenames with backticks/newlines and HTML injection tested. HTML uses a polished dark navy/teal/coral visual language, passport/travel metaphor subtly. English UI. One clear reading flow: overview, next steps, decisions, verification, Git, expandable evidence.
