# @genidtals/client

Zero-dependency TypeScript client for the GenIDtals REST API. It works on
Node.js 18+, Deno, Bun and browsers (through your backend).

```ts
import { GenIDtalsClient, GenIDtalsError } from "@genidtals/client";

const client = new GenIDtalsClient({ baseUrl: "https://biometrics.internal", apiKey: process.env.GENIDTALS_API_KEY! });

await client.enroll("alice", [ref1, ref2]);            // Uint8Array | ArrayBuffer | Blob | base64 string
const result = await client.verify("alice", probe);    // { authenticated, reason, message, quality, ... }

// Stateless: store the sealed template in your own database
const template = await client.createTemplate("u_123", [ref1, ref2]);
await client.verifyTemplate("u_123", template, probe);
```

Errors throw `GenIDtalsError` with `.status` (HTTP code) and `.code`
(`unauthorized`, `already_enrolled`, `low_quality`, `invalid_template`, ...).
A failed verification is not an error: check `result.authenticated` and
`result.reason`.

```bash
npm install && npm run build        # emits dist/
npm test                            # e2e; needs a running server (see test/e2e.test.ts)
```
