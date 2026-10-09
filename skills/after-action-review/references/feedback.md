# Optional feedback and required decisions

Read when asking a completion survey or handling its reply.

Ask once per finished/blocked user task, after delivering the result. Prefer
`request_user_input_async` with one question when available; otherwise put one
optional question in the final response. Do not delay delivery for feedback.

Russian default: «Результат и процесс вас устроили?»
Options: «Да, всё хорошо», «Есть замечания», «Пропустить».
For checkpoints ask only for a needed preference. After a complaint acknowledge
its substance and address it without asking the user to repeat it.

Record real replies using `aar_feedback(review_id, rating, comment)` with good,
issue or skip. Distinguish a survey reply from a new task; do not label every next
message feedback. Respect requests to disable surveys using configure. Unanswered
surveys stay pending; never invent a rating or treat silence as approval.

A survey rating cannot approve a queued change. Ask a separate concrete decision
after staging a serious/disputed proposal. Leave it pending while doing independent
authorized work. Explain the material risk in plain language.

Do not create a survey about its own survey, an AAR about journal writes, or a new
goal to acknowledge feedback. Native stop_hook_active plus the durable one-pass
budget bound recursion, including after compaction.
