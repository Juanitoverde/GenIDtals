// End-to-end test against a running server:
//   GENIDTALS_URL=http://127.0.0.1:8080 GENIDTALS_API_KEY=... GENIDTALS_SAMPLES=dir npm test
// where dir holds a_1.png..a_3.png (one subject) and b_1.png (another).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { GenIDtalsClient, GenIDtalsError } from "../src/index.ts";

const baseUrl = process.env.GENIDTALS_URL ?? "http://127.0.0.1:8080";
const apiKey = process.env.GENIDTALS_API_KEY ?? "";
const samples = process.env.GENIDTALS_SAMPLES ?? "samples";
const img = (name: string) => new Uint8Array(readFileSync(join(samples, name)));

const client = new GenIDtalsClient({ baseUrl, apiKey });

assert.equal((await client.health()).status, "ok");

const user = "ts-user@example.com";
await client.deleteUser(user);
const enrolled = await client.enroll(user, [img("a_1.png"), img("a_2.png")]);
assert.equal(enrolled.references, 2);

const ok = await client.verify(user, img("a_3.png"));
assert.equal(ok.authenticated, true);
assert.equal(ok.reason, "ok");
assert.equal(ok.confidence, null);

const bad = await client.verify(user, img("b_1.png"));
assert.equal(bad.authenticated, false);
assert.equal(bad.reason, "no_match");

const template = await client.createTemplate("ts-stateless", [img("a_1.png"), img("a_2.png")]);
assert.equal((await client.verifyTemplate("ts-stateless", template, img("a_3.png"))).authenticated, true);

await assert.rejects(client.enroll(user, [img("a_1.png"), img("a_2.png")]),
  (e: unknown) => e instanceof GenIDtalsError && e.status === 409 && e.code === "already_enrolled");
await assert.rejects(new GenIDtalsClient({ baseUrl, apiKey: "wrong" }).status(user),
  (e: unknown) => e instanceof GenIDtalsError && e.status === 401);
assert.throws(() => new GenIDtalsClient({ baseUrl: "http://example.com", apiKey }));

assert.equal((await client.deleteUser(user)).deleted, true);
console.log("typescript client e2e: all checks passed");
