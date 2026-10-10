# Prompt display and export

The data source is the registered segments in analysis/local-video-prompts.json. Never reconstruct stored prompts from chat text. Use SKILL.md's stable interaction language for every heading, title, metadata value, link label, status, and explanation; translate display titles without modifying stored data.

## Export first

After registering prompts, export each exact prompt with:

```text
python3 scripts/export_prompt_texts.py --manifest <manifest> --output-dir <task>/prompts
```

Use the returned absolute paths for backup links. Keep all prompts complete and unchanged in the files, including target-language dialogue and identifiers. Exports do not add an approval gate.

## Choose the display path

When prompt language matches interaction language, display every registered segment in order, followed by its full prompt in a separate text code block. Do not summarize, truncate, fold, or rewrite the prompt. Include only the exact prompt body inside the block. If the body contains triple backticks, use a longer outer fence.

When prompt language differs from interaction language, default to a localized overview of all segments, their durations/source windows, a localized summary of each prompt, an explicit target-language label, and each unchanged text-file link. Do not automatically paste foreign-language prompt text into the chat. If the user explicitly requests complete inline prompts, show them unchanged with a clear target-language label and keep all surrounding explanation in the interaction language. This does not change targetLanguage or translate generation data.

## Full inline example

Translate this example's headings, labels, segment titles, and status text to the interaction language. Missing metadata is displayed as the localized equivalent of "Not provided"; do not guess values.

````markdown
Video recreation prompts

Generated <count> segment prompts. The code blocks can be copied directly; text files are backups.

| Segment | Title | Duration | Source time window |
|---|---|---:|---:|
| 01 | <localized title> | <duration>s | <globalStart>–<globalEnd>s |

Segment 01 | <localized title>

<duration>s · Source <globalStart>–<globalEnd>s · Storyboard <storyboardIds>

```text
<exact registered prompt body>
```

[Open Segment 01 text backup](</absolute/path/segment-01-prompt.txt>)
````

After displaying all segments through the applicable path, state the actual next status once and continue authorized generation. Do not ask for a new confirmation. If the required video CLI is unavailable, explain the blocker in the interaction language, preserve all artifacts, and list the required reference images and prompt backups. Do not switch providers automatically.
