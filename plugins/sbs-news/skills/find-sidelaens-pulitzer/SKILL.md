---
name: find-sidelaens-pulitzer
description: "Find Danish news articles from the current or previous calendar week that could qualify for Langt fra løgnen's 'sidelæns Pulitzer', select the strongest one, and draft a casual, article-specific listener email while offering linked alternatives. Use for candidate searches, monitoring, or submission drafts. Do not use for general news summaries or intentionally satirical articles."
---

# Find sidelæns Pulitzer

Find genuine journalism whose comedy is unintended. The target is not a particular subject but a mismatch between the story's triviality and the journalistic treatment it receives. Recommend the journalism, not merely an event that gives the listener an opportunity to write a joke.

## Search scope

- Search the live web; these results must be current and source-backed.
- Only accept Danish-language articles published during the current or immediately preceding calendar week in the `Europe/Copenhagen` timezone. Treat weeks as Monday through Sunday. At runtime, calculate and state the exact eligible date range, beginning on Monday of the previous week and ending today.
- The publication date must be verifiable on the original publisher's page or reliable page metadata. An old article does not become eligible solely because of a minor update, a new search snippet date, or republication without materially new reporting. Exclude undated articles.
- Search national, regional, local, trade, broadcaster, and liveblog sources. Include paywalled results only when the headline, date, and relevant premise can still be verified.
- Before searching, read [references/sources.md](references/sources.md) for useful Danish starting points. Treat it as a seed list, never a whitelist or exhaustive checklist. Search other Danish sites whenever broader queries surface promising material.
- Combine broad web queries with direct inspection of publishers' front pages, latest-news indexes, relevant sections, liveblogs, and RSS feeds when available. Search rankings tend to suppress the trivial stories this task is trying to find, so search results alone are not sufficient.
- Before selecting a winner, sample current material from established national media, regional/local media, and at least one low-calorie or gossip source. Prioritize editorial mismatch over source type.
- Vary sources and queries. Do not merely repeat search-engine snippets; open each serious candidate and verify the original page.
- Prefer the original publisher over aggregators or social reposts. Record the exact headline, publisher, publication date, and direct URL.

Useful patterns include:

- A headline that states something self-evident or tautological.
- A tiny everyday occurrence treated as breaking news, a live event, an investigation, or a major public-service intervention.
- Reporting that contains almost no information despite conspicuous journalistic effort.
- An unintentionally funny headline, image-caption pairing, quote, or framing.
- A local or bureaucratic non-event presented with disproportionate drama.

The canonical example is TV 2's headline "Smeltet sne giver vand i gaderne i København", supported by a live reporter visiting a large puddle.

## Verification and judgment

Exclude fabricated stories, intentional satire, parody, advertorials disguised as journalism, and pages whose wording cannot be verified. Automated aggregators, scraper sites, generic AI-content farms, press-release mirrors, and pure republication sites may supply leads but cannot be final candidates; trace the story to original editorial reporting. Do not nominate a story merely because the underlying event is strange; the journalism or framing itself should create the sideways-Pulitzer quality.

### Apply the newsroom-origin test

A primary recommendation must contain a specific, verifiable journalistic choice that is funny in itself: for example the headline's wording, an absurdly grave framing, a reporter dispatched to document a non-event, needless expert consultation, excessive data or graphics, a revealing caption, or conspicuous reporting effort applied to almost nothing.

Before ranking a candidate, identify that exact choice and then remove the commentary, analogies, puns, and comic framing that Codex or the listener would add in the email. Ask: **Would the original article still be an obvious sideways-Pulitzer candidate if it were submitted with only its headline, link, and one factual sentence describing what the newsroom did?** If not, reject it or mark it as a weak alternative.

Apply these distinctions strictly:

- A situation that is relatable, odd, provincial, luxurious, or easy to mock is not enough. The comedy must implicate the newsroom's news judgment, wording, sourcing, or production choice.
- A good joke written by the listener does not make the source article a good candidate. If the email supplies most of the humor, the recommendation is too weak.
- Ordinary service journalism about a real inconvenience, safety issue, transport problem, school condition, or infrastructure failure is not inherently sideways, even when the headline sounds mundane. Require additional disproportion or self-evident framing in the journalism itself.
- Substantial reporting can be disproportionate and funny, but it can also show that the subject has genuine public importance. Do not treat maps, interviews, statistics, or an on-location reporter as comic merely because they exist; explain why they are excessive relative to what the article actually establishes.
- Gossip's subject matter is routinely trivial, so triviality alone has little weight there. Require an especially empty premise, accidental wording, or comically excessive treatment.
- Deliberately playful headlines and knowingly comic narration are weaker than accidental newsroom solemnity. Penalize or exclude them rather than presenting the outlet's intentional joke as an unintended one.

When uncertain, prefer the candidate whose comic flaw can be quoted or neutrally described most precisely. Do not choose a merely defensible candidate just because it supports a clever email draft.

Avoid candidates whose humor primarily depends on mocking victims, disability, illness, poverty, death, or private individuals in distress. A funny institutional framing can still qualify when the joke clearly lands on the newsroom, authority, or production choice.

Score plausible candidates internally:

- Banality or self-evidence: 0–5
- Disproportionate journalistic treatment: 0–5
- Unintentional comedy: 0–5
- Newsroom-origin strength: 0–5
- Verifiability: 0–3
- Freshness: 0–2

Subtract up to 5 points for ambiguity, deliberate humor, recycled material, or needless cruelty. Subtract a further 5 points when the proposed email's analogy or punchline supplies the main comedy. A primary recommendation should normally score at least 4 for newsroom-origin strength; if none does, say that no convincing candidate was found. Treat the score as a comparison aid, not objective truth. Prefer a sharply defensible candidate over a merely odd headline.

## Deliverable

Choose the strongest verified article as the primary recommendation. Return:

- Its exact headline, publisher, publication date, and direct link.
- A brief explanation of why it qualifies.
- The recipient address: `langtfra@radio4.dk`.
- A ready-to-copy Danish email with an article-specific subject line when a subject would be useful.

After the primary recommendation and email, include up to four credible alternatives. For each alternative give the exact headline, publisher, publication date, direct link, and one concise sentence explaining its potential. Keep this section easy to scan and do not draft emails for the alternatives unless the user asks. If fewer than four alternatives are genuinely good, return fewer.

Every primary and alternative article must fall within the eligible current-or-previous-week date range. Never pad the results with older material.

When the user later selects an alternative, draft a fresh email specifically for that article. Re-open the source first if its relevant details were not already verified or may have changed.

The email must sound like an actual listener casually sending in a funny find. Keep it short and natural. Point out the precise detail that makes this particular article funny or relevant, but do not analyze the joke to death. The draft may sharpen comedy already present in the journalism, but it must not rescue a weak candidate with an unrelated analogy, an invented premise, or a punchline that is funnier than the source. Vary the opening, phrasing, rhythm, and sign-off according to the article; do not use a fixed template, stock boilerplate, placeholders, marketing language, or a formal pitch voice. It should feel written for this one article rather than generated from a reusable form.

Do not include the internal score in the email. Clearly distinguish verified facts from inference outside the draft. If no convincing candidate exists, say so instead of forcing a submission.

Do not send email or contact the podcast unless the user explicitly asks for that separate action.
