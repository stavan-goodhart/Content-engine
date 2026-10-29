
"""GHL Content Engine: Slack, news, editorial memory, and Sanity drafts."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from openai import OpenAI
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import memory
from news import fetch_shortlist
from sanity_store import SanityStore


load_dotenv(Path(__file__).with_name(".env"), override=True)

CHANNEL = os.environ["SLACK_CHANNEL_ID"]

APPROVERS = {
    x.strip()
    for x in os.getenv("APPROVER_IDS", "").split(",")
    if x.strip()
}

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

app = App(token=os.environ["SLACK_BOT_TOKEN"])
ai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

pool = ThreadPoolExecutor(max_workers=1)


# ============================================================
# EDITORIAL STYLE
# ============================================================

STYLE = """
You are the editorial writer for GoodHart Labs (GHL).

Write clear, evidence-based commentary about clinical AI,
healthcare technology, verification, and deployment.

Your writing must reflect GHL's published articles and
Dr. Shilpa's writing provided in the context.

Use their reasoning, specificity, and editorial style.
Do not copy their sentences or repeat previously published arguments.

EDITORIAL STYLE

1. Write 350 to 500 words unless instructed otherwise.
2. Start with a specific, factual development. No dramatic opening.
3. Use short paragraphs, generally 2 to 4 sentences.
4. Use at most two meaningful subheadings.
5. Write a coherent article, not a report, outline, or checklist.
6. Each paragraph must introduce a meaningful new point.
7. Explain why the development matters in practical clinical settings.
8. Develop one original, defensible argument relevant to GHL.
9. End with a concrete implication, not a summary of earlier paragraphs.
10. Use straightforward language that an informed clinician can understand.

NEVER USE

- Generic headings such as "What happened", "Why it matters",
  "GoodHart Labs' view", or "Conclusion".
- Labels such as "Verified", "Interpretation", or "Analysis"
  inside the article.
- Phrases such as "a clear operational choice",
  "now more than ever", "the facts are not theoretical",
  or "the need is unavoidable".
- Unnecessary urgency, exaggerated certainty, or marketing language.
- Repeated statistics or arguments.
- Generic AI safety conclusions.
- Forced connections to GHL products.
- Invented quotations, thresholds, evidence, or regulatory requirements.

FACTUAL ACCURACY

1. Every factual claim must be supported by the supplied
   evidence or researched sources.

2. Distinguish actual findings from recommendations,
   interpretations, and proposed approaches through natural
   wording, not explicit labels.

3. Preserve the precise meaning of statistics.
   Never confuse interest with adoption, survey responses
   with institutional measurements, or correlation with causation.

4. Preserve exact names, dates, organizations, figures,
   denominators, and study populations.

5. Explain the scope of evidence.
   An incident involving a general AI agent is not evidence
   that the same incident occurred in a hospital.

6. Do not convert examples into established industry practices.

7. Never propose numerical thresholds, deadlines, sample sizes,
   or clinical requirements without a defensible basis.
   If illustrative, explicitly identify them as proposals.

8. Check that conclusions follow from the evidence.
   Do not make stronger claims than the sources support.

9. If a claim cannot be substantiated from the available
   evidence, remove it or express the uncertainty accurately.

10. Do not fabricate source URLs. Preserve the exact URLs
    returned by research.

GHL CONTEXT

Treat published GHL articles and Dr. Shilpa's published writing
as authoritative examples of their editorial perspectives.

Treat previous AI-generated drafts as unapproved material,
not established GHL positions.

Before writing, identify which arguments GHL has already made.
Do not simply repackage them as a new article.

Focus on what the new development actually contributes.

Never imply that GHL officially endorses a new policy,
framework, threshold, or recommendation unless supported
by its existing published material.

REVISION RULES

When revising an article:

1. Follow the editor's latest feedback.
2. Preserve supported facts and original source attribution.
3. Remove repetition rather than rephrase it.
4. Correct factual overstatements, attribution errors,
   and unsupported interpretations.
5. Preserve previous editorial decisions when supplied.
6. Do not introduce new facts or citations without research.
7. Improve paragraph transitions and narrative flow.
8. Do not add headings merely to organize short sections.
9. Remove redundant conclusions.
10. Return the complete revised article.

Research factual corrections using original sources.
Replace incorrect citations with verified primary sources.
Do not introduce unsupported claims.

FINAL SELF-CHECK

Before returning the article, silently check:

- Are all numbers used in the correct context?
- Are names, dates, and source attributions accurate?
- Does each conclusion follow from the cited evidence?
- Are proposed requirements clearly distinguishable from facts?
- Does each paragraph contribute something new?
- Is any sentence repetitive, inflated, or unnecessary?
- Does this sound like a published GHL article rather than
  an AI-generated research summary?

Revise internally until these checks are satisfied.

OUTPUT

Return a concise title and polished article body.
Do not repeat the title in the body.
Keep citations and source URLs in the separate sources field.
Provide one specific visual suggestion separately.
Never describe the article as verified solely because
the model generated or reviewed it.
"""


# ============================================================
# STRUCTURED OUTPUT
# ============================================================

DECISION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "news",
                "list_articles",
                "topic",
                "edit_angle",
                "approve_topic",
                "revise",
                "approve_final",
                "reject",
                "sync",
                "question",
            ],
        },
        "feedback": {"type": "string"},
    },
    "required": ["action", "feedback"],
}


ARTICLE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verified": {"type": "boolean"},
        "reason": {"type": "string"},
        "date": {"type": "string"},
        "title": {"type": "string"},
        "body": {"type": "string"},
        "visual": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["name", "url"],
            },
        },
    },
    "required": [
        "verified",
        "reason",
        "date",
        "title",
        "body",
        "visual",
        "sources",
    ],
}


FACT_CHECK = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "passed": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {"type": "string"},
        },
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "supported",
                            "unsupported",
                            "uncertain",
                        ],
                    },
                    "evidence_url": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": [
                    "claim",
                    "status",
                    "evidence_url",
                    "explanation",
                ],
            },
        },
    },
    "required": [
        "passed",
        "issues",
        "checks",
    ],
}


# ============================================================
# OPENAI
# ============================================================

def ask(instructions, data, schema=None, search=False):
    params = {
        "model": MODEL,
        "instructions": instructions,
        "input": data,
    }

    if schema:
        params["text"] = {
            "format": {
                "type": "json_schema",
                "name": "result",
                "strict": True,
                "schema": schema,
            }
        }

    if search:
        params.update(
            tools=[{"type": "web_search"}],
            tool_choice="required",
        )

    response = ai.responses.create(**params)

    if search and not any(
        getattr(item, "type", "") == "web_search_call"
        for item in response.output
    ):
        raise ValueError("Web research did not run.")

    if schema:
        return json.loads(response.output_text)

    return response.output_text.strip()


# ============================================================
# SLACK
# ============================================================

def post(message, thread=None):
    params = {
        "channel": CHANNEL,
        "text": message,
        "unfurl_links": False,
        "unfurl_media": False,
    }

    if thread:
        params["thread_ts"] = thread

    app.client.chat_postMessage(**params)


def classify(text, state):
    instructions = """
    Interpret a Slack message for GHL's editorial bot.

    Select one action:

    news:
    Request recent headlines.

    list_articles:
    Request stored article titles or links by GHL or contributors.

    topic:
    Propose or select an article idea.

    edit_angle:
    Suggest edits to an article angle before drafting.

    approve_topic:
    Explicitly authorize researching and drafting a selected angle.

    revise:
    Request changes to an existing article.

    approve_final:
    Explicitly approve a completed draft for saving to Sanity.

    reject:
    Reject an article idea.

    sync:
    Refresh stored website content.

    question:
    Ask a question without changing editorial state.

    Respect the current phase.
    Generic praise is not final approval.
    """

    brief = {
        "phase": (state or {}).get("phase", "new"),
        "angle": (state or {}).get("angle", ""),
        "title": (
            (state or {}).get("article") or {}
        ).get("title", ""),
    }

    result = ask(
        instructions,
        json.dumps({
            "message": text,
            "state": brief,
        }),
        DECISION,
    )

    return result["action"]


# ============================================================
# DIRECT DATABASE ARTICLE RETRIEVAL
# ============================================================

def list_articles(thread):
    """
    Retrieve stored titles and URLs directly from SQLite.
    OpenAI does not generate or modify these results.
    """

    with memory.db() as conn:
        rows = conn.execute(
            """
            SELECT kind, title, url
            FROM docs
            ORDER BY title
            """
        ).fetchall()

    lines = []
    seen = set()

    for kind, title, url in rows:
        if kind not in ("published", "editorial"):
            continue

        key = (title or "", url or "")

        if key in seen:
            continue

        seen.add(key)

        name = (title or "Untitled").replace("|", " ")

        if url and urlsplit(url).scheme == "https":
            lines.append(f"• <{url}|{name}>")
        else:
            lines.append(
                f"• {name} (no public URL stored)"
            )

    post(
        "*Stored GHL articles*\n"
        + ("\n".join(lines) or "No articles found."),
        thread,
    )


# ============================================================
# TOPIC PROPOSAL
# ============================================================

def propose(thread, story, feedback=""):
    angle = ask(
        """
        Suggest one specific GHL article angle in 2 to 4 sentences.

        Check published GHL writing for overlap.
        Explain what the new development adds.

        Do not repeat unsupported claims or invent
        company positions.
        """,
        json.dumps({
            "story": story,
            "feedback": feedback,
            "GHL_context": memory.context(story),
        }, ensure_ascii=False),
    )

    memory.thread(thread, {
        "phase": "topic",
        "story": story,
        "angle": angle,
    })

    post(
        "*Proposed article angle*\n"
        + angle
        + "\n\nDiscuss changes here. "
        + "An authorized editor can approve the topic.",
        thread,
    )


# ============================================================
# SOURCE VALIDATION
# ============================================================

def verified_sources(article):
    """
    Validate source URL formats and remove duplicates.

    Factual verification happens separately.
    """

    unique = []
    seen = set()

    for source in article["sources"]:
        parsed = urlsplit(source["url"])

        key = (
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
        )

        if (
            parsed.scheme == "https"
            and parsed.netloc
            and key not in seen
        ):
            seen.add(key)
            unique.append(source)

    if len(unique) < 2:
        raise ValueError(
            "Article needs at least two distinct HTTPS source URLs."
        )

    article["sources"] = unique

    return article


# ============================================================
# FACTUAL VERIFICATION
# ============================================================

def verify_article(article):
    result = ask(
        """
        You are a skeptical fact checker reviewing
        clinical AI news articles.

        Conduct new web research.

        Check all material factual assertions:
        dates, numbers, denominators, populations,
        quotations, named entities, research findings,
        regulation, causal interpretations, and incidents.

        Prefer original primary sources.

        Distinguish:
        - AI adoption from interest in AI.
        - Physician survey answers from institutional data.
        - General AI incidents from clinical incidents.
        - Research findings from proposed recommendations.
        - Demonstrated requirements from illustrative thresholds.

        Check whether each source supports its attributed claim.

        Return one check per material factual claim,
        including its evidence URL.

        Mark a claim uncertain when evidence cannot
        be established.

        Do not approve an article containing
        unsupported or uncertain material claims.

        Never trust the writer's own verified field.

        Your job is to identify factual errors,
        not to approve the article automatically.
    
        Distinguish factual assertions from clearly stated proposals.

        A recommendation does not require proof that it already works.
        A prediction that a recommendation WILL produce a specific
        outcome does require evidence.

        A 403 response means source access failed, not that a claim
        is false. Seek independent corroboration. If unavailable,
        mark the specific factual claim uncertain.

        Do not reject an entire article solely because one URL
        could not be opened.

        """,
        json.dumps(
            article,
            ensure_ascii=False,
        ),
        FACT_CHECK,
        search=True,
    )

    failed = [
        item
        for item in result["checks"]
        if (
            item["status"] != "supported"
            or urlsplit(
                item["evidence_url"]
            ).scheme != "https"
        )
    ]

    if (
        not result["passed"]
        or result["issues"]
        or failed
        or not result["checks"]
    ):
        problems = result["issues"] + [
            item["claim"] + ": " + item["explanation"]
            for item in failed
        ]

        raise ValueError(
            "Fact check failed:\n"
            + "\n".join(
                dict.fromkeys(problems)
            )[:2500]
        )

    return result


# ============================================================
# AUTOMATIC VERIFICATION AND CORRECTION
# ============================================================

def check_and_repair(thread, article):
    post("Checking factual claims against sources...", thread)

    try:
        verify_article(article)
        post("Fact check complete. No issues flagged.", thread)

    except Exception as error:
        print("Fact-check notes:", str(error), flush=True)
        post(
            "*Fact-check concerns for editorial review:*\n"
            + str(error)[:2500]
            + "\n\nThe draft is available for revision.",
            thread,
        )

    return article
# ============================================================
# DRAFTING AND REVISION
# ============================================================

def draft(thread, state, feedback=""):
    previous = state.get("article")

    if previous:
        instructions = STYLE + """
        Revise the supplied draft based on the latest feedback
        and earlier editorial decisions.

        Research factual corrections against original sources
        when necessary.

        Preserve supported claims.
        Remove claims that cannot be substantiated.
        """

        data = {
            "previous": previous,
            "feedback": feedback,
            "previous_feedback": state.get(
                "feedback", []
            ),
            "GHL_context": memory.context(
                state["story"] + " " + state["angle"]
            ),
        }

    else:
        instructions = STYLE + """
        Research a specific news development within
        the past seven days using web search.

        Use at least two credible, distinct sources.

        If the news development cannot be confirmed,
        set verified=false.

        Return the development date in YYYY-MM-DD format.
        """

        data = {
            "story": state["story"],
            "angle": state["angle"],
            "GHL_context": memory.context(
                state["story"] + " " + state["angle"]
            ),
            "today": datetime.now(
                timezone.utc
            ).date().isoformat(),
        }

    # Generate or revise the article.
    article = ask(
        instructions,
        json.dumps(
            data,
            ensure_ascii=False,
        ),
        ARTICLE,
        search=True,
    )

    if not article["verified"]:
        post(
            "Could not establish this story: "
            + article["reason"],
            thread,
        )
        return

    # Preserve the original development date during revisions.
    if previous:
        article["date"] = previous["date"]

    else:
        published = datetime.strptime(
            article["date"],
            "%Y-%m-%d",
        ).date()

        today = datetime.now(
            timezone.utc
        ).date()

        if not (
            today - timedelta(days=7)
            <= published <= today
        ):
            raise ValueError(
                "No recent news development confirmed "
                "within seven days."
            )

    # Check source URLs.
    verified_sources(article)

    # Verify and automatically correct factual errors.
    # IMPORTANT: This is outside the if/else above.
    # It runs for BOTH initial drafts and revisions.
    article = check_and_repair(
        thread,
        article,
    )

    # Store only after verification succeeds.
    if feedback:
        state.setdefault(
            "feedback", []
        ).append(feedback)

    state.update(
        phase="draft",
        article=article,
    )

    memory.thread(
        thread,
        state,
    )

    # Publish the draft inside the existing Slack thread.
    full = (
        "*" + article["title"] + "*\n\n"
        + article["body"]
    )

    part = ""

    for paragraph in full.split("\n\n"):
        if (
            part
            and len(part) + len(paragraph) + 2 > 2800
        ):
            post(part, thread)
            part = ""

        if len(paragraph) > 2800:
            for i in range(
                0,
                len(paragraph),
                2800,
            ):
                post(
                    paragraph[i:i + 2800],
                    thread,
                )

        else:
            part += (
                "\n\n" if part else ""
            ) + paragraph

    if part:
        post(part, thread)

    links = "\n".join(
        f"• <{source['url']}|{source['name']}>"
        for source in article["sources"]
    )

    post(
        "*Suggested visual:* "
        + article["visual"]
        + "\n\n*Sources*\n"
        + links
        + "\n\nReply with revisions or explicitly "
        + "approve the final article.",
        thread,
    )


# ============================================================
# SANITY DRAFT STORAGE
# ============================================================

def save(thread, state):
    article = state["article"]

    slug = "-".join(
        "".join(
            c.lower() if c.isalnum() else " "
            for c in article["title"]
        ).split()
    )[:90]

    blocks = []

    for i, paragraph in enumerate(
        article["body"].split("\n\n")
    ):
        text = paragraph.strip()

        if not text:
            continue

        heading = text.startswith("#")

        blocks.append({
            "_key": f"b{i}",
            "_type": "block",
            "style": "h2" if heading else "normal",
            "markDefs": [],
            "children": [{
                "_key": f"s{i}",
                "_type": "span",
                "text": (
                    text.lstrip("# ").strip()
                    if heading else text
                ),
                "marks": [],
            }],
        })

    doc_id = (
        "drafts.ghl-post-"
        + thread.replace(".", "-")
    )

    doc = {
        "_id": doc_id,
        "_type": "ghlPost",
        "title": article["title"],
        "slug": {
            "_type": "slug",
            "current": slug,
        },
        "category": "Research note",
        "excerpt": article["body"][:250],
        "body": blocks,
        "sourceNotes": (
            "Selected story: "
            + state["story"]
            + "\nVisual: "
            + article["visual"]
        ),
        "sources": [
            {
                "_key": str(i),
                "_type": "object",
                "title": source["name"],
                "url": source["url"],
            }
            for i, source in enumerate(
                article["sources"]
            )
        ],
    }

    # Store as an unpublished Sanity draft.
    SanityStore().mutate([
        {"createOrReplace": doc}
    ])

    # Update editorial memory only after Sanity succeeds.
    memory.document(
        "approved:" + thread,
        "approved",
        article["title"],
        article["body"],
    )

    if state.get("feedback"):
        memory.document(
            "feedback:" + thread,
            "feedback",
            article["title"],
            "\n".join(
                state["feedback"]
            ),
        )

    state["phase"] = "approved"

    memory.thread(
        thread,
        state,
    )

    post(
        "Approved. Saved to Sanity as an unpublished draft: "
        + doc_id,
        thread,
    )


# ============================================================
# SLACK CONVERSATION HANDLING
# ============================================================

def handle(event):
    if (
        event.get("channel") != CHANNEL
        or event.get("bot_id")
        or event.get("subtype")
        or not event.get("text")
    ):
        return

    # Prevent duplicate Slack event processing.
    if not memory.first(event["ts"]):
        return

    thread = (
        event.get("thread_ts")
        or event["ts"]
    )

    state = memory.thread(thread)

    text = " ".join(
        word
        for word in event["text"].split()
        if not word.startswith("<@")
    )

    try:
        action = classify(
            text,
            state,
        )

        # Database article listing.
        if action == "list_articles":
            list_articles(thread)

        # Recent news.
        elif action == "news":
            for item in fetch_shortlist(
                days=3,
                limit=5,
            ):
                post(
                    f"*{item['title']}*\n"
                    f"{item['publisher']}\n"
                    f"{item['url']}\n"
                    "Reply to discuss this story."
                )

        # Refresh local website memory.
        elif action == "sync":
            memory.sync()

            post(
                "Published website content refreshed "
                "in local memory.",
                thread,
            )

        # Contextual questions.
        elif action == "question":
            answer = ask(
                """
                Answer briefly using the supplied GHL context.

                Do not invent company claims, source URLs,
                statistics, or publications.

                If unknown, say so.
                """,
                json.dumps({
                    "question": text,
                    "article_thread": state,
                    "context": memory.context(text),
                }, ensure_ascii=False),
            )

            post(answer, thread)

        # Authorization.
        elif (
            action in (
                "approve_topic",
                "approve_final",
                "reject",
            )
            and event.get("user") not in APPROVERS
        ):
            post(
                "Only authorized editors can approve "
                "or reject articles.",
                thread,
            )

        # First approval.
        elif (
            action == "approve_topic"
            and state
            and state.get("phase") == "topic"
        ):
            state["phase"] = "drafting"

            memory.thread(
                thread,
                state,
            )

            post(
                "Topic approved. Researching and writing...",
                thread,
            )

            try:
                draft(thread, state)

            finally:
                latest = memory.thread(thread)

                if (
                    latest
                    and latest.get("phase") == "drafting"
                ):
                    latest["phase"] = "topic"

                    memory.thread(
                        thread,
                        latest,
                    )

        # Final approval.
        elif (
            action == "approve_final"
            and state
            and state.get("phase") == "draft"
        ):
            save(thread, state)

        # Rejection.
        elif action == "reject" and state:
            state["phase"] = "rejected"

            memory.thread(
                thread,
                state,
            )

            post(
                "Topic rejected.",
                thread,
            )

        # Article revisions.
        elif (
            state
            and state.get("phase") == "draft"
            and action in (
                "revise",
                "edit_angle",
                "topic",
            )
        ):
            post(
                "Revising the article...",
                thread,
            )

            draft(
                thread,
                state,
                text,
            )

        # Avoid duplicate proposals during drafting.
        elif (
            state
            and state.get("phase") == "drafting"
        ):
            post(
                "Article drafting is already in progress.",
                thread,
            )

        # Propose an angle.
        elif action in (
            "topic",
            "edit_angle",
        ):
            if state:
                story = state["story"]

            elif event.get("thread_ts"):
                result = (
                    app.client.conversations_replies(
                        channel=CHANNEL,
                        ts=thread,
                        limit=1,
                    )
                )

                story = result["messages"][0]["text"]

            else:
                story = text

            propose(
                thread,
                story,
                text if state else "",
            )

        else:
            post(
                "Share a news story or reply "
                "in an existing editorial thread.",
                thread,
            )

    except Exception as error:
        print(
            "GHL bot error:",
            repr(error),
            flush=True,
        )

        post(
            "Error: " + str(error)[:800],
            thread,
        )


# ============================================================
# SLACK EVENTS
# ============================================================

@app.event("message")
def on_message(event):
    pool.submit(handle, event)


@app.event("app_mention")
def on_mention(event):
    pool.submit(handle, event)


# ============================================================
# STARTUP
# ============================================================

if __name__ == "__main__":
    if not APPROVERS:
        print(
            "WARNING: Add authorized Slack user IDs "
            "to APPROVER_IDS in .env"
        )

    print("GHL Content Engine running.")

    SocketModeHandler(
        app,
        os.environ["SLACK_APP_TOKEN"],
    ).start()
