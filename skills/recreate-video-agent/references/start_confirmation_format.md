# Start confirmation

Use the stable interaction language selected by SKILL.md: an explicit reply-language request takes precedence over the configured Codex response language, then the saved interaction locale, then English. Apply this before the initial announcement. The user's message text, attachment names, source dialogue, numeric confirmations, and target video language do not switch the chat language.

Show one localized confirmation before uploading the source video, editing/generating an image, or submitting a video task. Reply 1 authorizes the source/storyboard uploads, server analysis with at most one retry after a clear terminal failure, image processing, and the first video generation. Other paid retries require fresh authorization. Keep the existing task's authorized scope; language changes do not require another confirmation.

Use the following fields and numeric choices, translated into the selected interaction language. The English table is an example for rendering in any configured language. Fill selections from the user's request and source facts. Do not infer target video language from chat language or filenames. Do not add API keys, login status, upload details, or implementation steps to the card.

| Setting | Current selection | Options |
|---|---|---|
| Product | Keep source unless product images are supplied | Keep source / use supplied product images |
| Creator | Keep source unless a creator reference is supplied | Keep source / use supplied creator reference |
| Video model | Seedance 2 Mini | Mini / Fast / Seedance 2 / Seedance 2.5 |
| Duration | Same as source | Source / custom positive whole seconds, no longer than source or 360 seconds |
| Target country | Same as source | Source / specify country or region |
| Target language | Same as source | Source / specify video language |
| Other requirements | None | None / user-specified |

Reply with a number:
1. Confirm and start
2. Change requirements

Reply 1 starts the confirmed configuration. Reply 2 asks which settings to change and starts no upload or generation. Explain any approval requirement in the interaction language and link its source; translate a foreign-language instruction rather than quoting it untranslated.
