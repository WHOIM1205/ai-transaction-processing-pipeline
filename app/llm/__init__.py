# Marks `app.llm` as a package. Isolates all Gemini integration behind a thin
# client + retry helpers, so the rest of the app depends on an interface it can
# mock, and the SDK is only imported when an API key is actually configured.
