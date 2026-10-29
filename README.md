# GHL Content Eng — one-day MVP

## What is implemented

**Google News RSS + team messages → `#content-eng` → initial Slack approval → OpenAI web research and article draft → Slack full-text review and revisions → final Slack approval → Sanity → LinkedIn + X → Substack publication handoff.**

- Reuses the *concept and stdlib approach* of Hawk's RSS collector, with GHL-specific queries. Does not need Hawk's database, production workflow, or Muse service.
- Uses Sanity as the state database **and** content database. All work-in-progress docs get the `drafts.` prefix, so an unauthenticated production Sanity query cannot read them.
- Approved articles become `ghlPost` documents in Sanity. The GHL website must be modified to fetch/render them.
- Two Slack approvals. Topic rejection, full-text Slack preview, feedback in thread, regeneration, and final approval.
- Human-approved publication only. No unverified article can be published directly by an AI.
- A durable per-channel status shows failures, and retries skip successfully posted LinkedIn/X content.
- **Substack does not have a verified official write API for full posts**, so this MVP hands off the approved copy for manual publication in the account, then records the final URL. Do NOT represent this as automated Substack publishing.
- **Monitoring**: editorial and audience observations replied in a published article thread are stored in Sanity and included in later drafts. Automatic analytics ingestion is not implemented.
- `DRY_RUN=true` by default prevents unintended external publication.

## Setup: Slack (10-minute configuration, once you have access)

1. Create a Slack app under <https://api.slack.com/apps>, enable **Socket Mode**. Generate `xapp-...` with `connections:write`.
2. Install to the workspace with bot OAuth scopes: `chat:write`, `channels:history`, `channels:read` (or `groups:history` for a private channel). Subscribe to bot event `message.channels` (or `message.groups`). Socket Mode includes interactivity.
3. Invite bot to `#content-eng`. Copy the channel ID (`C...`) and bot OAuth token (`xoxb...`).
4. For tight approval permissions, set `APPROVER_IDS` to comma-separated Slack member IDs. If empty, anyone with channel access can approve.

## Setup: Sanity (project already exists)

1. Copy `studio/schemas/ghlPost.ts` and `studio/schemas/contentItem.ts` into the existing Sanity Studio. Add them to the Studio config `schema.types` array; if there is no Studio yet, use `npm create sanity@latest` to scaffold one and then register the two schema types. See `studio/sanity.config.example.ts`.
2. Run `npx sanity@latest deploy` from the configured Studio folder. Sanity hosts the **Studio UI**. This process does not host the automation worker.
3. Under Sanity project settings, generate a scoped editor/write API token for this worker. Enter it only in your private `.env` or hosting secrets, not in Slack, chat, Git, or the Studio frontend.
4. The published article documents use `_type: ghlPost`. Have the private GHL website repo fetch/render `ghlPost` on `/blog` and `/blog/[slug]` via `site-integration/get-ghl-posts.ts`. **Without this step, posts live in Sanity but do not appear on ghartlabs.com.**

## Setup: Worker

```bash
cd ghl-content-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill private settings in .env
python app.py --demo                   # no network, credentials, or publication
python app.py --run-news               # send today's Slack shortlist once
python app.py                          # start Socket Mode interactive workflow + daily scheduler
```

Persistent runtime: use an existing GHL worker host or a small always-on container. `docker build -t ghl-content-eng .` then run the image with private environment variables. Socket Mode makes inbound ports unnecessary. Keep exactly one persistent worker instance to avoid duplicate daily schedulers.

The `--run-news --force` command allows resending the day's shortlist. Topics may also be created by posting a normal message/URL in `#content-eng`. Reply inside its thread for editorial feedback.

## Environment variables

See `.env.example`. Required: Slack bot/app tokens and channel ID; Sanity project/dataset/write token; OpenAI key. OpenAI's Responses web-search tool powers research and drafting.

For full publishing: `DRY_RUN=false`, LinkedIn OAuth token + organization ID with suitable organization write permission, X API OAuth1 credentials with posting access, and a Substack account authorized to publish.

**Validation policy:** Models can still hallucinate sources or misunderstand them. URLs must be publicly resolvable (or have a reachable paywall) and at least two source URLs must be supplied. This check is *not* fact verification. A reviewer is accountable for checking factual claims and citations before clicking the final publishing button.

## Platform publishing state

| Destination | Implementation | Remaining GHL action |
|---|---|---|
| Sanity | API automated after second approval | Create schema and token |
| Website | `ghlPost` document ready | Wire website to Sanity and deploy one change |
| LinkedIn | `POST /rest/posts` integration | Connect app token and org permissions |
| X | `POST /2/tweets` integration | Connect developer app + user token |
| Substack | Approved article prepared | Publish in Substack editor and reply with URL in Slack |

## Security and operational limits

- Source URLs are checked for public HTTPS endpoints and private/local IP hosts are disallowed. Redirects aren't followed during source checks.
- Slack buttons check `APPROVER_IDS` when configured; never put API tokens into channel messages.
- Sanity `drafts.*` documents are private to authenticated client queries. Published documents can be publicly queried according to dataset permissions.
- The V1 reviewer must verify sources and legal/clinical claims before publishing.
- Production workloads should use a queue, idempotency locks, and webhooks; V1 prioritizes a single worker and avoids parallel deployments.
- Slack channel thread replies trigger revisions automatically while the article is awaiting review. Post `substack https://...` after publishing to record the newsletter URL.
- No active GHL tokens are included. API integration is not end-to-end verified without workspace credentials and website repository access.

## References

- Slack Socket Mode: https://docs.slack.dev/apis/events-api/using-socket-mode/
- Sanity hosting: https://www.sanity.io/docs/studio/deployment
- Sanity draft documents: https://www.sanity.io/docs/content-lake/drafts-and-versions
- LinkedIn Posts API: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
- Substack full post editor: https://support.substack.com/hc/en-us/articles/360037831771-How-do-I-publish-a-new-post-on-Substack
- GHL writing style: https://ghartlabs.com/blog/benchmarks-ceiling-deployment-floor
